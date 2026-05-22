"""Sensor unit tests with mocked hardware."""
from __future__ import annotations

import logging
import math
import os
import sys
import unittest
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sensors import Reading  # noqa: E402
from sensors.depth_temp import DepthTempSensor  # noqa: E402
from sensors.dissolved_oxygen import (  # noqa: E402
    DissolvedOxygenSensor,
    MCP3008Reader,
    do_saturation_mg_per_l,
)
from sensors.imu import IMUSensor  # noqa: E402
from sensors.leak import LeakSensor  # noqa: E402
from sensors.salinity import SalinitySensor, _salinity_pss78  # noqa: E402


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("tests")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


def _build_ms5837_mock_bus():
    """Return a MagicMock SMBus + module with realistic PROM and ADC behaviour."""
    bus_instance = MagicMock()

    # PROM coefficients with a valid CRC; reuse known-good MS5837-02BA values
    base_prom = [0, 40000, 38000, 25000, 24000, 33000, 30000, 0]

    def _crc4(prom_list):
        n_prom = list(prom_list)
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
        return (n_rem >> 12) & 0x000F

    crc = _crc4(list(base_prom))
    base_prom[0] = (crc & 0x0F) << 12

    # Pressure / temperature ADC raw values that produce sane results
    pressure_raw = 8_400_000   # high D1
    temp_raw = 8_300_000       # high D2

    state = {"last_cmd": None}

    def write_byte(addr, cmd):
        state["last_cmd"] = cmd

    def read_i2c_block_data(addr, reg, count):
        # PROM read
        if 0xA0 <= reg <= 0xAE and count == 2:
            idx = (reg - 0xA0) // 2
            value = base_prom[idx]
            return [(value >> 8) & 0xFF, value & 0xFF]
        # ADC read after a conversion command
        if reg == 0x00 and count == 3:
            cmd = state["last_cmd"] or 0
            raw = pressure_raw if (cmd & 0xF0) == 0x40 else temp_raw
            return [
                (raw >> 16) & 0xFF,
                (raw >> 8) & 0xFF,
                raw & 0xFF,
            ]
        return [0] * count

    bus_instance.write_byte.side_effect = write_byte
    bus_instance.read_i2c_block_data.side_effect = read_i2c_block_data
    bus_instance.close = MagicMock()

    module = MagicMock()
    module.SMBus.return_value = bus_instance
    return module, bus_instance


class TestDepthTempSensor(unittest.TestCase):
    def test_read_returns_three_readings(self):
        module, _ = _build_ms5837_mock_bus()
        sensor = DepthTempSensor(module, _silent_logger(), bus=1, address=0x76,
                                  fluid_density=1029.0, osr=256)
        readings = sensor.read()
        self.assertEqual(len(readings), 3)
        names = {r.sensor for r in readings}
        self.assertEqual(names, {"pressure", "depth", "temperature"})
        for r in readings:
            self.assertIsInstance(r, Reading)
            self.assertIn(r.quality_flag, (0, 1, 2))
        sensor.close()

    def test_temperature_cache_populates(self):
        module, _ = _build_ms5837_mock_bus()
        sensor = DepthTempSensor(module, _silent_logger(), bus=1, address=0x76, osr=256)
        self.assertIsNone(sensor.last_temperature_c)
        sensor.read()
        self.assertIsNotNone(sensor.last_temperature_c)


class TestDissolvedOxygen(unittest.TestCase):
    def test_saturation_table_monotone(self):
        prev = do_saturation_mg_per_l(0.0)
        for t in range(1, 41):
            v = do_saturation_mg_per_l(float(t))
            self.assertLess(v, prev + 0.01)
            prev = v

    def test_do_read_with_temp_provider(self):
        adc = MagicMock(spec=MCP3008Reader)
        adc.read_channel.return_value = 1.6  # volts = 1600 mV = 100% sat
        sensor = DissolvedOxygenSensor(
            adc, _silent_logger(), channel=0,
            cal_voltage_mv=1600.0, cal_temperature_c=25.0,
            temp_provider=lambda: 25.0,
        )
        result = sensor.read()
        self.assertEqual(len(result), 1)
        r = result[0]
        self.assertEqual(r.sensor, "dissolved_oxygen")
        self.assertEqual(r.unit, "mg/L")
        self.assertAlmostEqual(r.value, do_saturation_mg_per_l(25.0), places=2)
        self.assertEqual(r.quality_flag, 0)

    def test_do_read_without_temp_uses_calibration(self):
        adc = MagicMock(spec=MCP3008Reader)
        adc.read_channel.return_value = 0.8  # 50% sat
        sensor = DissolvedOxygenSensor(
            adc, _silent_logger(), channel=0,
            cal_voltage_mv=1600.0, cal_temperature_c=25.0,
            temp_provider=None,
        )
        result = sensor.read()
        self.assertEqual(result[0].quality_flag, 1)  # warning, no temp


class TestSalinity(unittest.TestCase):
    def test_pss78_reference_point(self):
        # At standard conductivity 42.914 mS/cm, T=15, P=0 -> salinity ~35 PSU
        psu = _salinity_pss78(42.914, 15.0, 0.0)
        self.assertAlmostEqual(psu, 35.0, places=2)

    def test_salinity_read_returns_two_readings(self):
        adc = MagicMock(spec=MCP3008Reader)
        adc.read_channel.return_value = 1.5
        sensor = SalinitySensor(
            adc, _silent_logger(), channel=1, cell_constant=1.0,
            reference_temperature_c=25.0, temp_compensation_alpha=0.02,
            circuit_gain=30000.0, temp_provider=lambda: 15.0,
        )
        readings = sensor.read()
        self.assertEqual(len(readings), 2)
        units = {r.unit for r in readings}
        self.assertEqual(units, {"mS/cm", "PSU"})


class TestIMU(unittest.TestCase):
    def _build_imu_mocks(self):
        spi_dev = MagicMock()
        spi_module = MagicMock()
        spi_module.SpiDev.return_value = spi_dev

        gpio = MagicMock()
        gpio.HIGH = 1
        gpio.LOW = 0
        gpio.BCM = 11
        gpio.OUT = 0
        gpio.IN = 1

        def xfer2(buf):
            reg = buf[0] & 0x7F
            if reg == 0x75:  # WHO_AM_I
                return [0, 0x47]
            if reg == 0x1F:  # accel data x1
                # 6 bytes returned; Z = +1 g (16384 LSB at 2g, scale auto adjusts)
                return [0, 0, 0, 0, 0, 0x20, 0x00]
            return [0] * len(buf)
        spi_dev.xfer2.side_effect = xfer2
        spi_dev.max_speed_hz = 0
        spi_dev.mode = 0
        return spi_module, gpio

    def test_imu_initializes(self):
        spi_module, gpio = self._build_imu_mocks()
        imu = IMUSensor(spi_module, gpio, _silent_logger(),
                        spi_speed_hz=1_000_000, sample_rate_hz=100,
                        sample_duration_seconds=1)
        self.assertTrue(imu.healthy)

    def test_wave_stats_sinusoid(self):
        spi_module, gpio = self._build_imu_mocks()
        imu = IMUSensor(spi_module, gpio, _silent_logger(),
                        spi_speed_hz=1_000_000, sample_rate_hz=100,
                        sample_duration_seconds=10)
        fs = 100.0
        t = np.arange(0, 60.0, 1.0 / fs)
        # Pure 0.2Hz wave, displacement amplitude 1.0 m
        omega = 2 * math.pi * 0.2
        accel_ms2 = -(omega ** 2) * np.sin(omega * t)  # d2/dt2 of sin(omega t)
        accel_g = accel_ms2 / 9.80665
        hs, tp = imu.compute_wave_stats(accel_g)
        self.assertGreater(hs, 0.5)
        self.assertAlmostEqual(tp, 5.0, delta=0.5)


class TestLeak(unittest.TestCase):
    def test_leak_callback_invoked(self):
        gpio = MagicMock()
        gpio.BCM = 11
        gpio.IN = 1
        gpio.PUD_UP = 22
        gpio.FALLING = 32
        gpio.HIGH = 1
        gpio.LOW = 0
        gpio.input = MagicMock(return_value=0)

        triggered = {"value": False}

        def on_leak():
            triggered["value"] = True

        sensor = LeakSensor(gpio, _silent_logger(), pin=17, on_leak=on_leak)
        # Simulate edge
        callback_args = gpio.add_event_detect.call_args
        cb = callback_args.kwargs.get("callback") or callback_args.args[2]
        cb(17)
        self.assertTrue(triggered["value"])
        self.assertTrue(sensor.is_leaking())

    def test_read_returns_state(self):
        gpio = MagicMock()
        gpio.BCM = 11
        gpio.IN = 1
        gpio.PUD_UP = 22
        gpio.FALLING = 32
        gpio.HIGH = 1
        gpio.LOW = 0
        gpio.input = MagicMock(return_value=1)
        sensor = LeakSensor(gpio, _silent_logger(), pin=17)
        readings = sensor.read()
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].value, 0.0)


if __name__ == "__main__":
    unittest.main()
