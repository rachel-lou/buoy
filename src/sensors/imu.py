"""ICM-42688-P IMU driver with wave spectrum analysis (SPI)."""
from __future__ import annotations

import logging
import time
from typing import List, Tuple

import numpy as np

from . import BaseSensor, Reading, QUALITY_ERROR, QUALITY_GOOD, QUALITY_WARNING

_REG_WHO_AM_I = 0x75
_WHO_AM_I_EXPECTED = 0x47
_REG_PWR_MGMT0 = 0x4E
_REG_ACCEL_CONFIG0 = 0x50
_REG_ACCEL_DATA_X1 = 0x1F  # 6 bytes: X1 X0 Y1 Y0 Z1 Z0
_REG_DEVICE_CONFIG = 0x11
_REG_INTF_CONFIG0 = 0x4C

# PWR_MGMT0: accel_mode bits [1:0] -> 11 = low-noise
_PWR_LN = 0x03

# ACCEL_CONFIG0:
#   ACCEL_FS_SEL [7:5] : 000=16g, 001=8g, 010=4g, 011=2g
#   ACCEL_ODR    [3:0] : 0x08=100Hz, 0x09=50Hz
_FS_SEL = {16: 0x00, 8: 0x20, 4: 0x40, 2: 0x60}
_FS_SENS = {16: 2048.0, 8: 4096.0, 4: 8192.0, 2: 16384.0}  # LSB/g
_ODR_100HZ = 0x08

_G = 9.80665


class IMUSensor(BaseSensor):
    """ICM-42688-P SPI accelerometer driver with sea-state analysis."""

    def __init__(
        self,
        spidev_module,
        gpio_module,
        logger: logging.Logger,
        spi_bus: int = 0,
        spi_device: int = 0,
        spi_speed_hz: int = 1_000_000,
        cs_gpio: int = 8,
        accel_range_g: int = 4,
        sample_rate_hz: int = 100,
        sample_duration_seconds: int = 30,
    ) -> None:
        super().__init__("imu", logger)
        if accel_range_g not in _FS_SEL:
            raise ValueError(f"Unsupported accel range {accel_range_g}")
        self._spidev_module = spidev_module
        self._gpio = gpio_module
        self._spi_bus = spi_bus
        self._spi_device = spi_device
        self._spi_speed = spi_speed_hz
        self._cs = cs_gpio
        self._range = accel_range_g
        self._sensitivity = _FS_SENS[accel_range_g]
        self._sample_rate = sample_rate_hz
        self._duration = sample_duration_seconds
        self._spi = None
        self._configured = False
        self._open()

    def _open(self) -> None:
        try:
            self._spi = self._spidev_module.SpiDev()
            self._spi.open(self._spi_bus, self._spi_device)
            self._spi.max_speed_hz = self._spi_speed
            self._spi.mode = 0
            self._gpio.setwarnings(False)
            self._gpio.setmode(self._gpio.BCM)
            self._gpio.setup(self._cs, self._gpio.OUT, initial=self._gpio.HIGH)
            self._configure()
            self._mark_ok()
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"open: {exc}")

    def _select(self) -> None:
        self._gpio.output(self._cs, self._gpio.LOW)

    def _deselect(self) -> None:
        self._gpio.output(self._cs, self._gpio.HIGH)

    def _write_register(self, reg: int, value: int) -> None:
        self._select()
        try:
            self._spi.xfer2([reg & 0x7F, value & 0xFF])
        finally:
            self._deselect()

    def _read_registers(self, reg: int, count: int) -> List[int]:
        self._select()
        try:
            buf = [reg | 0x80] + [0x00] * count
            response = self._spi.xfer2(buf)
        finally:
            self._deselect()
        return list(response[1:])

    def _configure(self) -> None:
        # Soft reset
        self._write_register(_REG_DEVICE_CONFIG, 0x01)
        time.sleep(0.02)

        who = self._read_registers(_REG_WHO_AM_I, 1)[0]
        if who != _WHO_AM_I_EXPECTED:
            raise IOError(f"ICM-42688-P WHO_AM_I=0x{who:02X}, expected 0x47")

        # Place accelerometer in low-noise mode
        self._write_register(_REG_PWR_MGMT0, _PWR_LN)
        time.sleep(0.001)

        # Configure range + 100Hz ODR
        self._write_register(_REG_ACCEL_CONFIG0, _FS_SEL[self._range] | _ODR_100HZ)
        time.sleep(0.05)
        self._configured = True

    def _read_accel_z_g(self) -> float:
        data = self._read_registers(_REG_ACCEL_DATA_X1, 6)
        raw_z = (data[4] << 8) | data[5]
        if raw_z & 0x8000:
            raw_z -= 1 << 16
        return raw_z / self._sensitivity

    def _collect_window(self) -> np.ndarray:
        n = int(self._sample_rate * self._duration)
        samples = np.empty(n, dtype=np.float64)
        period = 1.0 / float(self._sample_rate)
        start = time.monotonic()
        for i in range(n):
            samples[i] = self._read_accel_z_g()
            next_t = start + (i + 1) * period
            sleep_for = next_t - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
        return samples

    @staticmethod
    def _double_integrate(accel_ms2: np.ndarray, fs: float) -> np.ndarray:
        """Return displacement (m) from acceleration (m/s^2) via two cumulative
        trapezoidal integrals with mean removal at each stage to suppress drift.
        """
        dt = 1.0 / fs
        a = accel_ms2 - np.mean(accel_ms2)
        velocity = np.cumsum((a[:-1] + a[1:]) * 0.5) * dt
        velocity = np.concatenate(([0.0], velocity))
        velocity -= np.mean(velocity)
        displacement = np.cumsum((velocity[:-1] + velocity[1:]) * 0.5) * dt
        displacement = np.concatenate(([0.0], displacement))
        displacement -= np.mean(displacement)
        return displacement

    @staticmethod
    def _peak_frequency(displacement: np.ndarray, fs: float) -> float:
        n = displacement.size
        window = np.hanning(n)
        windowed = (displacement - np.mean(displacement)) * window
        spectrum = np.fft.rfft(windowed)
        power = np.abs(spectrum) ** 2
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)
        # Restrict search to plausible ocean wave band (0.04 - 1.0 Hz, 1-25 s period)
        mask = (freqs >= 0.04) & (freqs <= 1.0)
        if not np.any(mask):
            return 0.0
        sub_power = power[mask]
        sub_freqs = freqs[mask]
        if np.all(sub_power == 0):
            return 0.0
        return float(sub_freqs[int(np.argmax(sub_power))])

    def compute_wave_stats(self, accel_g: np.ndarray) -> Tuple[float, float]:
        """Return (significant_wave_height_m, peak_period_s)."""
        accel_ms2 = accel_g.astype(np.float64) * _G
        displacement = self._double_integrate(accel_ms2, float(self._sample_rate))
        hs = 4.0 * float(np.std(displacement))
        peak_freq = self._peak_frequency(displacement, float(self._sample_rate))
        tp = (1.0 / peak_freq) if peak_freq > 0 else 0.0
        return hs, tp

    def read(self) -> List[Reading]:
        """Collect a 30 s acceleration window and return wave stats."""
        if self._spi is None or not self._configured:
            self._open()
        if self._spi is None or not self._configured:
            ts = self._now()
            return [
                Reading(ts, "wave_hs", 0.0, "m", QUALITY_ERROR),
                Reading(ts, "wave_tp", 0.0, "s", QUALITY_ERROR),
            ]
        try:
            accel = self._collect_window()
            hs, tp = self.compute_wave_stats(accel)
            quality = QUALITY_GOOD
            if hs > 20.0 or tp > 25.0:
                quality = QUALITY_WARNING
            self._mark_ok()
            ts = self._now()
            return [
                Reading(ts, "wave_hs", float(hs), "m", quality),
                Reading(ts, "wave_tp", float(tp), "s", quality),
            ]
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"read: {exc}")
            ts = self._now()
            return [
                Reading(ts, "wave_hs", 0.0, "m", QUALITY_ERROR),
                Reading(ts, "wave_tp", 0.0, "s", QUALITY_ERROR),
            ]

    def close(self) -> None:
        """Release SPI and GPIO resources held by the IMU driver."""
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("spi_close_failed", extra={"error": str(exc)})
            self._spi = None
        try:
            self._gpio.cleanup(self._cs)
        except Exception:  # noqa: BLE001
            pass
