"""MS5837-02BA depth and temperature sensor driver (I2C)."""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from . import BaseSensor, Reading, QUALITY_ERROR, QUALITY_GOOD, QUALITY_WARNING

_CMD_RESET = 0x1E
_CMD_ADC_READ = 0x00
_CMD_PROM_READ_BASE = 0xA0
_CMD_CONVERT_D1_BASE = 0x40   # pressure
_CMD_CONVERT_D2_BASE = 0x50   # temperature

# Conversion time in seconds per OSR setting
_OSR_DELAY = {
    256: 0.001,
    512: 0.002,
    1024: 0.003,
    2048: 0.005,
    4096: 0.010,
    8192: 0.020,
}

_OSR_OFFSET = {
    256: 0x00,
    512: 0x02,
    1024: 0x04,
    2048: 0x06,
    4096: 0x08,
    8192: 0x0A,
}

_G = 9.80665


class DepthTempSensor(BaseSensor):
    """MS5837-02BA pressure transducer.

    Outputs three readings per :meth:`read` call:

    * ``pressure`` (mbar)
    * ``depth`` (m, computed from gauge pressure and fluid density)
    * ``temperature`` (°C)
    """

    def __init__(
        self,
        smbus_module,
        logger: logging.Logger,
        bus: int = 1,
        address: int = 0x76,
        fluid_density: float = 1029.0,
        osr: int = 8192,
    ) -> None:
        super().__init__("depth_temp", logger)
        if osr not in _OSR_DELAY:
            raise ValueError(f"Unsupported OSR {osr}")
        self._smbus_module = smbus_module
        self._bus_num = bus
        self._address = address
        self._fluid_density = float(fluid_density)
        self._osr = osr
        self._bus = None
        self._prom: Tuple[int, ...] = ()
        self._last_temperature_c: Optional[float] = None
        self._open()

    def _open(self) -> None:
        try:
            self._bus = self._smbus_module.SMBus(self._bus_num)
            self._bus.write_byte(self._address, _CMD_RESET)
            time.sleep(0.020)
            self._prom = self._read_prom()
            if not self._validate_prom(self._prom):
                raise IOError("MS5837 PROM CRC mismatch")
            self._mark_ok()
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"open: {exc}")

    def _read_prom(self) -> Tuple[int, ...]:
        coefficients = []
        for i in range(8):
            data = self._bus.read_i2c_block_data(
                self._address, _CMD_PROM_READ_BASE + (i * 2), 2
            )
            coefficients.append((data[0] << 8) | data[1])
        return tuple(coefficients)

    @staticmethod
    def _validate_prom(prom: Tuple[int, ...]) -> bool:
        """CRC-4 check defined in the MS5837 datasheet."""
        if len(prom) != 8:
            return False
        n_prom = list(prom)
        crc_read = (n_prom[0] & 0xF000) >> 12
        n_prom[0] = n_prom[0] & 0x0FFF
        n_prom.append(0)
        n_rem = 0
        for i in range(16):
            if i % 2 == 1:
                n_rem ^= n_prom[i >> 1] & 0x00FF
            else:
                n_rem ^= n_prom[i >> 1] >> 8
            for _ in range(8):
                if n_rem & 0x8000:
                    n_rem = (n_rem << 1) ^ 0x3000
                else:
                    n_rem = n_rem << 1
                n_rem &= 0xFFFF
        n_rem = (n_rem >> 12) & 0x000F
        return n_rem == crc_read

    def _convert(self, base_cmd: int) -> int:
        cmd = base_cmd + _OSR_OFFSET[self._osr]
        self._bus.write_byte(self._address, cmd)
        time.sleep(_OSR_DELAY[self._osr])
        data = self._bus.read_i2c_block_data(self._address, _CMD_ADC_READ, 3)
        return (data[0] << 16) | (data[1] << 8) | data[2]

    def _compute(self, d1: int, d2: int) -> Tuple[float, float]:
        c1, c2, c3, c4, c5, c6 = self._prom[1:7]
        d_t = d2 - (c5 << 8)
        temp = 2000 + ((d_t * c6) >> 23)

        # 02BA variant constants
        off = (c2 << 17) + ((c4 * d_t) >> 6)
        sens = (c1 << 16) + ((c3 * d_t) >> 7)

        # Second order temperature compensation (02BA)
        if temp < 2000:
            t2 = (11 * d_t * d_t) >> 35
            off2 = (31 * (temp - 2000) ** 2) >> 3
            sens2 = (63 * (temp - 2000) ** 2) >> 5
        else:
            t2 = 0
            off2 = 0
            sens2 = 0

        temp -= t2
        off -= off2
        sens -= sens2

        pressure = (((d1 * sens) >> 21) - off) / 32768.0  # 0.01 mbar -> mbar after /100
        pressure_mbar = pressure / 100.0
        temperature_c = temp / 100.0
        return pressure_mbar, temperature_c

    @property
    def last_temperature_c(self) -> Optional[float]:
        """Most recent temperature in °C, or ``None`` if never read."""
        return self._last_temperature_c

    def read(self) -> List[Reading]:
        """Trigger a conversion, return pressure, depth and temperature."""
        if self._bus is None:
            self._open()
        if self._bus is None:
            ts = self._now()
            return [
                Reading(ts, "pressure", 0.0, "mbar", QUALITY_ERROR),
                Reading(ts, "depth", 0.0, "m", QUALITY_ERROR),
                Reading(ts, "temperature", 0.0, "C", QUALITY_ERROR),
            ]

        try:
            d1 = self._convert(_CMD_CONVERT_D1_BASE)
            d2 = self._convert(_CMD_CONVERT_D2_BASE)
            pressure_mbar, temperature_c = self._compute(d1, d2)
            self._last_temperature_c = temperature_c

            # Depth from gauge pressure relative to 1013.25 mbar atmosphere.
            gauge_pa = (pressure_mbar - 1013.25) * 100.0
            depth_m = max(0.0, gauge_pa / (self._fluid_density * _G))

            quality = QUALITY_GOOD
            if temperature_c < -5 or temperature_c > 60:
                quality = QUALITY_WARNING
            if pressure_mbar < 300 or pressure_mbar > 3000:
                quality = QUALITY_WARNING

            self._mark_ok()
            ts = self._now()
            return [
                Reading(ts, "pressure", float(pressure_mbar), "mbar", quality),
                Reading(ts, "depth", float(depth_m), "m", quality),
                Reading(ts, "temperature", float(temperature_c), "C", quality),
            ]
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"read: {exc}")
            ts = self._now()
            return [
                Reading(ts, "pressure", 0.0, "mbar", QUALITY_ERROR),
                Reading(ts, "depth", 0.0, "m", QUALITY_ERROR),
                Reading(ts, "temperature", 0.0, "C", QUALITY_ERROR),
            ]

    def close(self) -> None:
        """Close the underlying I2C bus."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("i2c_close_failed", extra={"error": str(exc)})
            self._bus = None
