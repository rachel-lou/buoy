"""INA219 battery monitor driver."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

_REG_CONFIG = 0x00
_REG_SHUNT_V = 0x01
_REG_BUS_V = 0x02
_REG_POWER = 0x03
_REG_CURRENT = 0x04
_REG_CALIBRATION = 0x05

# Config: 32V bus range, +-320mV shunt, 12-bit, 532us, continuous shunt+bus
_CONFIG_DEFAULT = 0x399F


@dataclass
class PowerReading:
    """One INA219 sample."""

    bus_voltage: float    # V
    shunt_voltage: float  # V
    current: float        # A
    power: float          # W


class PowerMonitor:
    """INA219 driver over I2C."""

    def __init__(
        self,
        smbus_module,
        logger: logging.Logger,
        bus: int = 1,
        address: int = 0x40,
        shunt_ohms: float = 0.1,
        max_expected_amps: float = 3.2,
    ) -> None:
        self._smbus_module = smbus_module
        self._logger = logger
        self._bus_num = bus
        self._address = address
        self._shunt_ohms = float(shunt_ohms)
        self._max_expected_amps = float(max_expected_amps)
        self._bus = None
        self._current_lsb: float = max_expected_amps / 32768.0
        self._power_lsb: float = self._current_lsb * 20.0
        self._open()

    def _open(self) -> None:
        try:
            self._bus = self._smbus_module.SMBus(self._bus_num)
            self._write_register(_REG_CONFIG, _CONFIG_DEFAULT)
            cal = int(0.04096 / (self._current_lsb * self._shunt_ohms))
            cal &= 0xFFFE
            self._write_register(_REG_CALIBRATION, cal)
            self._logger.info(
                "power_monitor_init",
                extra={"module": "power", "address": self._address, "cal": cal},
            )
        except Exception as exc:  # noqa: BLE001
            self._bus = None
            self._logger.error(
                "power_monitor_open_failed",
                extra={"module": "power", "error": str(exc)},
            )

    def _write_register(self, reg: int, value: int) -> None:
        hi = (value >> 8) & 0xFF
        lo = value & 0xFF
        self._bus.write_i2c_block_data(self._address, reg, [hi, lo])

    def _read_register(self, reg: int) -> int:
        data = self._bus.read_i2c_block_data(self._address, reg, 2)
        return ((data[0] << 8) | data[1]) & 0xFFFF

    @staticmethod
    def _as_signed16(value: int) -> int:
        return value - 0x10000 if value & 0x8000 else value

    def read(self) -> Optional[PowerReading]:
        """Sample the INA219 and return a :class:`PowerReading`, or None on error."""
        if self._bus is None:
            self._open()
        if self._bus is None:
            return None
        try:
            raw_shunt = self._as_signed16(self._read_register(_REG_SHUNT_V))
            raw_bus = self._read_register(_REG_BUS_V)
            raw_current = self._as_signed16(self._read_register(_REG_CURRENT))
            raw_power = self._read_register(_REG_POWER)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "power_monitor_read_failed",
                extra={"module": "power", "error": str(exc)},
            )
            return None

        bus_voltage = ((raw_bus >> 3) * 4.0) / 1000.0  # mV -> V, drop status bits
        shunt_voltage = raw_shunt * 10e-6              # 10uV LSB -> V
        current = raw_current * self._current_lsb       # A
        power = raw_power * self._power_lsb             # W

        return PowerReading(
            bus_voltage=float(bus_voltage),
            shunt_voltage=float(shunt_voltage),
            current=float(current),
            power=float(power),
        )

    def close(self) -> None:
        """Close the I2C bus."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
