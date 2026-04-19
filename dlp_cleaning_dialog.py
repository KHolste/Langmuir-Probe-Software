"""Modal Cleaning dialog for the DLP V3 main window.

Configures and runs a timed probe-cleaning sweep on the SMU:
* user-editable duration (s), bias voltage (V, strictly negative) and
  compliance current (A);
* live readback of voltage and current;
* visible countdown;
* Start / Stop buttons.

Behaviour during cleaning:
* SMU is forced to GND-referenced output (`set_output_low("GRO")`);
  the previous low-terminal mode is restored on exit.
* On end OR abort the SMU is brought back to a safe state: 0 V and
  output OFF, regardless of which path closed the dialog.
* A short summary lands in the parent window's log.

Simulation mode: the caller passes ``sim_current_a`` (e.g. 0.777 A);
the live current display then shows that value instead of querying
the SMU, while voltage and countdown still run normally.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QElapsedTimer, QTimer, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


log = logging.getLogger(__name__)

#: Sensible per-axis caps so spinboxes don't accept absurd values.
_V_MIN, _V_MAX = -1000.0, 0.0     # only negative bias allowed
_DUR_MIN, _DUR_MAX = 0.1, 3600.0  # seconds
_I_MIN, _I_MAX = 1e-6, 3.0        # amps (B2901 hardware ceiling)

#: How often the live update + countdown ticks.
_TICK_MS = 100


class CleaningDialog(QDialog):
    """Modal probe-cleaning dialog. Blocks the main GUI while running."""

    def __init__(
        self,
        smu,
        *,
        parent=None,
        sim_current_a: Optional[float] = None,
        prev_output_low: str = "GRO",
        duration_s: float = 10.0,
        voltage_v: float = -100.0,
        current_limit_a: float = 0.1,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Probe Cleaning")
        self.setModal(True)
        self.setMinimumWidth(320)

        self._smu = smu
        self._sim_current = (
            float(sim_current_a) if sim_current_a is not None else None)
        self._prev_output_low = str(prev_output_low or "GRO").upper()
        self._running = False
        self._duration_ms = 0
        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)

        # ── Parameters ──────────────────────────────────────────────
        grp_par = QGroupBox("Parameters")
        fl = QFormLayout(grp_par)

        self.spnDuration = QDoubleSpinBox()
        self.spnDuration.setDecimals(2)
        self.spnDuration.setRange(_DUR_MIN, _DUR_MAX)
        self.spnDuration.setSingleStep(1.0)
        self.spnDuration.setSuffix(" s")
        self.spnDuration.setValue(float(duration_s))
        fl.addRow("Duration:", self.spnDuration)

        self.spnVoltage = QDoubleSpinBox()
        self.spnVoltage.setDecimals(2)
        # Only negative bias is accepted — enforced by the spin range.
        self.spnVoltage.setRange(_V_MIN, _V_MAX)
        self.spnVoltage.setSingleStep(1.0)
        self.spnVoltage.setSuffix(" V")
        self.spnVoltage.setValue(float(voltage_v))
        self.spnVoltage.setToolTip(
            "Cleaning bias — must be negative.  The dialog refuses to "
            "start with a value of 0 V or above.")
        fl.addRow("Voltage:", self.spnVoltage)

        self.spnCurrentLimit = QDoubleSpinBox()
        self.spnCurrentLimit.setDecimals(4)
        self.spnCurrentLimit.setRange(_I_MIN, _I_MAX)
        self.spnCurrentLimit.setSingleStep(0.01)
        self.spnCurrentLimit.setSuffix(" A")
        self.spnCurrentLimit.setValue(float(current_limit_a))
        fl.addRow("Current limit:", self.spnCurrentLimit)
        layout.addWidget(grp_par)

        # ── Live readback ───────────────────────────────────────────
        grp_live = QGroupBox("Live")
        fl2 = QFormLayout(grp_live)
        mono = "font-family: Consolas, monospace; font-size: 12pt;"
        self.lblActualV = QLabel("\u2014 V")
        self.lblActualV.setStyleSheet(mono)
        self.lblActualI = QLabel("\u2014 A")
        self.lblActualI.setStyleSheet(mono)
        self.lblCountdown = QLabel("\u2014 s")
        self.lblCountdown.setStyleSheet(mono)
        fl2.addRow("Actual voltage:", self.lblActualV)
        fl2.addRow("Actual current:", self.lblActualI)
        fl2.addRow("Countdown:", self.lblCountdown)
        layout.addWidget(grp_live)

        # ── Action row ──────────────────────────────────────────────
        row = QHBoxLayout()
        self.btnStart = QPushButton("Start cleaning")
        self.btnStart.clicked.connect(self._on_start)
        row.addWidget(self.btnStart)
        self.btnStop = QPushButton("Stop cleaning")
        self.btnStop.setEnabled(False)
        self.btnStop.clicked.connect(self._on_stop)
        row.addWidget(self.btnStop)
        layout.addLayout(row)

        # ── Close button ────────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _log(self, msg: str, level: str = "info") -> None:
        """Forward to the parent window's log if available."""
        try:
            from utils import append_log
            append_log(self.parent(), msg, level)
        except Exception:
            log.info(msg)

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------
    @Slot()
    def _on_start(self) -> None:
        if self._running:
            return
        v = float(self.spnVoltage.value())
        if v >= 0:
            self._log("Cleaning aborted: voltage must be negative.", "warn")
            return
        dur = float(self.spnDuration.value())
        i_lim = float(self.spnCurrentLimit.value())

        # Force GND-referenced output for the cleaning run; previous
        # low-terminal mode is restored in _shutdown().
        try:
            if hasattr(self._smu, "set_output_low"):
                self._smu.set_output_low("GRO")
        except Exception as exc:
            self._log(f"Cleaning: set_output_low failed: {exc}", "warn")

        try:
            self._smu.set_current_limit(i_lim)
            self._smu.set_voltage(v)
            self._smu.output(True)
        except Exception as exc:
            self._log(f"Cleaning start failed: {exc}", "error")
            self._shutdown(reason="failed")
            return

        self._duration_ms = int(dur * 1000)
        self._elapsed.start()
        self._running = True
        self.btnStart.setEnabled(False)
        self.btnStop.setEnabled(True)
        for w in (self.spnDuration, self.spnVoltage, self.spnCurrentLimit):
            w.setEnabled(False)
        self._log(
            f"Cleaning start: V={v:+.2f} V, I_max={i_lim:.4g} A, "
            f"t={dur:.2f} s.", "ok")
        self._timer.start(_TICK_MS)
        self._tick()

    @Slot()
    def _on_stop(self) -> None:
        if not self._running:
            return
        self._shutdown(reason="aborted")

    @Slot()
    def _tick(self) -> None:
        if not self._running:
            return
        elapsed_ms = self._elapsed.elapsed()
        remaining_s = max(0.0, (self._duration_ms - elapsed_ms) / 1000.0)
        self.lblCountdown.setText(f"{remaining_s:.1f} s")

        try:
            v = float(self._smu.read_voltage())
            self.lblActualV.setText(f"{v:+.4f} V")
        except Exception:
            self.lblActualV.setText("ERR")
        try:
            if self._sim_current is not None:
                i = self._sim_current
            else:
                i = float(self._smu.read_current())
            self.lblActualI.setText(f"{i:+.4f} A")
        except Exception:
            self.lblActualI.setText("ERR")

        if remaining_s <= 0.0:
            self._shutdown(reason="finished")

    def _shutdown(self, *, reason: str) -> None:
        """Bring the SMU to a safe state and reset the dialog UI."""
        self._timer.stop()
        was_running = self._running
        self._running = False

        # Voltage to zero, output off — always.
        try:
            self._smu.set_voltage(0.0)
        except Exception as exc:
            self._log(f"Cleaning shutdown: set_voltage(0) failed: {exc}",
                      "warn")
        try:
            self._smu.output(False)
        except Exception as exc:
            self._log(f"Cleaning shutdown: output(False) failed: {exc}",
                      "warn")
        # Restore the prior low-terminal mode if the SMU supports it.
        try:
            if (hasattr(self._smu, "set_output_low")
                    and self._prev_output_low
                    and self._prev_output_low != "GRO"):
                self._smu.set_output_low(self._prev_output_low)
        except Exception as exc:
            self._log(f"Cleaning shutdown: set_output_low restore "
                      f"failed: {exc}", "warn")

        self.btnStart.setEnabled(True)
        self.btnStop.setEnabled(False)
        for w in (self.spnDuration, self.spnVoltage, self.spnCurrentLimit):
            w.setEnabled(True)
        self.lblCountdown.setText("0.0 s")
        if was_running:
            self._log(f"Cleaning {reason}.",
                      "ok" if reason == "finished" else "warn")

    # ------------------------------------------------------------------
    # Qt overrides — force shutdown if the user closes the dialog
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._running:
            self._shutdown(reason="aborted")
        super().closeEvent(event)

    def reject(self):
        if self._running:
            self._shutdown(reason="aborted")
        super().reject()
