"""Triple-Langmuir-probe measurement worker (Phase 2).

A small ``QObject`` that drives a periodic SMU + K2000 read sequence,
runs the per-tick numbers through :mod:`dlp_triple_analysis`, and
emits one structured ``sample`` payload per tick.  Independent of any
GUI — the future Triple-Probe window will only listen to its signals.

Wiring contract:
* SMU is the floating source: provides ``V_d12`` (constant bias) and
  ``I_measure``.
* K2000 measures ``V_d13`` differentially between the floating tip
  (Pin 3) and the SMU positive terminal (Pin 1).

Sign convention for ``V_d13`` is **explicit**: pass ``v_d13_sign``
(+1 or −1) at construction time depending on how the K2000 leads are
physically attached.  No auto-detection.

Lifecycle:
1. ``start()``  – snapshot previous output-low mode, force FLO,
                   write compliance + bias, enable output, start tick.
2. tick loop    – read SMU current, K2000 voltage, optional SMU
                   voltage readback; run analysis; emit ``sample``.
3. ``request_stop()`` (or ``failed``) – stop tick, V→0, output OFF,
                   restore previous output-low mode, emit ``stopped``
                   or ``failed`` accordingly.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from dlp_triple_analysis import (
    DEFAULT_AREA_M2,
    DEFAULT_SPECIES,
    analyze_sample,
    mi_from_species,
)


log = logging.getLogger(__name__)


#: Default tick rate (ms).  Pragmatic: SMU + K2000 sequential reads
#: take ~100–150 ms together at NPLC 1; 250 ms is comfortable.
DEFAULT_TICK_MS = 250


class TripleProbeWorker(QObject):
    """Periodic Triple-Probe sampler.  GUI-free."""

    started = Signal()
    sample = Signal(dict)
    stopped = Signal(str)       # reason: "user" | "finished"
    failed = Signal(str)        # human-readable failure message

    def __init__(
        self,
        smu,
        k2000,
        *,
        v_d12_setpoint: float,
        current_limit_a: float,
        area_m2: float = DEFAULT_AREA_M2,
        mi_kg: Optional[float] = None,
        species_name: str = DEFAULT_SPECIES,
        mi_rel_unc: float = 0.0,
        v_d13_sign: int = +1,
        prefer_eq10: bool = True,
        tick_ms: int = DEFAULT_TICK_MS,
        prev_output_low: str = "GRO",
        sim_current_a: Optional[float] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        # Default parent = None: a parented worker would stay alive as
        # a Qt child of the LP window even after ``_worker = None``,
        # which used to leak a fresh QTimer per start/stop cycle and
        # contributed to sluggish restarts.  Callers can still pass an
        # explicit parent if they want Qt-tree ownership.
        super().__init__(parent)
        if v_d12_setpoint <= 0:
            raise ValueError(
                f"V_d12 must be positive (got {v_d12_setpoint!r})")
        if v_d13_sign not in (-1, +1):
            raise ValueError(
                f"v_d13_sign must be +1 or -1 (got {v_d13_sign!r})")
        if current_limit_a <= 0:
            raise ValueError(
                f"current_limit_a must be > 0 (got {current_limit_a!r})")

        self._smu = smu
        self._k2000 = k2000
        self._v_d12 = float(v_d12_setpoint)
        self._i_lim = float(current_limit_a)
        self._area = float(area_m2)
        self._mi = float(mi_kg) if mi_kg is not None else mi_from_species(
            species_name)
        self._species = species_name
        # Ion-mass relative uncertainty (from the shared ion-composition
        # context): feeds the mass-only n_e CI emitted per tick.  Zero
        # means "no uncertainty propagated" — the sample payload will
        # still carry CI fields, but method="unavailable".
        try:
            self._mi_rel_unc = max(0.0, float(mi_rel_unc or 0.0))
        except (TypeError, ValueError):
            self._mi_rel_unc = 0.0
        self._sign = int(v_d13_sign)
        # Optional Triple-Probe-current override for the simulation
        # path: when set, the SMU current readback is bypassed and
        # this constant is used instead.  Lets the LP main window
        # produce sign-correct, magnitude-plausible Te / n_e values
        # in sim mode without touching the real measurement chain.
        self._sim_current_a: Optional[float] = (
            float(sim_current_a) if sim_current_a is not None else None)
        self._prefer_eq10 = bool(prefer_eq10)
        self._tick_ms = max(20, int(tick_ms))
        self._prev_output_low = str(prev_output_low or "GRO").upper()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._t0: float = 0.0
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @Slot()
    def start(self) -> None:
        """Bring the SMU into a Triple-Probe-ready state and start ticking."""
        if self._running:
            return
        try:
            if hasattr(self._smu, "set_output_low"):
                # Triple-Probe REQUIRES floating output; the previous
                # mode is restored in _shutdown().
                self._smu.set_output_low("FLO")
            self._smu.set_current_limit(self._i_lim)
            self._smu.set_voltage(self._v_d12)
            self._smu.output(True)
        except Exception as exc:
            self.failed.emit(f"Triple-probe start failed: {exc}")
            self._safe_restore()
            return

        self._t0 = time.perf_counter()
        self._running = True
        self._timer.start(self._tick_ms)
        self.started.emit()
        # Drive one immediate tick so the GUI/CSV gets a value before
        # the first timer interval elapses.
        self._tick()

    @Slot()
    def request_stop(self) -> None:
        if not self._running:
            return
        self._shutdown(reason="user")

    @Slot()
    def _tick(self) -> None:
        if not self._running:
            return
        if self._sim_current_a is not None:
            i_a = self._sim_current_a
        else:
            try:
                i_a = float(self._smu.read_current())
            except Exception as exc:
                self.failed.emit(f"Triple-probe SMU read failed: {exc}")
                self._shutdown(reason="failed")
                return
        try:
            u_meas = float(self._k2000.read_voltage())
        except Exception as exc:
            self.failed.emit(f"Triple-probe K2000 read failed: {exc}")
            self._shutdown(reason="failed")
            return
        # Optional SMU voltage readback (some users like to log the
        # actual delivered V_d12; not strictly needed for the model).
        v_d12_actual = self._v_d12
        try:
            v_d12_actual = float(self._smu.read_voltage())
        except Exception:
            pass

        v_d13 = self._sign * u_meas
        analysis = analyze_sample(
            v_d12=v_d12_actual,
            v_d13=v_d13,
            i_measure_a=i_a,
            area_m2=self._area,
            mi_kg=self._mi,
            prefer_eq10=self._prefer_eq10,
            mi_rel_unc=self._mi_rel_unc,
        )
        sample = {
            "t_rel_s": time.perf_counter() - self._t0,
            "v_d12_setpoint": self._v_d12,
            "v_d12_actual": v_d12_actual,
            "u_meas_v": u_meas,
            "v_d13": v_d13,
            "i_a": i_a,
            "Te_eV": analysis["Te_eV"],
            "n_e_m3": analysis["n_e_m3"],
            "species": self._species,
            "area_m2": self._area,
            "mi_kg": self._mi,
            "mi_rel_unc": self._mi_rel_unc,
            "ne_ci95_lo_m3": analysis.get("ne_ci95_lo_m3"),
            "ne_ci95_hi_m3": analysis.get("ne_ci95_hi_m3"),
            "ne_ci_method": analysis.get("ne_ci_method", "unavailable"),
            "ne_ci_note": analysis.get("ne_ci_note", "fit_only"),
        }
        self.sample.emit(sample)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def _shutdown(self, *, reason: str) -> None:
        self._timer.stop()
        was_running = self._running
        self._running = False
        self._safe_restore()
        if was_running:
            self.stopped.emit(reason)

    def _safe_restore(self) -> None:
        """Bring the SMU back to a safe state.  Each step is wrapped
        defensively — a single SCPI hiccup must not strand the
        instrument with output ON."""
        try:
            self._smu.set_voltage(0.0)
        except Exception as exc:
            log.warning("triple worker: set_voltage(0) failed: %s", exc)
        try:
            self._smu.output(False)
        except Exception as exc:
            log.warning("triple worker: output(False) failed: %s", exc)
        # Restore prior output-low only when it differs from FLO.
        # Skipping the no-op restore avoids a multi-second SMU
        # reconfiguration on real hardware that turned a quick
        # Stop → Start round-trip into a 5-10 s pause.
        try:
            prev = (self._prev_output_low or "").upper()
            if (hasattr(self._smu, "set_output_low")
                    and prev
                    and prev not in ("", "FLO")):
                self._smu.set_output_low(prev)
        except Exception as exc:
            log.warning("triple worker: set_output_low restore failed: %s",
                        exc)
