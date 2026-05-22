"""Two-electrode conductivity sensor with PSS-78 salinity conversion."""
from __future__ import annotations

import logging
import math
from typing import List, Optional

from . import BaseSensor, Reading, QUALITY_ERROR, QUALITY_GOOD, QUALITY_WARNING
from .dissolved_oxygen import MCP3008Reader

# Standard seawater conductivity (mS/cm) at 35 PSU, 15 °C
_C_15_35 = 42.914


def _salinity_pss78(conductivity_ms_cm: float, temperature_c: float, pressure_dbar: float = 0.0) -> float:
    """Return practical salinity (PSU) from conductivity (mS/cm) and temperature (°C).

    Implements the PSS-78 algorithm; pressure defaults to 0 (surface).
    """
    if conductivity_ms_cm <= 0:
        return 0.0

    R = conductivity_ms_cm / _C_15_35
    T = temperature_c

    # rT polynomial
    c0, c1, c2, c3, c4 = (
        0.6766097,
        2.00564e-2,
        1.104259e-4,
        -6.9698e-7,
        1.0031e-9,
    )
    rT = c0 + c1 * T + c2 * T * T + c3 * T ** 3 + c4 * T ** 4

    # pressure correction
    d1, d2, d3, d4 = 3.426e-2, 4.464e-4, 4.215e-1, -3.107e-3
    e1, e2, e3 = 2.070e-5, -6.370e-10, 3.989e-15
    P = pressure_dbar
    Rp = 1.0 + (
        P * (e1 + e2 * P + e3 * P * P)
    ) / (1.0 + d1 * T + d2 * T * T + (d3 + d4 * T) * R)

    Rt = R / (Rp * rT)
    sqrt_Rt = math.sqrt(max(Rt, 0.0))

    a0, a1, a2, a3, a4, a5 = (
        0.0080,
        -0.1692,
        25.3851,
        14.0941,
        -7.0261,
        2.7081,
    )
    b0, b1, b2, b3, b4, b5 = (
        0.0005,
        -0.0056,
        -0.0066,
        -0.0375,
        0.0636,
        -0.0144,
    )
    k = 0.0162

    S = (
        a0
        + a1 * sqrt_Rt
        + a2 * Rt
        + a3 * Rt * sqrt_Rt
        + a4 * Rt * Rt
        + a5 * Rt * Rt * sqrt_Rt
    )

    dS = (T - 15.0) / (1.0 + k * (T - 15.0)) * (
        b0
        + b1 * sqrt_Rt
        + b2 * Rt
        + b3 * Rt * sqrt_Rt
        + b4 * Rt * Rt
        + b5 * Rt * Rt * sqrt_Rt
    )
    return max(0.0, S + dS)


class SalinitySensor(BaseSensor):
    """Two-electrode conductivity probe -> conductivity, salinity, PSU."""

    def __init__(
        self,
        adc: MCP3008Reader,
        logger: logging.Logger,
        channel: int = 1,
        cell_constant: float = 1.0,
        reference_temperature_c: float = 25.0,
        temp_compensation_alpha: float = 0.02,
        circuit_gain: float = 1000.0,
        temp_provider=None,
    ) -> None:
        super().__init__("salinity", logger)
        self._adc = adc
        self._channel = channel
        self._cell_constant = float(cell_constant)
        self._reference_temp = float(reference_temperature_c)
        self._alpha = float(temp_compensation_alpha)
        self._circuit_gain = float(circuit_gain)
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
                extra={"module": self.name, "error": str(exc)},
            )
            return None

    def read(self) -> List[Reading]:
        """Return conductivity (mS/cm) and salinity (PSU) readings."""
        try:
            voltage = self._adc.read_channel(self._channel)

            # Conductance from probe circuit (microsiemens); divide by cell
            # constant to obtain conductivity in uS/cm, then convert to mS/cm.
            conductance_us = voltage * self._circuit_gain
            conductivity_ms_cm = (conductance_us / self._cell_constant) / 1000.0

            temp_c = self._resolve_temperature()
            if temp_c is None:
                quality = QUALITY_WARNING
                effective_temp = self._reference_temp
            else:
                quality = QUALITY_GOOD
                effective_temp = temp_c

            # Temperature compensation -> reference temperature
            comp = 1.0 + self._alpha * (effective_temp - self._reference_temp)
            if comp <= 0:
                comp = 1.0
                quality = QUALITY_WARNING
            conductivity_ref = conductivity_ms_cm / comp

            # PSU from conductivity at sample temperature (no pressure)
            psu = _salinity_pss78(conductivity_ms_cm, effective_temp, pressure_dbar=0.0)

            if psu > 45.0 or conductivity_ms_cm < 0:
                quality = QUALITY_WARNING

            self._mark_ok()
            ts = self._now()
            return [
                Reading(ts, "conductivity", float(conductivity_ref), "mS/cm", quality),
                Reading(ts, "salinity", float(psu), "PSU", quality),
            ]
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(f"read: {exc}")
            ts = self._now()
            return [
                Reading(ts, "conductivity", 0.0, "mS/cm", QUALITY_ERROR),
                Reading(ts, "salinity", 0.0, "PSU", QUALITY_ERROR),
            ]

    def close(self) -> None:
        """No-op; the shared ADC is closed by its owner."""
        return None
