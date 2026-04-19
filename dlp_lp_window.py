"""Langmuir-probe measurement window — generalised successor of the
Triple-Probe-only ``dlp_triple_window``.  The same module will be
extended to host single- and double-probe modes in later iterations,
which is why the file is no longer suffixed ``v4`` / ``triple``.

Highlights of this revision:
* dark-themed plot area (matches the rest of the GUI),
* two stacked subplots — Te on top, n_e below,
* dedicated Plot Settings dialog (Auto / Manual range per axis),
* auto-save option with visible + editable target path.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from dlp_triple_analysis import (
    DEFAULT_AREA_M2,
    DEFAULT_SPECIES,
    SPECIES_AMU,
)
from dlp_triple_dataset import TripleDataset, make_triple_csv_path
from dlp_triple_worker import DEFAULT_TICK_MS, TripleProbeWorker
from utils import append_log


log = logging.getLogger(__name__)


class LPMeasurementWindow(QWidget):
    """Non-modal Langmuir-probe live window (currently Triple mode)."""

    running_changed = Signal(bool)

    def __init__(self, smu, k2000, parent=None, *,
                 sim_current_a: Optional[float] = None,
                 gas_mix_label: Optional[str] = None,
                 mi_kg: Optional[float] = None,
                 area_m2: Optional[float] = None):
        super().__init__(parent)
        self.setWindowTitle("Langmuir Probe Measurement")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(940, 620)

        # Pull the dark theme from the V1 main window if the parent
        # carries one; otherwise fall back to a neutral dark palette.
        self._theme = self._resolve_theme(parent)

        self._smu = smu
        self._k2000 = k2000
        # When set, the worker uses this fixed current instead of the
        # SMU readback — used by the LP main window in simulation mode
        # so the Triple-Probe analysis produces sign-correct demo
        # values (Te ≈ 4 eV, n_e ≈ 1e17 m⁻³).
        self._sim_current_a = sim_current_a
        # Gas-mix context provided by the LP main window (taken from
        # the Experiment dialog).  ``gas_mix_label`` is the human
        # text shown next to the live readout; ``mi_kg`` is the
        # effective ion mass forwarded to the worker.  Both are
        # optional — Argon is the safe default.
        self._gas_mix_label = gas_mix_label
        self._mi_kg_override = mi_kg
        # Probe area is owned by the main window's Probe Params…
        # dialog; the LP window only displays it.  Falls back to
        # the documented default until the main window injects the
        # current value.
        self._area_m2 = float(area_m2) if area_m2 is not None \
            else float(DEFAULT_AREA_M2)
        self._dataset = TripleDataset()
        self._worker: Optional[TripleProbeWorker] = None
        self._te_t: list[float] = []
        self._te_v: list[float] = []
        self._ne_t: list[float] = []
        self._ne_v: list[float] = []

        self._build_ui()
        self._refresh_button_state()

    @staticmethod
    def _resolve_theme(parent) -> dict:
        """Inherit the parent's matplotlib palette if available so the
        plot blends seamlessly into the rest of the GUI."""
        for src in (parent, getattr(parent, "_theme", None)):
            t = src._theme if hasattr(src, "_theme") else src
            if isinstance(t, dict) and "plot_bg" in t:
                return t
        return {
            "plot_bg":   "#10131a",
            "plot_fg":   "#d0d4e0",
            "plot_grid": "#2e354a",
            "plot_fig":  "#161a24",
        }

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)

        # ── left column ────────────────────────────────────────────
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(10)

        # Parameters
        grp_par = QGroupBox("Parameters")
        fl = QFormLayout(grp_par)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(6)

        self.spnVd12 = QDoubleSpinBox()
        self.spnVd12.setRange(0.1, 1000.0); self.spnVd12.setDecimals(2)
        self.spnVd12.setValue(25.0); self.spnVd12.setSuffix(" V")
        fl.addRow("V_d12 (bias):", self.spnVd12)

        self.spnCompliance = QDoubleSpinBox()
        self.spnCompliance.setRange(1e-6, 3.0); self.spnCompliance.setDecimals(4)
        self.spnCompliance.setSingleStep(0.001)
        self.spnCompliance.setValue(0.01); self.spnCompliance.setSuffix(" A")
        fl.addRow("Compliance:", self.spnCompliance)

        # Probe area — read-only display.  The single source of truth
        # is the main window's Probe Params… dialog; the LP window
        # only mirrors the value here.
        self.lblArea = QLabel(self._format_area_label(self._area_m2))
        self.lblArea.setToolTip(
            "Probe area derived from Probe Params… in the main window. "
            "Edit it there — this field is read-only here.")
        self.lblArea.setStyleSheet(
            "color: #c0c8d8; font-style: italic;")
        fl.addRow("Probe area:", self.lblArea)

        # Gas mix is now read from the parent's Experiment dialog
        # (Process gas types) so a real mixture is honoured by the
        # Triple analysis.  The label is read-only — change the mix
        # via the main window's Experiment… button.
        self.lblGasMix = QLabel(self._gas_mix_label or "Argon (Ar)")
        self.lblGasMix.setToolTip(
            "Gas mix used by the Triple-Probe analysis.\n"
            "Edit via the main window's Experiment\u2026 button.")
        self.lblGasMix.setStyleSheet(
            "color: #c0c8d8; font-style: italic;")
        fl.addRow("Gas mix:", self.lblGasMix)

        self.cmbSign = QComboBox()
        self.cmbSign.addItem("+1  (V_d13 = +U_K2000)", +1)
        self.cmbSign.addItem("-1  (V_d13 = -U_K2000)", -1)
        fl.addRow("V_d13 sign:", self.cmbSign)

        # Formula choice — user-friendly labels for what used to be
        # "Use exact Eq-10 (fallback Eq-11)".  Default is the fast
        # closed-form approximation; Exact triggers the bisection
        # solver with the closed form as a fallback.
        self.cmbEqMode = QComboBox()
        self.cmbEqMode.addItem("Approx.", False)   # prefer_eq10 = False
        self.cmbEqMode.addItem("Exact", True)      # prefer_eq10 = True
        self.cmbEqMode.setCurrentIndex(0)          # default: Approx.
        self.cmbEqMode.setToolTip(
            "Te formula:\n"
            "  Approx. — closed-form Te = V_d13 / ln 2 (fast, robust).\n"
            "  Exact   — implicit triple-probe equation, solved "
            "numerically (falls back to Approx. on failure).")
        fl.addRow("Formula:", self.cmbEqMode)

        self.spnTick = QSpinBox()
        self.spnTick.setRange(50, 5000); self.spnTick.setValue(DEFAULT_TICK_MS)
        self.spnTick.setSuffix(" ms")
        fl.addRow("Tick:", self.spnTick)
        lv.addWidget(grp_par)

        # Live readout
        grp_live = QGroupBox("Live")
        fl2 = QFormLayout(grp_live)
        fl2.setContentsMargins(8, 8, 8, 8); fl2.setSpacing(4)
        mono_small = ("font-family: Consolas, monospace; font-size: 10pt; "
                       "color: #aab0c0;")
        mono_big = ("font-family: Consolas, monospace; font-size: 13pt; "
                     "font-weight: bold;")
        self.lblT = QLabel("\u2014 s"); self.lblT.setStyleSheet(mono_small)
        self.lblUsupply = QLabel("\u2014 V"); self.lblUsupply.setStyleSheet(mono_small)
        self.lblUmeas = QLabel("\u2014 V"); self.lblUmeas.setStyleSheet(mono_small)
        self.lblImeas = QLabel("\u2014 A"); self.lblImeas.setStyleSheet(mono_small)
        self.lblTe = QLabel("\u2014 eV")
        self.lblTe.setStyleSheet(mono_big + " color: #4f9dff;")
        self.lblNe = QLabel("\u2014 m\u207B\u00B3")
        self.lblNe.setStyleSheet(mono_big + " color: #f0a040;")
        self.lblSamples = QLabel("0"); self.lblSamples.setStyleSheet(mono_small)
        fl2.addRow("Time:", self.lblT)
        fl2.addRow("U_supply:", self.lblUsupply)
        fl2.addRow("U_measure:", self.lblUmeas)
        fl2.addRow("I_measure:", self.lblImeas)
        fl2.addRow("Te:", self.lblTe)
        fl2.addRow("n_e:", self.lblNe)
        fl2.addRow("Samples:", self.lblSamples)
        lv.addWidget(grp_live)

        # Auto-save group: checkbox + visible path + browse button.
        grp_save = QGroupBox("Auto-save CSV")
        sv = QVBoxLayout(grp_save)
        sv.setContentsMargins(8, 8, 8, 8); sv.setSpacing(4)
        self.chkAutoSave = QCheckBox("Auto-save on stop")
        self.chkAutoSave.setChecked(False)
        sv.addWidget(self.chkAutoSave)
        row_path = QHBoxLayout()
        self.editAutoSavePath = QLineEdit(str(self._default_autosave_path()))
        self.editAutoSavePath.setToolTip(
            "Target file for auto-save.  Edit directly or use Browse.")
        row_path.addWidget(self.editAutoSavePath, 1)
        self.btnBrowse = QPushButton("Browse\u2026")
        self.btnBrowse.setMaximumWidth(80)
        self.btnBrowse.clicked.connect(self._on_browse_autosave)
        row_path.addWidget(self.btnBrowse)
        sv.addLayout(row_path)
        lv.addWidget(grp_save)

        # Primary action row.
        row_primary = QHBoxLayout(); row_primary.setSpacing(8)
        self.btnStart = QPushButton("Start")
        self.btnStart.setMinimumHeight(34)
        self.btnStart.setStyleSheet("QPushButton { font-weight: bold; }")
        self.btnStart.clicked.connect(self._on_start)
        row_primary.addWidget(self.btnStart, 1)
        self.btnStop = QPushButton("Stop")
        self.btnStop.setMinimumHeight(34); self.btnStop.setEnabled(False)
        self.btnStop.clicked.connect(self._on_stop)
        row_primary.addWidget(self.btnStop, 1)
        lv.addLayout(row_primary)

        # Secondary action row + plot settings.
        row_secondary = QHBoxLayout(); row_secondary.setSpacing(8)
        self.btnSave = QPushButton("Save CSV\u2026")
        self.btnSave.setMinimumHeight(26); self.btnSave.setEnabled(False)
        self.btnSave.clicked.connect(self._on_save_csv)
        row_secondary.addWidget(self.btnSave, 1)
        self.btnClear = QPushButton("Clear Plot")
        self.btnClear.setMinimumHeight(26)
        self.btnClear.setToolTip(
            "Clear the live plot.  Safe to use during a running "
            "measurement — only the visible curves are reset, the "
            "underlying dataset and the worker keep going.")
        self.btnClear.clicked.connect(self._on_clear)
        row_secondary.addWidget(self.btnClear, 1)
        self.btnPlotSettings = QPushButton("Plot\u2026")
        self.btnPlotSettings.setMinimumHeight(26)
        self.btnPlotSettings.setMaximumWidth(80)
        self.btnPlotSettings.clicked.connect(self._on_plot_settings)
        row_secondary.addWidget(self.btnPlotSettings)
        # Help button — opens the Triple-probe documentation dialog
        # via :mod:`dlp_triple_help`.  Parallel to Single / Double
        # help dialogs so operators have one entry point per method.
        self.btnHelp = QPushButton("Help\u2026")
        self.btnHelp.setMinimumHeight(26)
        self.btnHelp.setMaximumWidth(70)
        self.btnHelp.setToolTip(
            "Open the Triple-probe analysis help dialog: "
            "method description, assumptions, parameter meanings, "
            "and common-failure checklist.")
        self.btnHelp.clicked.connect(self._on_open_help)
        row_secondary.addWidget(self.btnHelp)
        lv.addLayout(row_secondary)

        self.lblStatus = QLabel("Idle.")
        self.lblStatus.setMinimumHeight(28); self.lblStatus.setWordWrap(True)
        self.lblStatus.setStyleSheet(
            "QLabel { color: #c0c8d8; font-size: 10pt; "
            "padding: 4px 6px; border: 1px solid #3a4150; "
            "border-radius: 3px; background: #232830; }")
        lv.addWidget(self.lblStatus)
        lv.addStretch(1)
        split.addWidget(left)

        # ── right column: two stacked plots, dark themed ───────────
        plot_w = QWidget()
        pv = QVBoxLayout(plot_w); pv.setContentsMargins(0, 0, 0, 0)
        self._fig = Figure(figsize=(5, 5))
        self._canvas = FigureCanvasQTAgg(self._fig)
        # 2 rows × 1 col, shared X axis (time).
        self._ax_te = self._fig.add_subplot(2, 1, 1)
        self._ax_ne = self._fig.add_subplot(2, 1, 2, sharex=self._ax_te)
        self._ax_ne.set_yscale("log")
        self._ax_te.set_ylabel("Te (eV)")
        self._ax_ne.set_ylabel("n_e (m⁻³)")
        self._ax_ne.set_xlabel("t (s)")
        (self._line_te,) = self._ax_te.plot(
            [], [], "o-", markersize=3, color="#4f9dff", label="Te")
        (self._line_ne,) = self._ax_ne.plot(
            [], [], "s-", markersize=3, color="#f0a040", label="n_e")
        self._apply_plot_theme()
        self._fig.tight_layout()
        pv.addWidget(self._canvas)
        split.addWidget(plot_w)
        # Legacy aliases — older test suites refer to ``_ax`` / ``_ax2``
        # from the previous Triple-only window; keep them pointing at
        # the corresponding new axes so nothing has to be rewritten.
        self._ax = self._ax_te
        self._ax2 = self._ax_ne

        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([330, 600])

    def _apply_plot_theme(self) -> None:
        """Take figure + both axes into the dark color palette."""
        from utils import apply_clean_axis_format
        t = self._theme
        self._fig.set_facecolor(t["plot_fig"])
        for ax in (self._ax_te, self._ax_ne):
            apply_clean_axis_format(ax)
            ax.set_facecolor(t["plot_bg"])
            for spine in ax.spines.values():
                spine.set_color(t["plot_fg"])
            ax.tick_params(colors=t["plot_fg"])
            ax.xaxis.label.set_color(t["plot_fg"])
            ax.yaxis.label.set_color(t["plot_fg"])
            ax.grid(True, color=t["plot_grid"], alpha=0.5)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _default_autosave_path(self) -> Path:
        try:
            from paths import lp_measurements_data_dir
            base = lp_measurements_data_dir()
        except Exception:
            base = Path.cwd()
        return make_triple_csv_path(base)

    def _log(self, msg: str, level: str = "info") -> None:
        try:
            append_log(self.parent(), msg, level)
        except Exception:
            log.info(msg)

    def _refresh_button_state(self) -> None:
        running = self._worker is not None and self._worker.is_running
        self.btnStart.setEnabled(not running)
        self.btnStop.setEnabled(running)
        self.btnSave.setEnabled(len(self._dataset) > 0 and not running)
        # Clear Plot is *always* available — during a run it only
        # resets the visible curves; the dataset keeps growing.
        self.btnClear.setEnabled(True)
        for w in (self.spnVd12, self.spnCompliance,
                  self.cmbSign, self.cmbEqMode,
                  self.spnTick, self.editAutoSavePath, self.btnBrowse,
                  self.chkAutoSave):
            w.setEnabled(not running)

    # ------------------------------------------------------------------
    # Browse / Plot Settings
    # ------------------------------------------------------------------
    @Slot()
    def _on_browse_autosave(self) -> None:
        current = self.editAutoSavePath.text().strip() or str(
            self._default_autosave_path())
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Choose auto-save CSV path", current, "CSV (*.csv)")
        if chosen:
            self.editAutoSavePath.setText(chosen)

    @Slot()
    def _on_plot_settings(self) -> None:
        from dlp_lp_plot_settings_dialog import LPPlotSettingsDialog
        dlg = LPPlotSettingsDialog(self._ax_te, self._ax_ne, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                dlg.apply_to_axes()
                self._canvas.draw_idle()
            except Exception as exc:
                self._log(f"Plot settings apply failed: {exc}", "warn")

    @Slot()
    def _on_open_help(self) -> None:
        """Open the Triple-probe help documentation dialog.

        Lazy import keeps this module import-cheap when the help
        module is not needed (e.g. headless tests).  Failures are
        logged as warnings rather than raised so the measurement
        window stays usable even if the help module breaks at
        import time.
        """
        try:
            from dlp_triple_help import open_triple_help_dialog
        except Exception as exc:  # pragma: no cover - defensive
            self._log(
                f"Triple help unavailable: "
                f"{type(exc).__name__}: {exc}", "warn")
            return
        open_triple_help_dialog(parent=self)

    # ------------------------------------------------------------------
    # Start / Stop / Sample handling — same flow as the predecessor
    # ------------------------------------------------------------------
    @Slot()
    def _on_start(self) -> None:
        if self._smu is None or self._k2000 is None:
            self._log("LP aborted: SMU and K2000 must be connected.", "warn")
            self.lblStatus.setText("SMU and K2000 must be connected.")
            return
        # Defensive: a previous worker reference may still hang around
        # if a prior teardown was interrupted.  Disconnect + delete it
        # explicitly before we build a fresh one — keeps Stop → Start
        # latency low and prevents accumulation of QTimer + signal
        # connections across runs.
        self._teardown_worker()
        # Visual fresh-start: clear the plot buffers + live readouts
        # before the new worker fires its first sample.  Without this
        # the very first restart-tick would call relim/autoscale on a
        # line that still carries the entire history of the previous
        # run (small new t_rel_s vs. large old t_s), forcing
        # matplotlib into an expensive full rebuild that took several
        # seconds in practice.  The dataset is intentionally NOT
        # touched — Save CSV after Stop+Restart still keeps the full
        # history.
        self._reset_plot_for_new_run()
        prev_low = "GRO"
        try:
            opts = getattr(self.parent(), "_instrument_opts", None)
            if isinstance(opts, dict):
                prev_low = str(opts.get("output_low", "GRO")).upper()
        except Exception:
            pass

        # Resolve species + ion mass from the gas mix the LP main
        # window injected (taken from the Experiment dialog).
        species = self._gas_mix_label or "Argon (Ar)"
        area_m2 = float(self._area_m2)
        try:
            self._worker = TripleProbeWorker(
                self._smu, self._k2000,
                v_d12_setpoint=float(self.spnVd12.value()),
                current_limit_a=float(self.spnCompliance.value()),
                area_m2=area_m2,
                species_name=species,
                mi_kg=self._mi_kg_override,
                v_d13_sign=int(self.cmbSign.currentData()),
                prefer_eq10=bool(self.cmbEqMode.currentData()),
                tick_ms=int(self.spnTick.value()),
                prev_output_low=prev_low,
                sim_current_a=self._sim_current_a,
                # parent=None on purpose — see TripleProbeWorker docs.
            )
        except Exception as exc:
            self._log(f"LP worker init failed: {exc}", "error")
            return

        self._worker.started.connect(self._on_worker_started)
        self._worker.sample.connect(self._on_worker_sample)
        self._worker.stopped.connect(self._on_worker_stopped)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    @Slot()
    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()

    @Slot()
    def _on_worker_started(self) -> None:
        self.lblStatus.setText("Running\u2026")
        self._refresh_button_state()
        self.running_changed.emit(True)
        self._log(
            f"LP started: V_d12={self.spnVd12.value():.2f} V, "
            f"compl={self.spnCompliance.value():.4g} A, "
            f"gas={self._gas_mix_label or 'Argon (Ar)'}.", "ok")

    @Slot(dict)
    def _on_worker_sample(self, payload: dict) -> None:
        sample = self._dataset.add_from_worker(payload)
        self.lblT.setText(f"{sample.t_s:.2f} s")
        self.lblUsupply.setText(f"{sample.u_supply_V:+.3f} V")
        self.lblUmeas.setText(f"{sample.u_measure_V:+.4f} V")
        self.lblImeas.setText(f"{sample.i_measure_A:+.4e} A")
        self.lblTe.setText(f"{sample.te_eV:.3f} eV"
                            if sample.te_eV == sample.te_eV else "NaN")
        self.lblNe.setText(f"{sample.ne_m3:.3e} m\u207B\u00B3")
        self.lblSamples.setText(str(len(self._dataset)))
        redraw = False
        if sample.te_eV == sample.te_eV:
            self._te_t.append(sample.t_s); self._te_v.append(sample.te_eV)
            self._line_te.set_data(self._te_t, self._te_v)
            self._ax_te.relim(); self._ax_te.autoscale_view()
            redraw = True
        if (sample.ne_m3 == sample.ne_m3) and sample.ne_m3 > 0.0:
            self._ne_t.append(sample.t_s); self._ne_v.append(sample.ne_m3)
            self._line_ne.set_data(self._ne_t, self._ne_v)
            self._ax_ne.relim(); self._ax_ne.autoscale_view()
            redraw = True
        if redraw:
            self._canvas.draw_idle()

    @Slot(str)
    def _on_worker_stopped(self, reason: str) -> None:
        self.lblStatus.setText(f"Stopped ({reason}).")
        self._teardown_worker()
        self._refresh_button_state()
        self.running_changed.emit(False)
        self._log(f"LP stopped ({reason}).", "info")
        self._maybe_autosave()

    @Slot(str)
    def _on_worker_failed(self, msg: str) -> None:
        self.lblStatus.setText(f"FAILED: {msg}")
        self._teardown_worker()
        self._refresh_button_state()
        self.running_changed.emit(False)
        self._log(f"LP failed: {msg}", "error")
        self._maybe_autosave()

    @staticmethod
    def _format_area_label(area_m2: float) -> str:
        """Compact human label for the probe area display."""
        try:
            mm2 = float(area_m2) * 1e6
            return f"{mm2:.4f} mm²  ({float(area_m2):.4g} m²)"
        except Exception:
            return "—"

    def _reset_plot_for_new_run(self) -> None:
        """Drop the live plot buffers + live-value labels.

        The dataset and the ``Samples`` counter are kept intact so a
        post-stop Save CSV still exports every sample ever measured.
        Run from ``_on_start`` so the very first tick of a restarted
        run paints onto an empty canvas instead of triggering a
        full-history matplotlib rebuild.
        """
        self._te_t.clear(); self._te_v.clear()
        self._ne_t.clear(); self._ne_v.clear()
        self._line_te.set_data([], []); self._line_ne.set_data([], [])
        self._ax_te.relim(); self._ax_te.autoscale_view()
        self._ax_ne.relim(); self._ax_ne.autoscale_view()
        self._canvas.draw_idle()
        self.lblT.setText("\u2014 s")
        self.lblUsupply.setText("\u2014 V")
        self.lblUmeas.setText("\u2014 V")
        self.lblImeas.setText("\u2014 A")
        self.lblTe.setText("\u2014 eV")
        self.lblNe.setText("\u2014 m\u207B\u00B3")

    def _teardown_worker(self) -> None:
        """Disconnect, defer-delete and forget the current worker.

        Without this, every Stop → Start cycle would leak a worker
        plus its QTimer as a Qt child of this window, and stale
        signal connections from the old worker could fire into
        slots after a new worker had already been built — both
        contributed to multi-second restart latency.
        """
        w = self._worker
        if w is None:
            return
        for sig, slot in (
            (w.started, self._on_worker_started),
            (w.sample, self._on_worker_sample),
            (w.stopped, self._on_worker_stopped),
            (w.failed, self._on_worker_failed),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            w.deleteLater()
        except Exception:
            pass
        self._worker = None

    # ------------------------------------------------------------------
    # Save / autosave / clear
    # ------------------------------------------------------------------
    def _build_meta(self) -> dict:
        return {
            "V_d12_setpoint_V": f"{self.spnVd12.value():.4g}",
            "Compliance_A": f"{self.spnCompliance.value():.4g}",
            "Probe_Area_m2": f"{float(self._area_m2):.4g}",
            "Species": self._gas_mix_label or "Argon (Ar)",
            "V_d13_sign": str(self.cmbSign.currentData()),
            "Tick_ms": str(self.spnTick.value()),
        }

    def _maybe_autosave(self) -> None:
        if not self.chkAutoSave.isChecked():
            return
        if len(self._dataset) == 0:
            return
        path = self.editAutoSavePath.text().strip()
        if not path:
            self._log("Auto-save skipped: empty target path.", "warn")
            return
        try:
            self._dataset.write_csv(path, meta=self._build_meta())
            self.lblStatus.setText(f"Auto-saved: {path}")
            self._log(f"LP CSV auto-saved: {path}", "ok")
        except Exception as exc:
            self.lblStatus.setText(f"Auto-save failed: {exc}")
            self._log(f"LP CSV auto-save failed: {exc}", "error")

    @Slot()
    def _on_save_csv(self) -> None:
        if len(self._dataset) == 0:
            self.lblStatus.setText("Nothing to save.")
            return
        default_path = self.editAutoSavePath.text().strip() or str(
            self._default_autosave_path())
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save LP CSV", default_path, "CSV (*.csv)")
        if not chosen:
            return
        try:
            self._dataset.write_csv(chosen, meta=self._build_meta())
            self.lblStatus.setText(f"Saved: {chosen}")
            self._log(f"LP CSV saved: {chosen}", "ok")
        except Exception as exc:
            self.lblStatus.setText(f"Save failed: {exc}")
            self._log(f"LP CSV save failed: {exc}", "error")

    @Slot()
    def _on_clear(self) -> None:
        """Clear the visible plot.

        While the worker is running this only drops the live plot
        buffers and the rendered curves — the dataset keeps growing
        from incoming samples and the worker is *not* stopped.  When
        the worker is idle, the dataset and the sample counter are
        reset as well.  This way the operator can declutter the plot
        mid-run without losing any data.
        """
        running = self._worker is not None and self._worker.is_running
        # Always: drop visible curves and the per-line buffers.
        self._te_t.clear(); self._te_v.clear()
        self._ne_t.clear(); self._ne_v.clear()
        self._line_te.set_data([], []); self._line_ne.set_data([], [])
        self._ax_te.relim(); self._ax_te.autoscale_view()
        self._ax_ne.relim(); self._ax_ne.autoscale_view()
        self._canvas.draw_idle()
        if not running:
            # Idle path: also reset the dataset + counter.
            self._dataset.clear()
            self.lblSamples.setText("0")
        self._refresh_button_state()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.is_running:
            self._worker.request_stop()
        super().closeEvent(event)


def show_or_raise(parent, smu, k2000, *,
                  sim_current_a: Optional[float] = None,
                  gas_mix_label: Optional[str] = None,
                  mi_kg: Optional[float] = None,
                  area_m2: Optional[float] = None,
                  ) -> LPMeasurementWindow:
    """Singleton accessor on the parent window.

    ``sim_current_a`` (when given) is forwarded to the worker as the
    simulation-mode probe-current override.  ``gas_mix_label`` and
    ``mi_kg`` are computed by the LP main window from the Experiment
    dialog (Process gas types) so a real gas mixture is honoured by
    the Triple analysis.
    """
    win = getattr(parent, "_lp_window", None)
    if win is None:
        win = LPMeasurementWindow(
            smu, k2000, parent=parent,
            sim_current_a=sim_current_a,
            gas_mix_label=gas_mix_label, mi_kg=mi_kg,
            area_m2=area_m2)
        parent._lp_window = win
        parent._triple_window = win
    else:
        win._smu = smu
        win._k2000 = k2000
        win._sim_current_a = sim_current_a
        if gas_mix_label is not None:
            win._gas_mix_label = gas_mix_label
            try:
                win.lblGasMix.setText(gas_mix_label)
            except Exception:
                pass
        if mi_kg is not None:
            win._mi_kg_override = mi_kg
        if area_m2 is not None:
            win._area_m2 = float(area_m2)
            try:
                win.lblArea.setText(win._format_area_label(area_m2))
            except Exception:
                pass
    win.show()
    win.raise_()
    win.activateWindow()
    return win
