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
                 area_m2: Optional[float] = None,
                 ion_composition_context: Optional[dict] = None,
                 base_save_dir: Optional[Path] = None):
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
        # Shared ion-composition context (mode, x_atomic, x_atomic_unc,
        # preset key, mi_rel_unc).  Pulled from the LP main window's
        # Experiment params so Triple sees the SAME assumption as
        # Single and Double.  Triple's per-tick n_e uses mi_kg
        # directly (already set above) so the preset's numerical
        # effect is carried by that field; this dict is the audit
        # trail persisted in the CSV header.
        self._ion_composition_context = dict(
            ion_composition_context or {})
        # Probe area is owned by the main window's Probe Params…
        # dialog; the LP window only displays it.  Falls back to
        # the documented default until the main window injects the
        # current value.
        self._area_m2 = float(area_m2) if area_m2 is not None \
            else float(DEFAULT_AREA_M2)
        # Base save folder shared with the main GUI.  ``None`` means
        # "fall back to paths.lp_measurements_data_dir()" — see
        # :meth:`_default_autosave_path`.  The main window passes its
        # persisted main-save-path here so Triple, Single and Double
        # all land under the same operator-chosen root.
        self._base_save_dir: Optional[Path] = (
            Path(base_save_dir) if base_save_dir is not None else None)
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

        # Probe area — read-only display plus an Edit\u2026 shortcut
        # to the main window's Probe Params dialog so the operator
        # doesn't have to leave the Triple window to change it.
        self.lblArea = QLabel(self._format_area_label(self._area_m2))
        self.lblArea.setToolTip(
            "Probe area derived from Probe Params\u2026 in the main "
            "window.  This field is read-only here \u2014 use the "
            "Edit\u2026 button on the right to change it.")
        self.lblArea.setStyleSheet(
            "color: #c0c8d8; font-style: italic;")
        self.btnEditProbe = QPushButton("Edit\u2026")
        self.btnEditProbe.setMaximumWidth(54)
        self.btnEditProbe.setToolTip(
            "Open the main window's Probe Params\u2026 dialog to edit "
            "probe geometry and area.")
        self.btnEditProbe.clicked.connect(self._open_probe_params_on_parent)
        area_row = QHBoxLayout()
        area_row.setContentsMargins(0, 0, 0, 0); area_row.setSpacing(6)
        area_row.addWidget(self.lblArea, 1)
        area_row.addWidget(self.btnEditProbe)
        fl.addRow("Probe area:", area_row)

        # Gas mix is read from the parent's Experiment dialog (Process
        # gas types) so a real mixture is honoured.  Edit\u2026 opens
        # that same dialog directly.
        self.lblGasMix = QLabel(self._gas_mix_label or "Argon (Ar)")
        self.lblGasMix.setToolTip(
            "Gas mix used by the Triple-Probe analysis.  Read-only "
            "here \u2014 use Edit\u2026 to open the main window's "
            "Experiment dialog.")
        self.lblGasMix.setStyleSheet(
            "color: #c0c8d8; font-style: italic;")
        self.btnEditExperiment = QPushButton("Edit\u2026")
        self.btnEditExperiment.setMaximumWidth(54)
        self.btnEditExperiment.setToolTip(
            "Open the main window's Experiment\u2026 dialog to edit "
            "process gas species, flows, and ion-composition settings.")
        self.btnEditExperiment.clicked.connect(
            self._open_experiment_on_parent)
        gas_row = QHBoxLayout()
        gas_row.setContentsMargins(0, 0, 0, 0); gas_row.setSpacing(6)
        gas_row.addWidget(self.lblGasMix, 1)
        gas_row.addWidget(self.btnEditExperiment)
        fl.addRow("Gas mix:", gas_row)

        self.cmbSign = QComboBox()
        self.cmbSign.addItem("+1  (V_d13 = +U_K2000)", +1)
        self.cmbSign.addItem("-1  (V_d13 = -U_K2000)", -1)
        fl.addRow("V_d13 sign:", self.cmbSign)

        # Formula choice — user-friendly labels for what used to be
        # "Use exact Eq-10 (fallback Eq-11)".  Default is the fast
        # closed-form approximation; Numeric triggers the bisection
        # solver with the closed form as a fallback.  "Numeric" is
        # the operator-facing wording since the solver is numerical
        # and not in any absolute sense more "exact" than the
        # analytical closed form.
        self.cmbEqMode = QComboBox()
        self.cmbEqMode.addItem("Approx.", False)   # prefer_eq10 = False
        self.cmbEqMode.addItem("Numeric", True)    # prefer_eq10 = True
        self.cmbEqMode.setCurrentIndex(0)          # default: Approx.
        self.cmbEqMode.setToolTip(
            "Te formula:\n"
            "  Approx. \u2014 closed-form Te = V_d13 / ln 2 "
            "(fast, robust).\n"
            "  Numeric \u2014 implicit triple-probe equation, solved "
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
        base = self._base_save_dir
        if base is None:
            try:
                from paths import lp_measurements_data_dir
                base = lp_measurements_data_dir()
            except Exception:
                base = Path.cwd()
        return make_triple_csv_path(Path(base))

    def set_base_save_dir(self, base: Optional[Path]) -> None:
        """Update the shared main save folder and refresh the
        auto-save path.  Called by the main GUI after the operator
        picks a new main save folder so this (singleton) window
        stays in sync even after a live change."""
        self._base_save_dir = Path(base) if base is not None else None
        try:
            self.editAutoSavePath.setText(
                str(self._default_autosave_path()))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Edit-in-parent helpers (Probe area, Gas mix)
    # ------------------------------------------------------------------
    @Slot()
    def _open_probe_params_on_parent(self) -> None:
        """Open the main window's Probe Params dialog without closing
        this Triple window.  Forwards to the parent's existing slot
        and re-reads the probe area afterwards so the read-only label
        reflects any change the operator just made."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "_open_probe_dialog"):
            self._log("Probe Params dialog unavailable \u2014 parent "
                      "window not wired for it.", "warn")
            return
        try:
            parent._open_probe_dialog()
        except Exception as exc:
            self._log(f"Probe Params open failed: {exc}", "error")
            return
        # Re-pull the area from the parent so the read-only label
        # matches the freshly-chosen geometry.
        try:
            new_area = None
            if hasattr(parent, "_build_lp_probe_area_m2"):
                new_area = parent._build_lp_probe_area_m2()
            if new_area is not None:
                self._area_m2 = float(new_area)
                self.lblArea.setText(self._format_area_label(new_area))
        except Exception:
            pass

    @Slot()
    def _open_experiment_on_parent(self) -> None:
        """Open the main window's Experiment dialog (process gases,
        ion composition).  Refreshes the gas-mix label afterwards."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "_open_experiment_dialog"):
            self._log("Experiment dialog unavailable \u2014 parent "
                      "window not wired for it.", "warn")
            return
        try:
            parent._open_experiment_dialog()
        except Exception as exc:
            self._log(f"Experiment open failed: {exc}", "error")
            return
        try:
            if hasattr(parent, "_build_lp_gas_context"):
                gas_label, mi_kg, _ = parent._build_lp_gas_context()
                if gas_label is not None:
                    self._gas_mix_label = gas_label
                    self.lblGasMix.setText(gas_label)
                if mi_kg is not None:
                    self._mi_kg_override = mi_kg
        except Exception:
            pass

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
            # The ion-composition mi_rel_unc feeds the Triple mass-only
            # n_e CI.  Pulled from the shared context so Single /
            # Double / Triple all quote the same uncertainty model.
            try:
                mi_rel_unc = float(
                    (self._ion_composition_context or {}).get(
                        "mi_rel_unc", 0.0) or 0.0)
            except (TypeError, ValueError):
                mi_rel_unc = 0.0
            self._worker = TripleProbeWorker(
                self._smu, self._k2000,
                v_d12_setpoint=float(self.spnVd12.value()),
                current_limit_a=float(self.spnCompliance.value()),
                area_m2=area_m2,
                species_name=species,
                mi_kg=self._mi_kg_override,
                mi_rel_unc=mi_rel_unc,
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
        ne_txt = f"{sample.ne_m3:.3e} m\u207B\u00B3"
        lo, hi = sample.ne_ci95_lo_m3, sample.ne_ci95_hi_m3
        if (lo is not None and hi is not None
                and sample.ne_m3 == sample.ne_m3
                and sample.ne_m3 > 0.0):
            # Symmetric mass-only CI: show a single ±half-width so the
            # operator reads the uncertainty at a glance.  Falls back
            # to the bare n_e when CI was not propagated (fit_only).
            try:
                half = 0.5 * (float(hi) - float(lo))
                if half > 0.0:
                    ne_txt += f"  \u00B1 {half:.2e}"
            except (TypeError, ValueError):
                pass
        self.lblNe.setText(ne_txt)
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
        meta: dict[str, str] = {
            "V_d12_setpoint_V": f"{self.spnVd12.value():.4g}",
            "Compliance_A": f"{self.spnCompliance.value():.4g}",
            "Probe_Area_m2": f"{float(self._area_m2):.4g}",
            "Species": self._gas_mix_label or "Argon (Ar)",
            "V_d13_sign": str(self.cmbSign.currentData()),
            "Tick_ms": str(self.spnTick.value()),
        }
        # Ion-composition audit trail — added whenever a context
        # dict was handed down from the LP main window.  The values
        # are shared across Single / Double / Triple: the same
        # experiment settings produced the same numbers everywhere.
        # A single ``Ion_Note`` entry spells out what the Ion_*
        # keys mean physically, so a later reader (or another
        # engineer) cannot misread ``Ion_Composition_Mode=atomic``
        # as "atomic feed gas" — it is a plasma-phase assumption.
        ctx = getattr(self, "_ion_composition_context", None) or {}
        if ctx:
            meta["Ion_Note"] = (
                "plasma-phase positive-ion assumption for the "
                "Bohm density formula; feed flows above remain "
                "molecular inlet flows")
            if "ion_composition_preset" in ctx:
                meta["Ion_Composition_Preset"] = str(
                    ctx.get("ion_composition_preset") or "custom")
            if "ion_composition_mode" in ctx:
                meta["Ion_Composition_Mode"] = str(
                    ctx.get("ion_composition_mode") or "molecular")
            if "x_atomic" in ctx:
                meta["Ion_x_atomic"] = \
                    f"{float(ctx.get('x_atomic', 0.0)):.3f}"
            if "x_atomic_unc" in ctx:
                meta["Ion_x_atomic_unc"] = \
                    f"{float(ctx.get('x_atomic_unc', 0.0)):.3f}"
            if "mi_rel_unc" in ctx:
                mi_rel = float(ctx.get("mi_rel_unc", 0.0) or 0.0)
                meta["Ion_mi_rel_unc"] = f"{mi_rel:.4f}"
                # Derived n_e relative uncertainty under the Triple
                # Bohm scaling: n_e ∝ 1/√m_i ⇒ σ_n/n = ½·σ_m/m.
                # Recording the derived number too keeps the CSV
                # self-describing even when a reader doesn't know
                # the physics.
                if mi_rel > 0.0:
                    meta["Ion_ne_rel_unc"] = f"{0.5 * mi_rel:.4f}"
            # Per-gas composition overrides (may be empty).  One
            # meta line per molecular gas with a non-default entry,
            # so a later reader sees exactly which gases carried
            # which regime.
            pg = ctx.get("per_gas_composition")
            if isinstance(pg, dict):
                for _g, _entry in pg.items():
                    if not isinstance(_entry, dict):
                        continue
                    _m = str(_entry.get("mode", "molecular"))
                    _x = float(_entry.get("x_atomic", 0.0))
                    _dx = float(_entry.get("x_atomic_unc", 0.0))
                    _p = str(_entry.get("preset", "custom"))
                    meta[f"Ion_{_g}"] = (
                        f"mode={_m}, x={_x:.3f}, "
                        f"\u0394x={_dx:.3f}, preset={_p}")
        return meta

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
                  ion_composition_context: Optional[dict] = None,
                  base_save_dir: Optional[Path] = None,
                  ) -> LPMeasurementWindow:
    """Singleton accessor on the parent window.

    ``sim_current_a`` (when given) is forwarded to the worker as the
    simulation-mode probe-current override.  ``gas_mix_label`` and
    ``mi_kg`` are computed by the LP main window from the Experiment
    dialog (Process gas types) so a real gas mixture is honoured by
    the Triple analysis.  ``base_save_dir`` is the operator-chosen
    main save folder from the main GUI so Triple's auto-save default
    lands under the same root as Single / Double.
    """
    win = getattr(parent, "_lp_window", None)
    if win is None:
        win = LPMeasurementWindow(
            smu, k2000, parent=parent,
            sim_current_a=sim_current_a,
            gas_mix_label=gas_mix_label, mi_kg=mi_kg,
            area_m2=area_m2,
            ion_composition_context=ion_composition_context,
            base_save_dir=base_save_dir)
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
        if ion_composition_context is not None:
            # Always replace wholesale — the context is a small
            # snapshot, not a partial patch, and we want Triple's
            # CSV header to reflect the *current* experiment state.
            win._ion_composition_context = dict(
                ion_composition_context)
        if base_save_dir is not None:
            # Keep the auto-save default in lockstep with the main
            # GUI's main save folder on every re-open.
            try:
                win.set_base_save_dir(base_save_dir)
            except Exception:
                pass
    win.show()
    win.raise_()
    win.activateWindow()
    return win
