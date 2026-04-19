"""Fake Keithley 2000 DMM for tests + sim mode of DLP V3.

Mirrors the public surface of :class:`keithley_2000.Keithley2000DMM`
(``connect`` / ``close`` / ``idn`` / ``read_voltage`` / setters) so the
GUI and tests can swap between real hardware and this fake by changing
nothing more than the constructor.

The fake returns a configurable constant voltage with optional Gaussian
noise.  Default voltage is **0.6 V** so the bench-acceptance test
("we expect roughly 0.6 V on this rig") can be reproduced offline.
"""
from __future__ import annotations

import random
from typing import Optional


class FakeKeithley2000:
    """In-memory K2000 stand-in.  No hardware, no PyVISA."""

    DEFAULT_IDN = "KEITHLEY INSTRUMENTS,MODEL 2000,FAKE-0001,A02 /A02"

    def __init__(
        self,
        *,
        voltage: float = 0.6,
        noise_std: float = 0.0,
        seed: int | None = None,
        idn: str | None = None,
    ) -> None:
        self.target_voltage = float(voltage)
        self.noise_std = max(0.0, float(noise_std))
        self._rng = random.Random(seed)
        self.idn_str = idn or self.DEFAULT_IDN
        self.is_connected = False
        self.v_range: Optional[float] = None  # None = autorange
        self.nplc: float = 1.0

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self) -> str:
        self.is_connected = True
        return self.idn_str

    def close(self) -> None:
        self.is_connected = False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def idn(self) -> str:
        self._require_connected()
        return self.idn_str

    def read_voltage(self) -> float:
        self._require_connected()
        if self.noise_std > 0:
            return self.target_voltage + self._rng.gauss(0.0, self.noise_std)
        return self.target_voltage

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def set_voltage_range(self, v_range: Optional[float]) -> None:
        if v_range is not None:
            r = float(v_range)
            if r <= 0:
                raise ValueError(f"voltage range must be > 0 (got {r!r})")
            self.v_range = r
        else:
            self.v_range = None

    def set_nplc(self, nplc: float) -> None:
        n = float(nplc)
        if n <= 0:
            raise ValueError(f"nplc must be > 0 (got {n!r})")
        self.nplc = n

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------
    def set_voltage_for_test(self, voltage: float) -> None:
        """Adjust the fake target voltage at runtime (test convenience)."""
        self.target_voltage = float(voltage)

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError("Not connected — call connect() first")
