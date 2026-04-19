"""
FakeB2901 – Software simulation of the Keysight B2901 SMU.

Drop-in replacement for KeysightB2901PSU (same public method signatures).
Produces a deterministic tanh-shaped I-V curve suitable for
Double-Langmuir-Probe development and testing without hardware.

Failure injection
-----------------
Pass ``fail_after`` to raise after *N* calls to instrumented methods
(``connect``, ``set_voltage``, ``read_voltage``, ``read_current``,
``output``).  Optionally restrict to a single method with ``fail_on``.
The fault fires **once** – subsequent calls succeed so the worker's
``finally`` cleanup can still run.
"""
from __future__ import annotations

import math
import random


class SimulatedTimeout(Exception):
    """Raised by FakeB2901 to simulate a VISA timeout."""


def _visa_timeout_class() -> type[Exception]:
    """Return pyvisa's VisaIOError if available, else SimulatedTimeout."""
    try:
        from pyvisa.errors import VisaIOError
        return VisaIOError
    except ImportError:
        return SimulatedTimeout


def make_visa_timeout(msg: str = "VI_ERROR_TMO") -> Exception:
    """Create a realistic timeout exception using pyvisa if available."""
    try:
        from pyvisa import constants, errors
        return errors.VisaIOError(constants.StatusCode.error_timeout, msg)
    except (ImportError, Exception):
        return SimulatedTimeout(msg)


class FakeB2901:
    """Simulated B2901 SMU with tanh I-V characteristic."""

    IDN = "Keysight Technologies,B2901A,SIM00001,1.0.0 (SIMULATED)"

    def __init__(
        self,
        *,
        model: str = "tanh",
        i_sat: float = 2.0e-3,
        te_eV: float = 3.0,
        resistance: float = 1000.0,
        noise_std: float = 0.0,
        seed: int | None = None,
        settle_delay: float = 0.0,
        current_compliance: float | None = None,
        fail_after: int | None = None,
        fail_on: str | None = None,
        fail_exc: type[Exception] | None = None,
        visa_timeout: bool = False,
        **_kw,                          # absorb extra kwargs from GUI
    ) -> None:
        if model not in ("tanh", "resistor"):
            raise ValueError(f"Unknown model {model!r}, use 'tanh' or 'resistor'")
        self.model = model
        self.i_sat = i_sat
        self.te_eV = te_eV
        self.resistance = resistance
        self.noise_std = noise_std
        self.settle_delay = settle_delay
        self.current_compliance = current_compliance or 0.1
        self._in_compliance = False

        # failure injection state
        self._fail_after = fail_after
        self._fail_on = fail_on
        self._visa_timeout = visa_timeout
        self._fail_exc = fail_exc or (
            _visa_timeout_class() if visa_timeout else SimulatedTimeout
        )
        self._call_count = 0
        self._fail_fired = False

        self._rng = random.Random(seed)
        self._voltage = 0.0
        self._output_on = False
        self._connected = False
        # Output-low recording lets tests against this fake assert
        # FLO/GRO sequencing without resorting to a MagicMock SMU.
        self.output_low: str = "GRO"
        self.output_low_history: list[str] = []

    # ── failure injection ─────────────────────────────────────────

    def _maybe_fail(self, method_name: str) -> None:
        """Raise if failure injection is armed and conditions are met."""
        if self._fail_after is None or self._fail_fired:
            return
        if self._fail_on is not None and self._fail_on != method_name:
            return
        self._call_count += 1
        if self._call_count > self._fail_after:
            self._fail_fired = True
            msg = (f"Simulated failure in {method_name} "
                   f"(after {self._fail_after} calls)")
            if self._visa_timeout:
                raise make_visa_timeout(msg)
            raise self._fail_exc(msg)

    # ── connection ────────────────────────────────────────────────

    def connect(self) -> str:
        self._maybe_fail("connect")
        self._connected = True
        return self.IDN

    def close(self) -> None:
        if self._output_on:
            self.output(False)
        self._connected = False

    # ── queries ───────────────────────────────────────────────────

    def idn(self) -> str:
        return self.IDN

    def read_voltage(self) -> float:
        self._maybe_fail("read_voltage")
        return self._voltage

    def read_current(self) -> float:
        """Return simulated probe current for the present voltage."""
        self._maybe_fail("read_current")
        if not self._output_on:
            self._in_compliance = False
            return 0.0
        if self.model == "resistor":
            i = self._voltage / self.resistance
        else:
            arg = self._voltage / (2.0 * self.te_eV) * 11604.52
            arg = max(-50.0, min(50.0, arg))
            i = self.i_sat * math.tanh(arg)
        if self.noise_std > 0:
            i += self._rng.gauss(0, self.noise_std)
        self._in_compliance = abs(i) > self.current_compliance
        if self._in_compliance:
            i = math.copysign(self.current_compliance, i)
        return i

    def is_in_compliance(self) -> bool:
        """Return True if the last current reading was compliance-limited."""
        return self._in_compliance

    # ── control ───────────────────────────────────────────────────

    def set_voltage(self, value: float) -> None:
        self._maybe_fail("set_voltage")
        self._voltage = value

    def set_current_limit(self, value: float) -> None:
        self.current_compliance = value

    def output(self, enable: bool) -> None:
        self._maybe_fail("output")
        self._output_on = enable
        if not enable:
            self._voltage = 0.0

    def set_nplc(self, nplc: float) -> None:
        pass  # no-op in simulation

    def set_output_low(self, mode: str) -> None:
        """Mirror the real driver surface so worker / dialog FLO/GRO
        restore paths can be verified against this fake instead of a
        MagicMock.  The mode is recorded in ``output_low_history`` so
        tests can assert ordering."""
        m = str(mode).upper()
        if m not in ("GRO", "FLO"):
            raise ValueError(
                f"output-low mode must be 'GRO' or 'FLO', got {mode!r}")
        self.output_low = m
        self.output_low_history.append(m)

    # ── state inspection (for tests) ──────────────────────────────

    @property
    def is_output_on(self) -> bool:
        return self._output_on

    @property
    def voltage(self) -> float:
        return self._voltage
