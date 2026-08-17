"""Ocean monitoring buoy main daemon."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from .comms.broadcast import SensorBroadcastService
from .comms.heartbeat import HeartbeatService
from .comms.radio import DataRequestService, IntervalControlService, OTAService, Radio
from .comms.textquery import TextQueryService
from .data import DataStore
from .sensors import Reading
from .sensors.depth_temp import DepthTempSensor
from .sensors.dissolved_oxygen import DissolvedOxygenSensor, MCP3008Reader
from .sensors.imu import IMUSensor
from .sensors.leak import LeakSensor
from .sensors.salinity import SalinitySensor
from .sensors.usb_depth_temp import UsbDepthTempSensor
from .utils import configure_logging, load_config
from .utils.power import PowerMonitor
from .utils.system_status import clock_looks_sane, read_disk_info, read_file_size_mb, read_memory_info
from .utils.watchdog import SystemdWatchdogNotifier

MIN_SAMPLE_INTERVAL_SECONDS = 1.0
MAX_SAMPLE_INTERVAL_SECONDS = 86400.0


def _import_hardware_modules():
    """Import hardware libraries. They are imported lazily so unit tests can
    run on a laptop without ``RPi.GPIO`` / ``smbus2`` installed.
    """
    import RPi.GPIO as GPIO  # type: ignore
    import smbus2  # type: ignore
    import serial  # type: ignore
    import serial.tools.list_ports  # type: ignore  # noqa: F401 -- attaches .tools.list_ports
    import spidev  # type: ignore

    return {"GPIO": GPIO, "smbus2": smbus2, "serial": serial, "spidev": spidev}


class BuoyApp:
    """Top-level application owning sensors, radio, store, watchdog and lifecycle."""

    def __init__(
        self,
        config: Dict[str, Any],
        config_path: str,
        hw_modules: Dict[str, Any],
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._hw = hw_modules
        self._start_time = time.monotonic()
        self._stop_event = threading.Event()
        self._reload_event = threading.Event()
        self._shutdown_reason: str = ""
        self._low_power: bool = False

        log_cfg = config["logging"]
        self._logger = configure_logging(
            log_cfg["path"],
            log_cfg["max_bytes"],
            log_cfg["backup_count"],
            log_cfg.get("level", "INFO"),
        )

        # Persistence
        data_cfg = config["data"]
        self._store = DataStore(
            db_path=data_cfg["db_path"],
            logger=self._logger,
            max_rows=int(data_cfg["max_rows"]),
        )


        wd_cfg = config["service_watchdog"]
        self._systemd_watchdog = SystemdWatchdogNotifier(
            self._logger, interval_seconds=float(wd_cfg["kick_interval_seconds"])
        )

        # Power monitor
        self._power = self._build_power_monitor()

        # Sensors
        self._depth_temp = self._build_depth_temp()
        self._imu = self._build_imu()
        needs_adc = (
            self._config["sensors"]["dissolved_oxygen"].get("enabled", True)
            or self._config["sensors"]["salinity"].get("enabled", True)
        )
        self._adc = self._build_mcp3008() if needs_adc else None
        self._do = self._build_do() if self._adc is not None else None
        self._salinity = self._build_salinity() if self._adc is not None else None
        self._leak = self._build_leak()
        self._usb_depth_temp = self._build_usb_depth_temp()

        self._sensors = [
            s for s in (
                self._depth_temp, self._imu, self._do, self._salinity, self._leak, self._usb_depth_temp,
            )
            if s is not None
        ]

        # Radio
        radio_cfg = config["radio"]
        self._radio = Radio(
            self._hw["serial"],
            self._logger,
            device=radio_cfg["device"],
            baud=int(radio_cfg["baud"]),
            read_timeout_seconds=float(radio_cfg["read_timeout_seconds"]),
        )
        self._data_service = DataRequestService(self._radio, self._store, self._logger)
        self._text_query_service = TextQueryService(
            self._radio,
            self._store,
            self._logger,
            set_interval=self.set_sample_interval,
            status_provider=self._build_status_payload,
        )
        self._interval_control_service = IntervalControlService(
            self._radio, self.set_sample_interval, self._logger
        )
        self._broadcast_service = SensorBroadcastService(self._radio, self._logger)
        self._ota_service = OTAService(self._radio, data_cfg["ota_staging_path"], self._logger)
        self._heartbeat = HeartbeatService(
            self._radio,
            self._logger,
            status_provider=self._build_status_payload,
            interval_seconds=float(radio_cfg["heartbeat_interval_seconds"]),
        )

        if self._leak is not None:
            self._leak.set_callback(lambda: self.request_shutdown("leak_detected"))

    # ---- builders ---------------------------------------------------------
    def _build_power_monitor(self) -> Optional[PowerMonitor]:
        cfg = self._config["power"]
        if not cfg.get("enabled", True):
            return None
        return PowerMonitor(
            self._hw["smbus2"],
            self._logger,
            bus=int(cfg["i2c_bus"]),
            address=int(cfg["ina219_address"]),
            shunt_ohms=float(cfg["shunt_ohms"]),
            max_expected_amps=float(cfg["max_expected_amps"]),
        )

    def _build_depth_temp(self) -> Optional[DepthTempSensor]:
        cfg = self._config["sensors"]["depth_temp"]
        if not cfg.get("enabled", True):
            return None
        return DepthTempSensor(
            self._hw["smbus2"],
            self._logger,
            bus=int(cfg["i2c_bus"]),
            address=int(cfg["address"]),
            fluid_density=float(cfg["fluid_density"]),
            osr=int(cfg.get("osr", 8192)),
        )

    def _build_imu(self) -> Optional[IMUSensor]:
        cfg = self._config["sensors"]["imu"]
        if not cfg.get("enabled", True):
            return None
        return IMUSensor(
            self._hw["spidev"],
            self._hw["GPIO"],
            self._logger,
            spi_bus=int(cfg["spi_bus"]),
            spi_device=int(cfg["spi_device"]),
            spi_speed_hz=int(cfg["spi_speed_hz"]),
            cs_gpio=int(cfg["cs_gpio"]),
            accel_range_g=int(cfg["accel_range_g"]),
            sample_rate_hz=int(self._config["sensors"]["imu_sample_rate_hz"]),
            sample_duration_seconds=int(self._config["sensors"]["imu_sample_duration_seconds"]),
        )

    def _build_mcp3008(self) -> MCP3008Reader:
        cfg = self._config["sensors"]["mcp3008"]
        return MCP3008Reader(
            self._hw["spidev"],
            self._hw["GPIO"],
            spi_bus=int(cfg["spi_bus"]),
            spi_device=int(cfg["spi_device"]),
            spi_speed_hz=int(cfg["spi_speed_hz"]),
            cs_gpio=int(cfg["cs_gpio"]),
            vref=float(cfg["vref"]),
        )

    def _build_do(self) -> Optional[DissolvedOxygenSensor]:
        cfg = self._config["sensors"]["dissolved_oxygen"]
        if not cfg.get("enabled", True):
            return None
        return DissolvedOxygenSensor(
            self._adc,
            self._logger,
            channel=int(cfg["mcp3008_channel"]),
            cal_voltage_mv=float(cfg["cal_voltage_mv"]),
            cal_temperature_c=float(cfg["cal_temperature_c"]),
            temp_provider=self._latest_temperature,
        )

    def _build_salinity(self) -> Optional[SalinitySensor]:
        cfg = self._config["sensors"]["salinity"]
        if not cfg.get("enabled", True):
            return None
        return SalinitySensor(
            self._adc,
            self._logger,
            channel=int(cfg["mcp3008_channel"]),
            cell_constant=float(cfg["cell_constant"]),
            reference_temperature_c=float(cfg["reference_temperature_c"]),
            temp_compensation_alpha=float(cfg["temp_compensation_alpha"]),
            circuit_gain=float(cfg["circuit_gain"]),
            temp_provider=self._latest_temperature,
        )

    def _build_leak(self) -> Optional[LeakSensor]:
        cfg = self._config["sensors"]["leak"]
        if not cfg.get("enabled", True):
            return None
        return LeakSensor(
            self._hw["GPIO"],
            self._logger,
            pin=int(cfg["gpio"]),
            bouncetime_ms=int(cfg["bouncetime_ms"]),
        )

    def _build_usb_depth_temp(self) -> Optional[UsbDepthTempSensor]:
        cfg = self._config["sensors"].get("usb_depth_temp", {})
        if not cfg.get("enabled", False):
            return None
        return UsbDepthTempSensor(
            self._hw["serial"],
            self._logger,
            device=cfg.get("device", "auto"),
            vendor_id=int(cfg.get("vendor_id", 0x1A86)),
            product_id=int(cfg.get("product_id", 0x7523)),
            baud=int(cfg.get("baud", 9600)),
            stale_after_seconds=float(cfg.get("stale_after_seconds", 30.0)),
        )

    def _latest_temperature(self) -> Optional[float]:
        if self._depth_temp is None:
            return None
        return self._depth_temp.last_temperature_c

    # ---- runtime control ---------------------------------------------------
    def set_sample_interval(self, seconds: float) -> float:
        """Change the normal (non-low-power) collection interval at runtime.

        This is the knob that -- once the buoy is duty-cycled by external
        power hardware instead of staying always-on -- will describe how
        often the Pi wakes up, collects, and powers back down. For now,
        while it stays on (e.g. for field testing), it's just the main
        loop's sleep interval. Not persisted to config.yaml; reverts on
        restart.
        """
        clamped = max(MIN_SAMPLE_INTERVAL_SECONDS, min(MAX_SAMPLE_INTERVAL_SECONDS, float(seconds)))
        self._config["sensors"]["sample_interval_seconds"] = clamped
        self._logger.info(
            "sample_interval_changed", extra={"component": "main", "interval_seconds": clamped}
        )
        return clamped

    # ---- status / payloads ------------------------------------------------
    def _build_status_payload(self) -> Dict[str, Any]:
        uptime = time.monotonic() - self._start_time
        power = self._power.read() if self._power is not None else None
        battery_v = power.bus_voltage if power else None
        sensor_health = {s.name: bool(s.healthy) for s in self._sensors}
        db_path = self._config["data"]["db_path"]
        return {
            "uptime_seconds": round(uptime, 3),
            "battery_voltage": battery_v,
            "battery_current": power.current if power else None,
            "battery_power": power.power if power else None,
            "low_power": self._low_power,
            "sensor_health": sensor_health,
            "row_count": self._store.row_count(),
            "max_rows": self._config["data"]["max_rows"],
            "memory": read_memory_info(),
            "disk": read_disk_info(os.path.dirname(db_path) or "."),
            "db_size_mb": read_file_size_mb(db_path),
            "clock_sane": clock_looks_sane(),
        }

    # ---- lifecycle --------------------------------------------------------
    def request_shutdown(self, reason: str) -> None:
        """Signal the main loop to exit and record the reason."""
        if self._stop_event.is_set():
            return
        self._shutdown_reason = reason
        self._logger.warning(
            "shutdown_requested", extra={"component": "main", "reason": reason}
        )
        self._stop_event.set()

    def request_reload(self) -> None:
        """Signal the main loop to reload the config file on next iteration."""
        self._logger.info("config_reload_requested")
        self._reload_event.set()

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.request_shutdown("SIGTERM"))
        signal.signal(signal.SIGINT, lambda *_: self.request_shutdown("SIGINT"))
        try:
            signal.signal(signal.SIGHUP, lambda *_: self.request_reload())
        except (AttributeError, ValueError):
            # SIGHUP is unavailable on Windows; safe to skip in dev mode.
            pass

    def _reload_config(self) -> None:
        try:
            new_cfg = load_config(self._config_path)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("config_reload_failed", extra={"error": str(exc)})
            return
        self._config = new_cfg
        self._logger.info("config_reloaded")

    # ---- main loop --------------------------------------------------------
    def _collect_once(self) -> List[Reading]:
        readings: List[Reading] = []
        # Read depth/temp first so the temperature is available to DO + salinity.
        ordered = []
        if self._depth_temp is not None:
            ordered.append(self._depth_temp)
        for s in self._sensors:
            if s is not self._depth_temp:
                ordered.append(s)
        for sensor in ordered:
            try:
                rs = sensor.read()
                readings.extend(rs)
            except Exception as exc:  # noqa: BLE001
                self._logger.error(
                    "sensor_read_exception",
                    extra={"component": sensor.name, "error": str(exc)},
                )
        return readings

    def _record_power(self) -> Optional[float]:
        if self._power is None:
            return None
        power = self._power.read()
        if power is None:
            return None
        ts = time.time()
        try:
            self._store.write_many([
                Reading(ts, "battery_voltage", power.bus_voltage, "V", 0),
                Reading(ts, "battery_current", power.current, "A", 0),
                Reading(ts, "battery_power", power.power, "W", 0),
            ])
        except Exception as exc:  # noqa: BLE001
            self._logger.error("power_store_failed", extra={"error": str(exc)})
        self._logger.info(
            "power",
            extra={
                "component": "power",
                "voltage": power.bus_voltage,
                "current": power.current,
                "power": power.power,
            },
        )
        return power.bus_voltage

    def _update_low_power(self, battery_v: Optional[float]) -> None:
        threshold = float(self._config["power"]["low_power_threshold_v"])
        if battery_v is None:
            return
        was = self._low_power
        self._low_power = battery_v < threshold
        if self._low_power != was:
            self._logger.warning(
                "low_power_mode_changed",
                extra={
                    "component": "main",
                    "low_power": self._low_power,
                    "battery_voltage": battery_v,
                },
            )

    def _current_interval(self) -> float:
        if self._low_power:
            return float(self._config["power"]["low_power_sample_interval_seconds"])
        return float(self._config["sensors"]["sample_interval_seconds"])

    def run(self) -> int:
        """Run the main loop. Returns 0 on clean exit."""
        self._install_signal_handlers()
        self._systemd_watchdog.start()
        if self._usb_depth_temp is not None:
            self._usb_depth_temp.start()
        self._radio.start()
        self._data_service.attach()
        self._text_query_service.attach()
        self._interval_control_service.attach()
        self._ota_service.attach()
        self._heartbeat.start()

        self._logger.info("buoy_started", extra={"component": "main"})

        try:
            while not self._stop_event.is_set():
                if self._reload_event.is_set():
                    self._reload_event.clear()
                    self._reload_config()

                cycle_start = time.monotonic()
                try:
                    readings = self._collect_once()
                    if readings:
                        self._store.write_many(readings)
                        self._broadcast_service.broadcast(readings)
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(
                        "collect_cycle_failed",
                        extra={"component": "main", "error": str(exc)},
                    )

                battery_v = self._record_power()
                self._update_low_power(battery_v)

                interval = self._current_interval()
                elapsed = time.monotonic() - cycle_start
                sleep_for = max(0.0, interval - elapsed)
                # Sleep in 1-second slices so we react quickly to signals.
                slept = 0.0
                while slept < sleep_for and not self._stop_event.is_set():
                    chunk = min(1.0, sleep_for - slept)
                    if self._stop_event.wait(chunk):
                        break
                    slept += chunk
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        """Tear down everything in a deterministic order."""
        if not self._shutdown_reason:
            self._shutdown_reason = "normal"
        self._logger.info(
            "shutdown_begin", extra={"component": "main", "reason": self._shutdown_reason}
        )
        try:
            self._systemd_watchdog.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("systemd_watchdog_stop_failed", extra={"error": str(exc)})
        try:
            self._heartbeat.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("heartbeat_stop_failed", extra={"error": str(exc)})
        try:
            self._radio.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("radio_stop_failed", extra={"error": str(exc)})
        for sensor in self._sensors:
            try:
                sensor.close()
            except Exception as exc:  # noqa: BLE001
                self._logger.error(
                    "sensor_close_failed",
                    extra={"component": sensor.name, "error": str(exc)},
                )
        try:
            if self._adc is not None:
                self._adc.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("adc_close_failed", extra={"error": str(exc)})
        try:
            if self._power is not None:
                self._power.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("power_close_failed", extra={"error": str(exc)})
        try:
            self._store.flush()
            self._store.close()
        except Exception as exc:  # noqa: BLE001
            self._logger.error("store_close_failed", extra={"error": str(exc)})
        self._logger.info(
            "shutdown_complete",
            extra={"component": "main", "reason": self._shutdown_reason},
        )


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point used by both the CLI and systemd."""
    parser = argparse.ArgumentParser(description="Ocean monitoring buoy daemon")
    parser.add_argument(
        "--config",
        default=os.environ.get("BUOY_CONFIG", "/etc/buoy/config.yaml"),
        help="Path to YAML config",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    hw = _import_hardware_modules()
    app = BuoyApp(config=config, config_path=args.config, hw_modules=hw)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
