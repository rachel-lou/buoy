"""DFRobot Gravity analog dissolved oxygen sensor read via MCP3008."""
from __future__ import annotations

import logging
from typing import List, Optional

from . import BaseSensor, Reading, QUALITY_ERROR, QUALITY_GOOD, QUALITY_WARNING

# DO saturation table (mg/L) for fresh water at 1 atm, 0-40 °C in 1 °C steps.
_DO_SAT_TABLE = [
    14.62, 14.22, 13.83, 13.46, 13.11, 12.77, 12.45, 12.14, 11.84, 11.56,
    11.29, 11.03, 10.78, 10.54, 10.31, 10.08,  9.87,  9.67,  9.47,  9.28,
     9.09,  8.92,  8.74,  8.58,  8.42,  8.26,  8.11,  7.97,  7.83,  7.69,
     7.56,  7.43,  7.30,  7.18,  7.07,  6.95,  6.84,  6.73,  6.63,  6.53,
     6.41,
]


class MCP3008Reader:
    """Helper that performs a single MCP3008 channel read over SPI.

    Shared by the DO and salinity drivers because both probes live on the
    same ADC.
    """

    def __init__(
        self,
        spidev_module,
        gpio_module,
        spi_bus: int,
        spi_device: int,
        spi_speed_hz: int,
        cs_gpio: int,
        vref: float,
    ) -> None:
        self._spidev_module = spidev_module
        self._gpio = gpio_module
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._spi_speed = spi_speed_hz
        self._cs = cs_gpio
        self._vref = vref
        self._spi = None
        self._open()

    def _open(self) -> None:
        self._spi = self._spidev_module.SpiDev()
        self._spi.open(self._spi_bus, self._spi_device)
        self._spi.max_speed_hz = self._spi_speed
        self._spi.mode = 0
        self._gpio.setwarnings(False)
        self._gpio.setmode(self._gpio.BCM)
        self._gpio.setup(self._cs, self._gpio.OUT, initial=self._gpio.HIGH)

    def read_channel(self, channel: int) -> float:
        """Return the channel voltage in volts."""
        if channel < 0 or channel > 7:
            raise ValueError(f"MCP3008 channel out of range: {channel}")
        self._gpio.output(self._cs, self._gpio.LOW)
        try:
            cmd = [0x01, (0x08 | channel) << 4, 0x00]
            response = self._spi.xfer2(cmd)
        finally:
            self._gpio.output(self._cs, self._gpio.HIGH)
        raw = ((response[1] & 0x03) << 8) | response[2]
        return (raw / 1023.0) * self._vref

    def close(self) -> None:
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:  # noqa: BLE001
                pass
            self._spi = None


def do_saturation_mg_per_l(temperature_c: float) -> float:
    """Return DO saturation concentration in mg/L for the given temperature."""
    if temperature_c <= 0:
        return _DO_SAT_TABLE[0]
    if temperature_c >= 40:
        return _DO_SAT_TABLE[-1]
    lo = int(temperature_c)
    hi = lo + 1
    frac = temperature_c - lo
    return _DO_SAT_TABLE[lo] * (1.0 - frac) + _DO_SAT_TABLE[hi] * frac


class DissolvedOxygenSensor(BaseSensor):
    """DFRobot Gravity analog DO probe with temperature compensation."""

    def __init__(
        self,
        adc: MCP3008Reader,
        logger: logging.Logger,
        channel: int = 0,
        cal_voltage_mv: float = 1600.0,
        cal_temperature_c: float = 25.0,
        temp_provider=None,
    ) -> None:
        super().__init__("dissolved_oxygen", logger)
        self._adc = adc
        self._channel = channel
        self._cal_voltage_mv = float(cal_voltage_mv)
        self._cal_temperature_c = float(cal_temperature_c)
        self._temp_provider = temp_provider

    def _resolve_temperature(self) -> Optional[float]:
        if self._temp_provider is None:
            return None
        try:
            t = self._temp_provider()
            if t is None:
                return None
            return float(t)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "temp_provider_failed",
                extra={"component": self.name, "error": str(exc)},
            )
            return None

    def read(self) -> List[Reading]:
        """Return the temperature-compensated DO concentration in mg/L."""
        try:
            voltage = self._adc.read_channel(self._channel)
            voltage_mv = voltage * 1000.0
            temp_c = self._resolve_temperature()

            if temp_c is None:
                quality = QUALITY_WARNING
                effective_temp = self._cal_temperature_c
            else:
                quality = QUALITY_GOOD
                effective_temp = temp_c

            v_sat_at_cal_temp = self._cal_voltage_mv  # mV @ 100% sat at cal temp
            # DFRobot temperature correction: V_sat(T) = V_cal * (1 + 0.022 * (T - T_cal))
            v_sat = v_sat_at_cal_temp * (
                1.0 + 0.022 * (effective_temp - self._cal_temperature_c)
            )
            do_sat_mg = do_saturation_mg_per_l(effective_temp)
            if v_sat <= 0:
                do_mg = 0.0
                quality = QUALITY_WARNING
            else:
                do_mg = max(0.0, (voltage_mv / v_sat) * do_sat_mg)

            if do_mg > 25.0:
                quality = QUALITY_WARNING

            self._mark_ok()
            return [Reading(self._now(), "dissolved_oxygen", float(do_mg), "mg/L", quality)]
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"read: {exc}")
            return [Reading(self._now(), "dissolved_oxygen", 0.0, "mg/L", QUALITY_ERROR)]

    def close(self) -> None:
        """No-op; the shared ADC is closed by its owner."""
        return None
