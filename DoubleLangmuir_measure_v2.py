"""
Double-Langmuir-Probe Monitor **v2** – improved simulation + integrated analysis.

Extends the base acquisition GUI with:
- FakeB2901v2 simulation backend (smooth tanh + sheath-expansion)
- saturation-branch fit lines visible in the plot
- corrected I-V curve overlay
- sensible default data directory (data/double_langmuir/)

The real-hardware path (KeysightB2901PSU) is unchanged.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Slot, QTimer
from PySide6.QtWidgets import (
    QApplication, QPushButton, QDoubleSpinBox, QGridLayout, QHBoxLayout,
    QLabel, QCheckBox, QSizePolicy,
)

# Patch the simulation class *before* anyone resolves the name.
import DoubleLangmuir_measure as _base
from fake_b2901_v2 import FakeB2901v2
from DoubleLangmuirAnalysis_v2 import (
    fit_saturation_branches, correct_iv_curve, compute_plasma_params,
)
from dlp_fit_models import (
    FitModelDialog, fit_dlp_model, MODELS, DEFAULT_MODEL,
    compare_all_models, grade_fit_quality,
)
from dlp_probe_dialog import (
    ProbeParameterDialog, DEFAULT_PROBE_PARAMS, probe_params_for_csv,
    compute_electrode_area,
)
from dlp_sim_dialog import (
    SimulationOptionsDialog, DEFAULT_SIM_OPTIONS, sim_options_to_fake_kwargs,
)
from dlp_experiment_dialog import (
    ExperimentParameterDialog, DEFAULT_EXPERIMENT_PARAMS,
    effective_ion_mass_kg,
)
from dlp_instrument_dialog import (
    InstrumentOptionsDialog, DEFAULT_INSTRUMENT_OPTIONS,
    apply_instrument_options, get_nplc,
)
from utils import append_log
from analysis_history import append_record as append_analysis_record
from analysis_log_window import show_or_raise as show_analysis_window

_base.FakeB2901 = FakeB2901v2          # simulation path now uses v2

# Re-export the full public API.
from DoubleLangmuir_measure import (    # noqa: E402
    build_voltage_list,
    build_sweep_voltages,
    make_csv_path,
    write_csv,
    DLPScanWorker,
    DLPMainWindow,
)

log = logging.getLogger("DLP-v2")


_C_ACCENT = "#4f8ef7"
_C_VAL    = "#5ccf8a"
_C_DIM    = "#8890a0"
_C_WARN   = "#e0b050"
_C_HEAD   = "#7799cc"


def _section(title: str) -> str:
    return (f'<span style="color:{_C_HEAD};font-size:11px;'
            f'font-weight:600;">{title}</span><br/>')


def format_result_block(fit: dict, pp: dict,
                         ion_label: str = "") -> str:
    """Format analysis results as a structured HTML block.

    Sections: Fit Status (only if non-OK) → Key Results → Fit Quality
    → Model & Parameters → Density.  A non-OK status is surfaced as a
    prominent coloured banner at the top of the block so the operator
    cannot mistake a failed fit for a merely weak one.
    """
    import html as _html
    from dlp_fit_models import FitStatus, FAILURE_STATUSES

    lines = [
        f'<div style="margin:6px 0;padding:4px 8px;'
        f'border-left:3px solid {_C_ACCENT};">',
        f'<span style="color:{_C_ACCENT};font-weight:700;">'
        f'\u2500\u2500 Analysis Results \u2500\u2500</span><br/>',
    ]

    # ── Fit-status banner (only emitted when not "ok") ──
    fit_status = pp.get("fit_status", FitStatus.OK) if pp else FitStatus.OK
    if fit_status != FitStatus.OK:
        reason = (pp.get("fit_error_reason")
                  or pp.get("fit_warning_reason")) if pp else None
        if fit_status in FAILURE_STATUSES:
            banner_color = "#f06060"   # red  — fit untrustworthy
            banner_head  = "Fit failed"
        else:                           # POOR / WARNING — converged
            banner_color = _C_WARN
            banner_head  = "Fit warning"
        lines.append(
            f'<span style="color:{banner_color};font-weight:700;">'
            f'{banner_head}: {_html.escape(str(fit_status))}'
            f'</span><br/>')
        if reason:
            lines.append(
                f'<span style="color:{_C_DIM};font-size:11px;">'
                f'{_html.escape(str(reason))}</span><br/>')

    # ── Key Results ──
    te = pp.get("Te_eV", float("nan"))
    te_err = pp.get("Te_err_eV", float("nan"))
    i_sat = pp.get("I_sat_fit_A", float("nan"))
    if not np.isnan(te):
        te_s = f"T_e = {te:.2f}"
        if not np.isnan(te_err):
            te_s += f" \u00b1 {te_err:.2f}"
        te_s += " eV"
        lines.append(f'<span style="color:{_C_VAL};font-size:15px;">'
                      f'<b>{te_s}</b></span><br/>')
        # 95 % CI line — keeps the uncertainty attached to the point
        # estimate in the operator's primary result block.
        ci_lo = pp.get("Te_ci95_lo_eV")
        ci_hi = pp.get("Te_ci95_hi_eV")
        ci_method = pp.get("Te_ci_method", "unavailable")
        if ci_method != "unavailable" and ci_lo is not None \
                and ci_hi is not None \
                and not np.isnan(ci_lo) and not np.isnan(ci_hi):
            label = ("bootstrap 95 % CI" if ci_method == "bootstrap"
                     else "95 % CI")
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;{label}: [{ci_lo:.2f}, {ci_hi:.2f}] eV'
                f'</span><br/>')
        elif ci_method == "unavailable":
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;95 % CI: unavailable</span><br/>')
    if not np.isnan(i_sat):
        i_sat_line = f'I_sat = {i_sat:.3e} A'
        i_sat_lo = pp.get("I_sat_ci95_lo_A")
        i_sat_hi = pp.get("I_sat_ci95_hi_A")
        i_sat_method = pp.get("I_sat_ci_method", "unavailable")
        lines.append(f'<span style="color:{_C_VAL};">{i_sat_line}'
                      f'</span><br/>')
        if (i_sat_method != "unavailable"
                and i_sat_lo is not None and i_sat_hi is not None
                and not np.isnan(i_sat_lo) and not np.isnan(i_sat_hi)):
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;95 % CI: [{i_sat_lo:.3e}, {i_sat_hi:.3e}] A'
                f'</span><br/>')
        elif i_sat_method == "unavailable":
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;95 % CI: unavailable</span><br/>')

    # ── Fit Quality ──
    r2 = pp.get("R2", float("nan"))
    nrmse = pp.get("NRMSE", float("nan"))
    grade = pp.get("grade", "")
    grade_color = pp.get("grade_color", _C_DIM)
    if not np.isnan(r2):
        lines.append(_section("Fit Quality"))
        q_parts = [f"R\u00b2 = {r2:.4f}"]
        if not np.isnan(nrmse):
            q_parts.append(f"NRMSE = {nrmse:.2%}")
        if grade:
            q_parts.append(
                f'<span style="color:{grade_color};">[{grade}]</span>')
        lines.append(f'<span style="color:{_C_DIM};">'
                      f'{"&ensp;|&ensp;".join(q_parts)}</span><br/>')

    # ── Model & Parameters ──
    model_label = pp.get("label", "")
    fit_data = pp.get("fit_data", "")
    pnames = pp.get("param_names", [])
    pvals = pp.get("param_values", [])
    perrs = pp.get("param_errors", [])
    punits = pp.get("param_units", [])
    if model_label:
        data_tag = f" ({fit_data} data)" if fit_data else ""
        lines.append(_section(f"Model: {model_label}{data_tag}"))
        for n, v, e, u in zip(pnames, pvals, perrs, punits):
            if np.isnan(v):
                continue
            s = f"&ensp;{n} = {v:.4e}"
            if not np.isnan(e):
                s += f" \u00b1 {e:.1e}"
            if u:
                s += f" {u}"
            lines.append(f'<span style="color:{_C_DIM};">{s}</span><br/>')

    # ── Density ──
    n_i = pp.get("n_i_m3", float("nan"))
    if not np.isnan(n_i):
        v_b = pp.get("v_Bohm_ms", 0)
        gas = f", {ion_label}" if ion_label else ""
        lines.append(f'<span style="color:{_C_VAL};">'
                      f'n_i = {n_i:.3e} m\u207b\u00b3'
                      f'&ensp;(v_B={v_b:.0f} m/s{gas})</span><br/>')
        # n_i CI — label honestly by what was folded in.  The note
        # comes directly from compute_double_analysis which updated
        # it to "fit_only" / "fit+area" / "fit+mass" / "fit+area+mass"
        # based on the operator's uncertainty inputs, so the label
        # under the number always truthfully names its scope.
        n_lo = pp.get("n_i_ci95_lo_m3")
        n_hi = pp.get("n_i_ci95_hi_m3")
        n_method = pp.get("n_i_ci_method", "unavailable")
        n_note = pp.get("n_i_ci_note", "fit_only")
        _scope_display = n_note.replace("_", "-")
        if (n_method != "unavailable"
                and n_lo is not None and n_hi is not None
                and not np.isnan(n_lo) and not np.isnan(n_hi)):
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;95 % CI ({_scope_display}): '
                f'[{n_lo:.3e}, {n_hi:.3e}] m\u207b\u00b3'
                f'</span><br/>')
            # The small-print caveat updates to say exactly which of
            # the two extra inputs is being included.  When the
            # operator leaves both at 0 % the caveat matches the
            # pre-existing shipping text.
            missing_parts = []
            if "area" not in n_note:
                missing_parts.append("probe area")
            if "mass" not in n_note:
                missing_parts.append("ion mass")
            if missing_parts:
                missing_txt = " &amp; ".join(missing_parts)
                lines.append(
                    f'<span style="color:{_C_DIM};font-size:10px;">'
                    f'&ensp;({missing_txt} treated as exact)'
                    f'</span><br/>')
        elif n_method == "unavailable":
            lines.append(
                f'<span style="color:{_C_DIM};">'
                f'&ensp;n_i 95 % CI: unavailable</span><br/>')
    elif not pp.get("label"):
        pass  # no model → skip
    else:
        lines.append(f'<span style="color:{_C_WARN};">'
                      f'n_i: n/a (set gas via Experiment\u2026)</span><br/>')

    # ── Compliance / Clipping provenance ──
    # Reads the last compliance summary set by the analyze path on
    # the V2 instance (see DLPMainWindowV2._run_analysis).  Module-
    # level importers that call format_result_block without a
    # window instance simply do not see this block — the meta can
    # also be passed in via pp["compliance_info"] so the function
    # stays usable in headless contexts / tests.
    comp_info = pp.get("compliance_info")
    if comp_info and int(comp_info.get("n_flagged", 0)) > 0:
        n_fl = int(comp_info["n_flagged"])
        n_to = int(comp_info.get("n_total", 0))
        frac = float(comp_info.get("clipped_fraction", 0.0))
        action = comp_info.get("action", "n/a")
        source = comp_info.get("source", "operator_provided")
        # Colour scales with severity: amber for advisory, red when
        # clipping dominated the fit (either mode).
        if action == "retained_in_fit":
            sev_color = _C_WARN
        elif frac >= 0.25:
            sev_color = "#f06060"
        elif frac >= 0.05:
            sev_color = _C_WARN
        else:
            sev_color = _C_DIM
        lines.append(_section("Compliance"))
        label = ("suspected clipping" if source == "heuristic_suspected"
                 else "compliance-flagged")
        verb = ("excluded from fit"
                if action == "excluded_from_fit"
                else ("retained in fit" if action == "retained_in_fit"
                       else "flagged"))
        lines.append(
            f'<span style="color:{sev_color};">'
            f'&ensp;{n_fl}/{n_to} {label} point(s) {verb} '
            f'({frac:.1%})</span><br/>')
        if source == "heuristic_suspected":
            lines.append(
                f'<span style="color:{_C_DIM};font-size:10px;">'
                f'&ensp;(legacy dataset — detected via plateau '
                f'heuristic, not a confirmed compliance flag)'
                f'</span><br/>')

    lines.append('</div>')
    return '\n'.join(lines)


def format_model_comparison(cmp: list[dict],
                             active_key: str = "") -> str:
    """Format model comparison as a structured HTML block."""
    if not cmp:
        return ""
    lines = [
        f'<div style="margin:4px 0;padding:3px 8px;'
        f'border-left:3px solid {_C_DIM};">',
        f'<span style="color:{_C_HEAD};font-weight:600;">'
        f'\u2500\u2500 Model Comparison \u2500\u2500</span><br/>',
        f'<span style="color:{_C_DIM};font-size:10px;">'
        f'(R\u00b2/NRMSE not comparable across data bases)</span><br/>',
    ]
    import html as _html
    from dlp_fit_models import FitStatus, FAILURE_STATUSES

    for c in cmp:
        is_active = c.get("model_key") == active_key
        marker = "\u25b6" if is_active else "&ensp;"
        fd = c.get("fit_data", "?")[:3]
        te = c.get("Te_eV", float("nan"))
        r2 = c.get("R2", float("nan"))
        nr = c.get("NRMSE", float("nan"))
        gr = c.get("grade", "n/a")
        status = c.get("fit_status", FitStatus.OK)

        label = c.get("label", "?")
        weight = "font-weight:600;" if is_active else ""
        te_s = f"{te:.2f}" if not np.isnan(te) else "n/a"
        r2_s = f"{r2:.4f}" if not np.isnan(r2) else "n/a"
        nr_s = f"{nr:.2%}" if not np.isnan(nr) else "n/a"

        # Per-row failure annotation so the comparison block tells
        # the operator WHY one candidate has n/a numbers instead of
        # just showing "n/a n/a n/a [n/a]".
        if status in FAILURE_STATUSES:
            reason = c.get("fit_error_reason") or status
            status_tag = (f' <span style="color:#f06060;">'
                          f'[failed: {_html.escape(str(reason))}]</span>')
        else:
            status_tag = ""

        lines.append(
            f'<span style="color:{_C_DIM};{weight}">'
            f'{marker} {label} '
            f'<span style="color:{_C_HEAD};">[{fd}]</span> '
            f'R\u00b2={r2_s} '
            f'NRMSE={nr_s} '
            f'T_e={te_s} eV '
            f'[{gr}]{status_tag}</span><br/>')
    lines.append('</div>')
    return '\n'.join(lines)


def _append_html_block(window, html: str) -> None:
    """Insert a raw HTML block into the log widget."""
    from PySide6.QtWidgets import QTextEdit
    widget = window.findChild(QTextEdit, "txtLog")
    if widget:
        widget.append(html)


def default_data_dir() -> Path:
    """Return (and create) the default data directory for LP
    measurements (Single, Double, Triple — per-method subfolders
    underneath).

    Dev mode resolves to ``<repo>/data/lp_measurements/``; frozen
    build to ``%APPDATA%/JLU-IPI/DLP/lp_measurements/``.  See
    :mod:`paths` for the canonical helper plus legacy-folder
    discovery / migration.
    """
    from paths import lp_measurements_data_dir
    return lp_measurements_data_dir()


def _ensure_valid_app_font(fallback_pt: int = 9) -> None:
    """Ensure QApplication font has a valid pointSize (> 0).

    Qt stylesheets with ``font-size: Npx`` cause ``pointSize()`` to
    return -1.  If any code later reads and re-applies that value,
    Qt emits ``QFont::setPointSize: Point size <= 0``.  This helper
    sets a clean fallback on the app font before widgets are created.
    """
    app = QApplication.instance()
    if app is None:
        return
    f = app.font()
    if f.pointSize() <= 0:
        f.setPointSize(fallback_pt)
        app.setFont(f)


class DLPMainWindowV2(DLPMainWindow):
    """v2 acquisition window with integrated saturation analysis."""

    def __init__(self):
        # Fix QFont::setPointSize warning BEFORE parent init.
        # The theme stylesheet uses font-size in px, which makes
        # QFont::pointSize() return -1.  Setting a valid point size
        # on the app font prevents the warning during widget creation.
        _ensure_valid_app_font()
        super().__init__()
        self.setWindowTitle("Double-Langmuir-Probe Monitor v2")

        # enlarge log font for better readability (11px → 13px)
        self.txtLog.setStyleSheet(
            self.txtLog.styleSheet() +
            "\nQTextEdit { font-size: 13px; line-height: 1.5; }")

        # override default save folder
        self._save_folder = default_data_dir()
        self.lblFolder.setText(str(self._save_folder))

        # analysis overlay: fit lines (bold, high z-order, fit-region only)
        self.line_fit_pos, = self.ax.plot([], [], "-",
                                          color="#ff4444", lw=2.5,
                                          zorder=5, label="Fit pos. sat.")
        self.line_fit_neg, = self.ax.plot([], [], "-",
                                          color="#ff8800", lw=2.5,
                                          zorder=5, label="Fit neg. sat.")
        self.line_corrected, = self.ax.plot([], [], "--",
                                            color="#2ecc71", lw=1.8,
                                            zorder=4, label="Corrected")
        self.line_te_fit, = self.ax.plot([], [], "-.",
                                          color="#cc44ff", lw=2.0,
                                          zorder=6, label="Model fit")
        # shading patches for fit regions (managed list, cleared on re-fit)
        self._fit_shading: list = []

        # probe parameters state
        self._probe_params: dict = dict(DEFAULT_PROBE_PARAMS)
        # simulation options state
        self._sim_options: dict = dict(DEFAULT_SIM_OPTIONS)
        # experiment parameters state
        import copy
        self._experiment_params: dict = copy.deepcopy(DEFAULT_EXPERIMENT_PARAMS)

        # Option buttons are no longer crammed into a single grid in
        # the Instrument group — they go where they semantically belong
        # (see _distribute_option_buttons below).

        self.btnProbeParams = QPushButton("Probe Params\u2026")
        self.btnProbeParams.setToolTip("Set probe geometry and metadata")
        self.btnProbeParams.clicked.connect(self._open_probe_dialog)

        self.btnSimOptions = QPushButton("Sim Options\u2026")
        self.btnSimOptions.setToolTip("Configure simulation noise, "
                                       "asymmetry, drift")
        self.btnSimOptions.clicked.connect(self._open_sim_dialog)

        self.btnExperiment = QPushButton("Experiment\u2026")
        self.btnExperiment.setToolTip("Gas species, flow rates, ion mass")
        self.btnExperiment.clicked.connect(self._open_experiment_dialog)

        # fit model state + button
        self._fit_model: str = DEFAULT_MODEL
        self.btnFitModel = QPushButton("Fit Model\u2026")
        self.btnFitModel.setToolTip("Select tanh fit model variant")
        self.btnFitModel.clicked.connect(self._open_fit_model_dialog)

        # analysis history file (None = use default path from
        # analysis_history.default_history_path()).  Tests override it.
        self._analysis_history_path: str | None = None

        # instrument options state + button
        self._instrument_opts: dict = dict(DEFAULT_INSTRUMENT_OPTIONS)
        self.btnInstrument = QPushButton("Instrument\u2026")
        self.btnInstrument.setToolTip("SMU speed, protection, autorange")
        self.btnInstrument.clicked.connect(self._open_instrument_dialog)

        # save CSV checkbox (default: ON)
        self.chkSave = QCheckBox("Save CSV")
        self.chkSave.setChecked(True)
        self.chkSave.setToolTip("Automatically save sweep data to CSV")

        # auto-analyze checkbox (default: OFF – keep raw data visible after
        # a sweep so atypical curves are not obscured by the fit / autoscale
        # logic; the user triggers analysis explicitly via btnAnalyze).
        self.chkAutoAnalyze = QCheckBox("Auto analyze")
        self.chkAutoAnalyze.setChecked(False)
        self.chkAutoAnalyze.setToolTip(
            "If enabled, analysis runs automatically after each sweep.\n"
            "Default: OFF – raw data stays visible, analysis is triggered "
            "manually via the Analyze button.")

        self._distribute_option_buttons()

        # Analyze button + sat_fraction control
        ctrl_layout = self.btnStop.parent().layout()
        if ctrl_layout is None:
            ctrl_layout = self.btnStart.parentWidget().layout()

        row_ana = QHBoxLayout()
        self.btnAnalyze = QPushButton("Analyze")
        self.btnAnalyze.setToolTip("Fit saturation branches on current data")
        self.btnAnalyze.clicked.connect(self._run_analysis)
        row_ana.addWidget(self.btnAnalyze)
        row_ana.addWidget(QLabel("Frac:"))
        self.spnSatFrac = QDoubleSpinBox()
        self.spnSatFrac.setRange(0.05, 0.45)
        self.spnSatFrac.setSingleStep(0.05)
        self.spnSatFrac.setDecimals(2)
        self.spnSatFrac.setValue(0.20)
        self.spnSatFrac.setToolTip("Fraction of V-range used for each "
                                    "saturation branch (outer x %)")
        row_ana.addWidget(self.spnSatFrac)
        ctrl_layout.insertLayout(1, row_ana)

    # ── option-button distribution ─────────────────────────────────

    _COMPACT_BTN_STYLE = "QPushButton { padding: 2px 6px; }"
    _COMPACT_BTN_MAX_H = 28

    def _distribute_option_buttons(self) -> None:
        """Place the five option buttons into their semantic homes.

        * Instrument…    → inside the Instrument group (hardware setup).
        * Sim Options…   → next to the Simulation checkbox; visible
                            only while the Simulation toggle is on.
        * Fit Model…     → inside the Control group (next to Analyze).
        * Probe Params…  → V3 picks it up as the 5th method button.
        * Experiment…    → inside a brand-new "Process gas types"
                            QGroupBox that gets inserted between
                            Control and Output in the left column.

        Save CSV / Auto-analyze move into the Output group where
        save behaviour semantically belongs.
        """
        for b in (self.btnProbeParams, self.btnSimOptions,
                  self.btnExperiment, self.btnFitModel,
                  self.btnInstrument):
            b.setMaximumHeight(self._COMPACT_BTN_MAX_H)
            b.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Fixed)
            b.setStyleSheet(self._COMPACT_BTN_STYLE)

        # 1) Instrument… → Instrument group
        inst_layout = getattr(self, "_inst_layout", None)
        if inst_layout is not None:
            inst_layout.addWidget(self.btnInstrument)

        # 2) Sim Options… → next to the Simulation checkbox; sichtbar
        #    nur, wenn der Sim-Mode aktiv ist.
        row_sim = getattr(self, "_row_sim_layout", None)
        if row_sim is not None:
            # insert right after chkSim (index 1, before the trailing stretch)
            row_sim.insertWidget(1, self.btnSimOptions)
        self.btnSimOptions.setVisible(self.chkSim.isChecked())
        self.chkSim.toggled.connect(self.btnSimOptions.setVisible)

        # 3) Fit Model… → Control group
        ctrl_layout = getattr(self, "_ctrl_layout", None)
        if ctrl_layout is not None:
            ctrl_layout.addWidget(self.btnFitModel)

        # 4) Process gas types group with Experiment… inside, inserted
        #    in the left column right BEFORE the Output group so Output
        #    naturally drops one slot lower.
        from PySide6.QtWidgets import QGroupBox, QVBoxLayout
        self.grpGases = QGroupBox("Process gas types")
        gas_v = QVBoxLayout(self.grpGases)
        gas_v.setContentsMargins(8, 8, 8, 8)
        gas_v.setSpacing(4)
        gas_v.addWidget(self.btnExperiment)

        left_v = getattr(self, "_left_v_layout", None)
        grp_file = getattr(self, "_grp_file", None)
        if left_v is not None and grp_file is not None:
            idx = left_v.indexOf(grp_file)
            if idx < 0:
                left_v.addWidget(self.grpGases)
            else:
                left_v.insertWidget(idx, self.grpGases)
        elif left_v is not None:
            left_v.addWidget(self.grpGases)

        # 5) Save CSV + Auto analyze move into the Output group.
        fv = getattr(self, "_fv_layout", None)
        if fv is not None:
            row_chk = QHBoxLayout()
            row_chk.setObjectName("rowOutputToggles")
            row_chk.setContentsMargins(0, 0, 0, 0)
            row_chk.setSpacing(12)
            row_chk.addWidget(self.chkSave)
            row_chk.addWidget(self.chkAutoAnalyze)
            row_chk.addStretch(1)
            fv.addLayout(row_chk)

    # ── probe parameters ───────────────────────────────────────────

    def _open_probe_dialog(self):
        dlg = ProbeParameterDialog(self._probe_params, parent=self)
        if dlg.exec() == ProbeParameterDialog.DialogCode.Accepted:
            self._probe_params = dlg.get_params()
            append_log(self, f"Probe params updated: "
                             f"A={dlg.get_effective_area_mm2():.3f} mm², "
                             f"{self._probe_params['geometry']}", "ok")

    # ── simulation options ──────────────────────────────────────────

    def _open_sim_dialog(self):
        dlg = SimulationOptionsDialog(self._sim_options, parent=self)
        if dlg.exec() == SimulationOptionsDialog.DialogCode.Accepted:
            self._sim_options = dlg.get_options()
            self._apply_sim_to_smu()
            append_log(self, f"Sim options: noise={self._sim_options['noise_uA']:.1f} µA, "
                             f"asym={self._sim_options['asymmetry_pct']:.1f}%, "
                             f"corr={self._sim_options['noise_corr']:.2f}", "ok")

    def _apply_sim_to_smu(self):
        """Push current sim options into the live FakeB2901v2 instance."""
        if not isinstance(self.smu, FakeB2901v2):
            return
        kw = sim_options_to_fake_kwargs(self._sim_options)
        self.smu.noise_std = kw["noise_std"]
        self.smu.noise_corr = kw["noise_corr"]
        self.smu.asymmetry = kw["asymmetry"]
        self.smu.i_offset = kw["i_offset"]
        self.smu.drift_per_point = kw["drift_per_point"]

    def _open_experiment_dialog(self):
        dlg = ExperimentParameterDialog(self._experiment_params, parent=self)
        if dlg.exec() == ExperimentParameterDialog.DialogCode.Accepted:
            self._experiment_params = dlg.get_params()
            mi = effective_ion_mass_kg(
                self._experiment_params.get("gases", []))
            if mi:
                append_log(self, f"Experiment params updated: "
                                 f"m_i = {mi/1.6605e-27:.2f} u", "ok")
            else:
                append_log(self, "Experiment params updated (no gas flow).",
                           "info")

    def _open_fit_model_dialog(self):
        dlg = FitModelDialog(self._fit_model, parent=self)
        if dlg.exec() == FitModelDialog.DialogCode.Accepted:
            self._fit_model = dlg.get_model_key()
            label = MODELS[self._fit_model]["label"]
            append_log(self, f"Fit model: {label}", "ok")

    def _open_instrument_dialog(self):
        # Single source of truth = self._instrument_opts.  Mirror the
        # current spnCompl value (mA, main panel) into compliance_A (A)
        # so the dialog opens with the *actual* current limit.
        self._instrument_opts["compliance_A"] = (
            float(self.spnCompl.value()) / 1000.0)
        dlg = InstrumentOptionsDialog(self._instrument_opts, parent=self)
        if dlg.exec() == InstrumentOptionsDialog.DialogCode.Accepted:
            self._instrument_opts = dlg.get_options()
            # Push compliance back into spnCompl so the existing sweep
            # path (_start_sweep / parent _toggle_connect) keeps working
            # without ever reading two diverging values.
            self.spnCompl.setValue(
                float(self._instrument_opts["compliance_A"]) * 1000.0)
            nplc = get_nplc(self._instrument_opts)
            rng = self._instrument_opts.get("current_range_A")
            rng_str = "AUTO" if rng is None else f"{rng:.4g} A"
            append_log(self, f"Instrument: NPLC={nplc}, "
                             f"prot={'ON' if self._instrument_opts['output_protection'] else 'OFF'}, "
                             f"range={rng_str}, "
                             f"compl={self._instrument_opts['compliance_A']*1000:.3g} mA",
                       "ok")
            # apply to live hardware if connected (duck-typed check)
            if self.smu and hasattr(self.smu, "enable_output_protection"):
                apply_instrument_options(self.smu, self._instrument_opts)
                append_log(self, "Instrument options applied.", "info")

    def _toggle_connect(self):
        """Override: sim options for FakeB2901v2, instrument options for real HW."""
        if self.smu is not None:
            super()._toggle_connect()
            return

        if self.chkSim.isChecked():
            compl_a = self.spnCompl.value() / 1000.0
            kw = sim_options_to_fake_kwargs(self._sim_options)
            self.smu = FakeB2901v2(current_compliance=compl_a, **kw)
            idn = self.smu.connect()
            from utils import set_led
            set_led(self.ledConn, self._theme["led_green"])
            self._set_compliance_led("idle")
            self.lblIdn.setText(idn)
            self.btnConnect.setText("Disconnect")
            self.chkSim.setEnabled(False)
            append_log(self, f"Simulation connected: {idn}", "ok")
            return

        # real hardware — connect then apply instrument options
        super()._toggle_connect()
        if self.smu and hasattr(self.smu, "enable_output_protection"):
            apply_instrument_options(self.smu, self._instrument_opts)
            nplc = get_nplc(self._instrument_opts)
            append_log(self, f"Instrument configured: NPLC={nplc}", "info")

    def get_config(self) -> dict:
        cfg = super().get_config()
        cfg["probe_parameters"] = self._probe_params
        cfg["simulation_options"] = self._sim_options
        cfg["experiment_parameters"] = self._experiment_params
        cfg["fit_model"] = self._fit_model
        cfg["instrument_options"] = self._instrument_opts
        cfg["save_csv"] = self.chkSave.isChecked()
        cfg["auto_analyze"] = self.chkAutoAnalyze.isChecked()
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        pp = cfg.get("probe_parameters")
        if pp and isinstance(pp, dict):
            self._probe_params.update(pp)
        so = cfg.get("simulation_options")
        if so and isinstance(so, dict):
            self._sim_options.update(so)
            self._apply_sim_to_smu()
        ep = cfg.get("experiment_parameters")
        if ep and isinstance(ep, dict):
            import copy
            self._experiment_params = copy.deepcopy(ep)
        fm = cfg.get("fit_model")
        if fm and fm in MODELS:
            self._fit_model = fm
        io = cfg.get("instrument_options")
        if io and isinstance(io, dict):
            self._instrument_opts.update(io)
        if "save_csv" in cfg:
            self.chkSave.setChecked(bool(cfg["save_csv"]))
        if "auto_analyze" in cfg:
            self.chkAutoAnalyze.setChecked(bool(cfg["auto_analyze"]))

    def _get_visa_text(self) -> str:
        """Safely extract VISA resource string from combo box."""
        try:
            txt = self.cmbVisa.currentText()
            parts = txt.split() if txt else []
            return parts[0] if parts else ""
        except (RuntimeError, IndexError):
            return ""

    def _save_csv(self, run_status="completed", failure_reason=""):
        """Save sweep data + probe params + analysis results to CSV."""
        n_pts = len(self._v_soll)
        # defensive: verify data buffers are consistent
        if n_pts == 0 and run_status == "completed":
            append_log(self, "WARNING: saving 'completed' with 0 data "
                             "points — check signal flow.", "warn")
        buf_lens = {
            "v_soll": len(self._v_soll), "v_ist": len(self._v_ist),
            "i_mean": len(self._i_mean), "i_std": len(self._i_std),
            "dir": len(self._directions), "compl": len(self._compliance),
        }
        if len(set(buf_lens.values())) > 1:
            append_log(self, f"WARNING: buffer length mismatch: "
                             f"{buf_lens}", "error")
        self._save_folder.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        path = self._make_csv_path(self._save_folder)
        meta = {
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Instrument": self.lblIdn.text(),
            "VISA": self._get_visa_text(),
            "V_start_V": f"{self.spnVstart.value():.6g}",
            "V_stop_V": f"{self.spnVstop.value():.6g}",
            "V_step_V": f"{self.spnVstep.value():.6g}",
            "Settle_s": f"{self.spnSettle.value():.4g}",
            "Compliance_A": f"{self.spnCompl.value()/1000:.6g}",
            "Averages": str(self.spnAvg.value()),
            "Bidirectional": str(self.chkBidir.isChecked()),
            "Points": str(len(self._v_soll)),
            "Run_Status": run_status,
            # Acquisition-method tag — resolved through the base-class
            # hook so LP-level subclasses can steer routing + tagging
            # via a single method override instead of module patching.
            "Method": self._csv_dataset_method(),
        }
        if failure_reason:
            meta["Failure_Reason"] = failure_reason
        meta.update(probe_params_for_csv(self._probe_params))
        # analysis results (if available)
        pp = getattr(self, "_last_plasma", None)
        if pp and not np.isnan(pp.get("Te_eV", float("nan"))):
            meta["Analysis_Te_eV"] = f"{pp['Te_eV']:.4f}"
            if not np.isnan(pp.get("Te_err_eV", float("nan"))):
                meta["Analysis_Te_err_eV"] = f"{pp['Te_err_eV']:.4f}"
            meta["Analysis_I_sat_A"] = f"{pp.get('I_sat_fit_A', 0):.4e}"
            meta["Analysis_R2"] = f"{pp.get('R2', 0):.6f}"
            meta["Analysis_NRMSE_pct"] = f"{pp.get('NRMSE', 0)*100:.2f}"
            meta["Analysis_Model"] = pp.get("label", "")
            meta["Analysis_Fit_Data"] = pp.get("fit_data", "")
            if not np.isnan(pp.get("n_i_m3", float("nan"))):
                meta["Analysis_n_i_m3"] = f"{pp['n_i_m3']:.4e}"
                meta["Analysis_v_Bohm_ms"] = f"{pp.get('v_Bohm_ms', 0):.1f}"
        try:
            self._write_csv(path, meta, self._v_soll, self._i_mean,
                            self._i_std, self._v_ist,
                            self._directions, self._compliance)
            # Remember the path so the Analyze path writes its
            # options sidecar next to this CSV (and not next to a
            # stale previous one).
            self._last_csv_path = path
            # verify written file
            written = path.read_text(encoding="utf-8")
            n_written = len([l for l in written.splitlines()
                            if l.strip() and not l.startswith("#")])
            n_expected = len(self._v_soll)
            print(f"[DLP] CSV saved: {path.name} "
                  f"({n_written}/{n_expected} data rows)", flush=True)
            if n_written < n_expected:
                append_log(self, f"WARNING: CSV truncated! "
                                 f"{n_written}/{n_expected} rows", "error")
            else:
                append_log(self, f"Saved: {path.name} "
                                 f"({n_written} data points)", "ok")
        except Exception as exc:
            import traceback
            print(f"[DLP] CSV save FAILED: {exc}", flush=True)
            traceback.print_exc()
            append_log(self, f"CSV save failed: {exc}", "error")

    # ── override: clear fit lines on sweep start ──────────────────

    def _start_sweep(self):
        """Full override — does NOT call super()._start_sweep().

        Key difference from parent: thread.quit is connected with
        QueuedConnection so that all point signals are delivered
        before the thread stops.
        """
        if self.smu is None:
            append_log(self, "Not connected.", "warn")
            return

        try:
            sweep = build_sweep_voltages(
                self.spnVstart.value(), self.spnVstop.value(),
                self.spnVstep.value(), self.chkBidir.isChecked())
        except ValueError as exc:
            append_log(self, str(exc), "error")
            return

        compl_a = self.spnCompl.value() / 1000.0
        try:
            self.smu.set_current_limit(compl_a)
        except Exception as exc:
            append_log(self, f"Compliance set failed: {exc}", "error")
            return

        # clear data buffers
        self._v_soll.clear(); self._v_ist.clear()
        self._i_mean.clear(); self._i_std.clear()
        self._directions.clear(); self._compliance.clear()
        # iter 4b: refresh compliance LED to "clean" for the new sweep.
        self._set_compliance_led("clear")

        # clear plot lines (parent + v2 overlays)
        for ln in (self.line_fwd, self.line_rev, self.line_compl,
                   self.line_fit_pos, self.line_fit_neg,
                   self.line_corrected, self.line_te_fit):
            ln.set_data([], [])
        self._clear_shading()
        self.ax.relim(); self.ax.autoscale_view()
        self.canvas.draw_idle()
        self.progress.setMaximum(len(sweep))
        self.progress.setValue(0)

        # sweep state for completion detection
        self._sweep_finished = False
        self._sweep_finalized = False
        self._sweep_elapsed = 0
        self._sweep_status = ""
        self._sweep_failure = ""
        self._sweep_n_expected = len(sweep)

        # Run worker in a Python thread with a polling timer to
        # drain data into the GUI.  This avoids QThread signal-delivery
        # issues where QueuedConnection events are batched until the
        # thread exits, blocking the GUI for the entire sweep.
        import threading
        import queue
        self._point_queue = queue.Queue()
        self._end_queue = queue.Queue()

        self._worker = DLPScanWorker(
            self.smu, sweep, self.spnSettle.value(), self.spnAvg.value())
        # connect worker signals to local queues (thread-safe)
        self._worker.point.connect(
            lambda *a: self._point_queue.put(("point", a)))
        self._worker.finished.connect(
            lambda elapsed: self._end_queue.put(("finished", elapsed)))
        self._worker.failed.connect(
            lambda msg: self._end_queue.put(("failed", msg)))
        self._worker.stopped.connect(
            lambda: self._end_queue.put(("stopped", None)))

        # poll timer drains queues into the GUI at 20 Hz
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_worker)
        self._poll_timer.start(50)

        # start worker in a plain Python thread
        self._thread = threading.Thread(
            target=self._worker.run, daemon=True)

        self._set_sweep_ui(True)
        self.lblStatus.setText("Sweeping ...")
        mode = "bidir" if self.chkBidir.isChecked() else "fwd"
        msg = (f"Sweep started ({mode}): "
               f"{sweep[0][0]:.3f} -> {sweep[-1][0]:.3f} V, "
               f"{len(sweep)} pts, settle={self.spnSettle.value()}s, "
               f"avg={self.spnAvg.value()}")
        append_log(self, msg, "ok")
        print(f"[DLP] {msg}", flush=True)
        self._thread.start()
        print(f"[DLP] Worker thread started", flush=True)

    def _poll_worker(self):
        """Timer callback: drain point queue into GUI, check for end."""
        import queue
        # drain all available points
        count = 0
        while True:
            try:
                kind, args = self._point_queue.get_nowait()
                if kind == "point":
                    self._on_point(*args)
                    count += 1
            except queue.Empty:
                break
        # check for end signal
        try:
            kind, payload = self._end_queue.get_nowait()
            self._poll_timer.stop()
            if kind == "finished":
                self._on_done(payload)
            elif kind == "failed":
                self._on_fail(payload)
            elif kind == "stopped":
                self._on_stopped()
        except queue.Empty:
            pass

    def _clear_shading(self):
        """Remove any existing fit-region shading patches."""
        for p in self._fit_shading:
            p.remove()
        self._fit_shading.clear()

    # ── override: sweep completion ──────────────────────────────────
    # Qt QueuedConnection does NOT guarantee FIFO ordering between
    # different signal types (point vs finished).  So _on_done may
    # arrive before all _on_point signals.  Solution: store the
    # pending status and perform analysis+save when the LAST point
    # arrives (idx == n-1), not when finished arrives.

    @Slot(int, int, float, float, float, float, bool, str)
    def _on_point(self, idx, n, v_soll, v_ist, i_mean, i_std,
                  compl, direction):
        """Override: store data, update plot at reduced rate, check done."""
        # store data (same as parent but without plot update every point)
        self._v_soll.append(float(v_soll))
        self._v_ist.append(float(v_ist))
        self._i_mean.append(float(i_mean))
        self._i_std.append(float(i_std))
        self._directions.append(str(direction))
        self._compliance.append(bool(compl))

        # iter 4b: keep the live compliance LED in sync (sticky red).
        self._set_compliance_led("hit" if any(self._compliance) else "clear")

        self.progress.setValue(idx + 1)
        self.lblStatus.setText(
            f"Point {idx+1}/{n} [{direction}] "
            f"V={v_ist:.3f} V  I={i_mean:.4e} A"
            f"{' COMPL' if compl else ''}")

        # update plot only every 5th point or on last point
        # (avoids matplotlib overload / segfaults)
        if idx % 5 == 0 or idx == n - 1:
            fwd_v = [v for v, d in zip(self._v_ist, self._directions)
                     if d == "fwd"]
            fwd_i = [i for i, d in zip(self._i_mean, self._directions)
                     if d == "fwd"]
            self.line_fwd.set_data(fwd_v, fwd_i)
            rev_v = [v for v, d in zip(self._v_ist, self._directions)
                     if d == "rev"]
            rev_i = [i for i, d in zip(self._i_mean, self._directions)
                     if d == "rev"]
            self.line_rev.set_data(rev_v, rev_i)
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()

        # check if sweep is complete
        if idx == n - 1 and getattr(self, "_sweep_finished", False):
            self._do_finalize()

    @Slot(float)
    def _on_done(self, elapsed):
        self._set_sweep_ui(False)
        self._sweep_elapsed = elapsed
        self._sweep_finished = True
        self._sweep_status = "completed"
        self._sweep_failure = ""
        print(f"[DLP] _on_done: {len(self._v_soll)} pts in buffer", flush=True)
        # if all points already received → finalize now
        n_expected = getattr(self, "_sweep_n_expected", 0)
        if len(self._v_soll) >= n_expected and n_expected > 0:
            self._do_finalize()

    @Slot(str)
    def _on_fail(self, msg):
        self._set_sweep_ui(False)
        self.lblStatus.setText(f"ERROR: {msg}")
        append_log(self, f"Sweep failed: {msg}", "error")
        self._sweep_finished = True
        self._sweep_status = "failed"
        self._sweep_failure = msg
        self._do_finalize()

    @Slot()
    def _on_stopped(self):
        self._set_sweep_ui(False)
        self.lblStatus.setText("Stopped")
        append_log(self, "Sweep stopped by user.", "warn")
        self._sweep_finished = True
        self._sweep_status = "aborted"
        self._sweep_failure = ""
        self._do_finalize()

    def closeEvent(self, event):
        """Override parent closeEvent for Python thread cleanup."""
        if self._worker:
            self._worker.request_stop()
        if hasattr(self, '_poll_timer'):
            self._poll_timer.stop()
        if self._thread and hasattr(self._thread, 'join'):
            self._thread.join(timeout=3.0)
        if self.smu:
            self.smu.close()
        # skip parent's closeEvent (it expects QThread)
        from PySide6.QtWidgets import QMainWindow
        QMainWindow.closeEvent(self, event)

    def _do_finalize(self):
        """Run analysis + save + stop thread. Called exactly once."""
        if getattr(self, "_sweep_finalized", False):
            return
        self._sweep_finalized = True
        # wait for worker thread to finish (it's a Python thread)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        n_pts = len(self._v_soll)
        elapsed = getattr(self, "_sweep_elapsed", 0)
        status = getattr(self, "_sweep_status", "completed")
        failure = getattr(self, "_sweep_failure", "")
        print(f"[DLP] _do_finalize: {n_pts} pts, status={status}", flush=True)
        if status == "completed":
            append_log(self, f"Sweep finished in {elapsed:.1f} s "
                             f"({n_pts} points).", "ok")
        if n_pts == 0 and status == "completed":
            append_log(self, "WARNING: 0 data points in buffer!", "error")
        # iter 4b: an aborted/failed sweep with zero data points must
        # not leave the leftover "clear" green from _start_sweep on
        # screen — it would falsely suggest the SMU swept cleanly.
        if n_pts == 0 and status != "completed":
            self._set_compliance_led("idle")
        # Auto-analysis is opt-in (default OFF) so atypical raw curves are
        # not obscured by fit lines / autoscale right after acquisition.
        if self.chkAutoAnalyze.isChecked() and n_pts > 0:
            try:
                self._run_analysis()
            except Exception as exc:
                append_log(self, f"Analysis error: {exc}", "warn")
        if self.chkSave.isChecked() and (n_pts > 0 or status == "completed"):
            self._save_csv(run_status=status, failure_reason=failure)

    # ── analysis ──────────────────────────────────────────────────

    def _run_analysis(self):
        """Fit saturation branches and update overlay lines in the plot.

        Math is delegated to :func:`dlp_double_analysis.compute_double_analysis`
        so the same Double-probe pipeline is the single source of
        truth for both V2 GUI and any pure / re-analysis caller.
        This method now owns only orchestration: read buffers →
        delegate math → store ``_last_*`` state → update plot →
        emit HTML / log / history.
        """
        if len(self._v_ist) < 10:
            append_log(self, "Not enough data for analysis.", "warn")
            return

        V = np.array(self._v_ist)
        I = np.array(self._i_mean)
        frac = self.spnSatFrac.value()

        from dlp_double_analysis import compute_double_analysis
        # Forward compliance + bootstrap configuration so the
        # analysis layer owns the clipping guard and CI decision.
        # The ``_exclude_clipped_in_fit`` / ``_bootstrap_te_ci``
        # attributes are set by LPMainWindow before this runs;
        # sensible defaults kick in for the standalone V2 entry
        # point so V2-only use keeps working.
        compliance_list = (list(self._compliance)
                            if self._compliance else None)
        exclude_clipped = bool(getattr(self,
                                         "_exclude_clipped_in_fit", True))
        bootstrap_enabled = bool(getattr(self, "_bootstrap_te_ci", False))
        bootstrap_n_iters = int(getattr(self,
                                          "_bootstrap_te_n_iters", 200))
        # Operator-supplied n_i uncertainty-budget inputs — the
        # attributes are set from DoubleAnalysisOptions by LP
        # before this slot runs.  Defaults to 0 via getattr, so
        # the pre-existing V2-standalone entry point keeps its
        # "fit_only" n_i CI behaviour.  The values are percentages
        # in the dataclass; compute_double_analysis takes a
        # dimensionless relative uncertainty, so we divide here.
        _area_rel = float(getattr(
            self, "_ni_probe_area_rel_unc_pct", 0.0)) / 100.0
        _mass_rel = float(getattr(
            self, "_ni_ion_mass_rel_unc_pct", 0.0)) / 100.0
        # Operator-selected ion-composition mode lives at the top of
        # ``experiment_params`` (written by the Experiment dialog's
        # new ion-composition combo).  Defaults to "molecular" so
        # pre-existing presets behave as before.
        _ion_mode = str(self._experiment_params.get(
            "ion_composition_mode", "molecular"))
        _ion_x = float(self._experiment_params.get("x_atomic", 0.0))
        _ion_dx = float(
            self._experiment_params.get("x_atomic_unc", 0.0))
        result = compute_double_analysis(
            V, I, fit_model=self._fit_model, sat_fraction=frac,
            probe_params=self._probe_params,
            gases=self._experiment_params.get("gases", []),
            compliance=compliance_list,
            exclude_clipped=exclude_clipped,
            bootstrap_enabled=bootstrap_enabled,
            bootstrap_n_iters=bootstrap_n_iters,
            probe_area_rel_unc=_area_rel,
            ion_mass_rel_unc=_mass_rel,
            ion_composition_mode=_ion_mode,
            ion_x_atomic=_ion_x,
            ion_x_atomic_unc=_ion_dx)
        # Expose the compliance summary so the LP override can
        # render it in the compact HTML without re-computing.
        self._last_compliance_info = result.get("compliance_info")
        if not result["ok"]:
            warns = "; ".join(result["warnings"]) or "unknown reason"
            append_log(self, f"Fit failed: {warns}", "warn")
            return

        fit = result["fit"]
        mfit = result["model_fit"]
        pp = result["plasma"]
        cmp = result["comparison"]
        ion_label_from_pure = result["ion_label"]

        self._last_fit = fit
        self._last_model_fit = mfit
        self._last_plasma = pp
        self._last_comparison = cmp

        # Surface a non-OK fit status in the acquisition log right
        # now, so the operator does not have to scroll the result
        # block to see that the reported numbers are untrustworthy.
        from dlp_fit_models import FitStatus, FAILURE_STATUSES
        _status = mfit.get("fit_status", FitStatus.OK) if mfit else FitStatus.OK
        if _status in FAILURE_STATUSES:
            _reason = (mfit.get("fit_error_reason")
                       or _status) if mfit else _status
            append_log(self, f"Fit failed ({_status}): {_reason}", "error")
        elif _status == FitStatus.POOR:
            _warn = (mfit.get("fit_warning_reason") or "graded poor")
            append_log(self, f"Fit converged but weak: {_warn}", "warn")

        # clear previous shading
        self._clear_shading()

        # shading for fit regions
        yl = self.ax.get_ylim()
        self._fit_shading.append(
            self.ax.axvspan(fit["v_pos_min"], float(V.max()),
                            color="#ff4444", alpha=0.08, zorder=0))
        self._fit_shading.append(
            self.ax.axvspan(float(V.min()), fit["v_neg_max"],
                            color="#ff8800", alpha=0.08, zorder=0))

        # fit lines — only within the fit region
        v_fp = np.linspace(fit["v_pos_min"], float(V.max()), 50)
        self.line_fit_pos.set_data(
            v_fp, fit["slope_pos"] * v_fp + fit["intercept_pos"])
        v_fn = np.linspace(float(V.min()), fit["v_neg_max"], 50)
        self.line_fit_neg.set_data(
            v_fn, fit["slope_neg"] * v_fn + fit["intercept_neg"])

        # corrected curve
        I_corr = correct_iv_curve(V, I, fit)
        self.line_corrected.set_data(V, I_corr)

        self.ax.relim()
        self.ax.autoscale_view()
        self.ax.legend(fontsize=7, loc="upper left")
        self.canvas.draw_idle()

        # detailed logging
        append_log(self, f"Fit regions: neg V <= {fit['v_neg_max']:.1f} V "
                         f"({fit['n_neg']} pts), "
                         f"pos V >= {fit['v_pos_min']:.1f} V "
                         f"({fit['n_pos']} pts)", "info")
        append_log(self, f"Slopes: neg={fit['slope_neg']:.3e}, "
                         f"pos={fit['slope_pos']:.3e}, "
                         f"avg={fit['slope_avg']:.3e} A/V", "info")
        append_log(self, f"I_sat: pos={fit['i_sat_pos']:.3e} A, "
                         f"neg={fit['i_sat_neg']:.3e} A", "ok")

        # transition zone shading (where T_e sensitivity is highest)
        self._fit_shading.append(
            self.ax.axvspan(fit["v_neg_max"], fit["v_pos_min"],
                            color="#cc44ff", alpha=0.06, zorder=0))

        # plot fit overlay with consistent label — model_fit + cmp
        # were computed by compute_double_analysis above and stored
        # on self._last_* already.
        if len(mfit.get("fit_V", [])) > 0:
            self.line_te_fit.set_data(mfit["fit_V"], mfit["fit_I"])
            self.line_te_fit.set_label(f"Model fit: {mfit['label']}")

        # result block — preserve V2's historical "first gas key"
        # ion_label semantics (gases[0].get("gas")) so existing
        # HTML / history snapshot tests stay byte-identical.
        gases = self._experiment_params.get("gases", [])
        ion_label = gases[0].get("gas", "") if gases else ""
        block = format_result_block(fit, pp, ion_label)
        _append_html_block(self, block)

        cmp_block = format_model_comparison(cmp, self._fit_model)
        _append_html_block(self, cmp_block)

        # Persist and surface the analysis.  The plain-text record
        # captures the same numbers but in a human-readable form so the
        # history file stays diff-friendly.  The dedicated analysis log
        # window keeps a visual, newest-first stream separate from the
        # acquisition log.
        plain = self._format_analysis_plain(fit, pp, ion_label, cmp)
        record = append_analysis_record(
            plain, path=self._analysis_history_path)
        # Only open the separate Analysis Log window when the operator
        # has explicitly opted in via the Double options dialog.
        # Default is OFF: the history file is still written above, the
        # compact HTML summary is still in the acquisition log, so no
        # information is lost by keeping the extra window closed.
        if getattr(self, "_show_analysis_log", False):
            try:
                win = show_analysis_window(self,
                                            history_path=self._analysis_history_path)
                win.prepend_html(block + cmp_block)
            except Exception as exc:  # pragma: no cover – purely defensive
                append_log(self, f"Analysis window error: {exc}", "warn")
        self._last_analysis_record = record

    def _format_analysis_plain(self, fit: dict, pp: dict,
                                ion_label: str, cmp: list[dict]) -> str:
        """Plain-text rendering for the persistent history file.

        When the fit did not produce trustworthy numbers (status not
        in {OK, POOR}) a ``Status``/``Failure reason`` pair leads the
        record, so ``analysis_history.txt`` preserves the cause of
        the failure alongside whatever partial numbers were logged.
        A POOR-but-converged fit is also annotated because
        reproducibility should flag the weak-fit case explicitly.
        """
        from dlp_fit_models import FitStatus, FAILURE_STATUSES

        lines: list[str] = []
        status = pp.get("fit_status", FitStatus.OK) if pp else FitStatus.OK
        if status in FAILURE_STATUSES:
            lines.append(f"Status       = {status}")
            reason = (pp.get("fit_error_reason")
                      or pp.get("fit_warning_reason"))
            if reason:
                lines.append(f"Failure reason = {reason}")
        elif status != FitStatus.OK:
            # POOR / WARNING — keep the banner short so success-path
            # history entries stay compact.
            lines.append(f"Status       = {status}")
            warn = pp.get("fit_warning_reason")
            if warn:
                lines.append(f"Warning      = {warn}")
        te = pp.get("Te_eV", float("nan"))
        te_err = pp.get("Te_err_eV", float("nan"))
        if not np.isnan(te):
            line = f"T_e  = {te:.3f}"
            if not np.isnan(te_err):
                line += f" +- {te_err:.3f}"
            line += " eV"
            lines.append(line)
        # 95 % CI for T_e — always written when a method is known so
        # the history record shows whether the uncertainty is
        # covariance-based (always-on) or bootstrap-based (opt-in).
        ci_lo = pp.get("Te_ci95_lo_eV")
        ci_hi = pp.get("Te_ci95_hi_eV")
        ci_method = pp.get("Te_ci_method")
        if ci_method and ci_method != "unavailable" \
                and ci_lo is not None and ci_hi is not None \
                and not np.isnan(ci_lo) and not np.isnan(ci_hi):
            lines.append(f"T_e 95% CI ({ci_method}) = "
                         f"[{ci_lo:.3f}, {ci_hi:.3f}] eV")
        elif ci_method == "unavailable" and not np.isnan(te):
            lines.append("T_e 95% CI = unavailable")
        i_sat = pp.get("I_sat_fit_A", float("nan"))
        if not np.isnan(i_sat):
            lines.append(f"I_sat = {i_sat:.3e} A")
            i_sat_lo = pp.get("I_sat_ci95_lo_A")
            i_sat_hi = pp.get("I_sat_ci95_hi_A")
            i_sat_method = pp.get("I_sat_ci_method")
            if (i_sat_method and i_sat_method != "unavailable"
                    and i_sat_lo is not None and i_sat_hi is not None
                    and not np.isnan(i_sat_lo)
                    and not np.isnan(i_sat_hi)):
                lines.append(
                    f"I_sat 95% CI ({i_sat_method}) = "
                    f"[{i_sat_lo:.3e}, {i_sat_hi:.3e}] A")
            elif i_sat_method == "unavailable":
                lines.append("I_sat 95% CI = unavailable")
        r2 = pp.get("R2", float("nan"))
        if not np.isnan(r2):
            lines.append(f"R^2   = {r2:.4f}")
        nrmse = pp.get("NRMSE", float("nan"))
        if not np.isnan(nrmse):
            lines.append(f"NRMSE = {nrmse*100:.2f} %")
        if pp.get("label"):
            lines.append(f"Model = {pp['label']}")
        n_i = pp.get("n_i_m3", float("nan"))
        if not np.isnan(n_i):
            gas = f", {ion_label}" if ion_label else ""
            lines.append(f"n_i   = {n_i:.3e} m^-3 "
                         f"(v_B={pp.get('v_Bohm_ms', 0):.0f} m/s{gas})")
            n_lo = pp.get("n_i_ci95_lo_m3")
            n_hi = pp.get("n_i_ci95_hi_m3")
            n_method = pp.get("n_i_ci_method")
            if (n_method and n_method != "unavailable"
                    and n_lo is not None and n_hi is not None
                    and not np.isnan(n_lo) and not np.isnan(n_hi)):
                # Scope note comes straight from the analysis layer
                # — "fit_only" / "fit+area" / "fit+mass" /
                # "fit+area+mass" — so a later reader can tell
                # exactly which uncertainty components were folded
                # into the width.
                n_note = pp.get("n_i_ci_note", "fit_only")
                lines.append(
                    f"n_i 95% CI ({n_note.replace('_', '-')}) = "
                    f"[{n_lo:.3e}, {n_hi:.3e}] m^-3")
            elif n_method == "unavailable":
                lines.append("n_i 95% CI = unavailable")
        lines.append(f"Fit region: neg<={fit['v_neg_max']:.2f} V "
                     f"({fit['n_neg']} pts), "
                     f"pos>={fit['v_pos_min']:.2f} V "
                     f"({fit['n_pos']} pts)")
        # Persist the compliance summary so a re-reader of the
        # history file can see whether clipping affected the fit.
        comp_info = getattr(self, "_last_compliance_info", None)
        if comp_info and int(comp_info.get("n_flagged", 0)) > 0:
            n_fl = int(comp_info["n_flagged"])
            n_to = int(comp_info.get("n_total", 0))
            frac = float(comp_info.get("clipped_fraction", 0.0))
            action = comp_info.get("action", "n/a")
            lines.append(
                f"Compliance = {n_fl}/{n_to} flagged "
                f"({frac*100:.1f}%), action={action}")
        if cmp:
            lines.append("Models:")
            for c in cmp:
                te_c = c.get("Te_eV", float("nan"))
                te_s = f"{te_c:.2f}" if not np.isnan(te_c) else "n/a"
                lines.append(f"  - {c.get('label', '?')}: "
                             f"T_e={te_s} eV "
                             f"R^2={c.get('R2', float('nan')):.4f}")
        return "\n".join(lines)


def main():
    """Launch the v2 acquisition GUI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    app = QApplication(sys.argv)
    win = DLPMainWindowV2()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
