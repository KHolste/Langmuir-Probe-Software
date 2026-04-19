"""Double-probe analysis options + combined Fit-Model dialog.

Owns the analysis knobs that affect *only* the Double-probe path
so the Single-probe options dataclass stops carrying settings that
silently leak into Double behavior.

The dialog presents three things in one place:
  * the existing tanh fit-model selector (re-used from FitModelDialog
    via its public ComboBox population),
  * compliance handling (exclude_clipped vs include_all),
  * forward/reverse hysteresis warning threshold.

Operators see one dialog per method (Single → Single options;
Double → this combined dialog) — clean ownership, no surprises.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


COMPLIANCE_MODES = ("exclude_clipped", "include_all")


@dataclass
class DoubleAnalysisOptions:
    """Operator-facing knobs that govern the Double-probe pipeline.

    Defaults preserve the current shipping behavior: clipped-point
    filtering on (transparent in HTML), 5 % hysteresis warning
    threshold, bootstrap CI off (covariance-based CI is always
    populated when available), separate Analysis Log window off.
    """
    compliance_mode: str = "exclude_clipped"
    hysteresis_threshold_pct: float = 5.0
    # Non-parametric residual-bootstrap T_e 95 % CI.  Off by default
    # because it adds ~1 s per analysis on a typical 60-point sweep.
    # When off, the covariance-based CI from the fit is still shown.
    bootstrap_enabled: bool = False
    bootstrap_n_iters: int = 200
    # Separate Analysis Log window.  OFF by default — the compact
    # HTML summary in the acquisition log is usually enough for live
    # use, and an extra window popping up on every Analyze click is
    # distracting.  The persistent analysis_history.txt is still
    # written regardless of this flag.
    show_analysis_log: bool = False
    # Optional relative uncertainty inputs for the n_i CI budget.
    # Both default to 0 % which reproduces the pre-existing
    # "fit_only" behaviour (probe area and ion mass treated as
    # exact).  Operators who know their calibration can supply
    # realistic values; the dialog clamps to [0, 100] % so an
    # accidental keystroke cannot produce a nonsense CI.
    probe_area_rel_unc_pct: float = 0.0
    ion_mass_rel_unc_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "DoubleAnalysisOptions":
        if not data:
            return cls()
        defaults = asdict(cls())
        merged = {**defaults, **{k: v for k, v in data.items()
                                  if k in defaults}}
        if merged["compliance_mode"] not in COMPLIANCE_MODES:
            merged["compliance_mode"] = "exclude_clipped"
        try:
            merged["hysteresis_threshold_pct"] = float(
                merged["hysteresis_threshold_pct"])
        except (TypeError, ValueError):
            merged["hysteresis_threshold_pct"] = 5.0
        merged["bootstrap_enabled"] = bool(merged.get("bootstrap_enabled",
                                                       False))
        try:
            merged["bootstrap_n_iters"] = max(
                50, min(2000, int(merged.get("bootstrap_n_iters", 200))))
        except (TypeError, ValueError):
            merged["bootstrap_n_iters"] = 200
        merged["show_analysis_log"] = bool(
            merged.get("show_analysis_log", False))
        # Relative-uncertainty inputs: clamp to sane lab ranges so
        # a stray edit cannot make the n_i CI explode.  100 % is a
        # very loose hard cap — real lab calibrations are
        # typically <= 20 %.
        try:
            merged["probe_area_rel_unc_pct"] = max(0.0, min(100.0,
                float(merged.get("probe_area_rel_unc_pct", 0.0))))
        except (TypeError, ValueError):
            merged["probe_area_rel_unc_pct"] = 0.0
        try:
            merged["ion_mass_rel_unc_pct"] = max(0.0, min(100.0,
                float(merged.get("ion_mass_rel_unc_pct", 0.0))))
        except (TypeError, ValueError):
            merged["ion_mass_rel_unc_pct"] = 0.0
        return cls(**merged)


# ---------------------------------------------------------------------------
def open_double_options_dialog(current_model_key: str,
                                 options: DoubleAnalysisOptions,
                                 parent=None
                                 ) -> tuple[str, DoubleAnalysisOptions] | None:
    """Modal helper.  Returns ``(new_model_key, new_options)`` on
    OK, or ``None`` on Cancel."""
    dlg = DoubleAnalysisOptionsDialog(current_model_key, options,
                                        parent=parent)
    if dlg.exec() == dlg.DialogCode.Accepted:
        return dlg.get_model_key(), dlg.get_options()
    return None


class DoubleAnalysisOptionsDialog:
    """Combined dialog: tanh fit-model selector + Double-probe
    operator knobs.  Qt imports kept local so the dataclass can
    be imported in headless contexts."""

    def __init__(self, current_model_key: str,
                 options: DoubleAnalysisOptions, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QGroupBox, QFormLayout, QComboBox, QLabel,
            QDoubleSpinBox, QDialogButtonBox)
        from dlp_fit_models import MODELS, MODEL_KEYS, DEFAULT_MODEL

        from PySide6.QtWidgets import QCheckBox, QSpinBox

        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle("Double-Probe Analysis Options")
        self._dlg.setMinimumWidth(400)
        self.DialogCode = QDialog.DialogCode
        self._models = MODELS

        try:
            from utils import setup_scrollable_dialog
            layout, _scroll_top = setup_scrollable_dialog(self._dlg)
        except Exception:
            from PySide6.QtWidgets import QVBoxLayout
            layout = QVBoxLayout(self._dlg)
            _scroll_top = layout

        # Model selection — same combo content as FitModelDialog.
        grp_model = QGroupBox("Fit Model")
        form_m = QFormLayout(grp_model)
        self.cmbModel = QComboBox()
        for key in MODEL_KEYS:
            self.cmbModel.addItem(MODELS[key]["label"], key)
        idx = (MODEL_KEYS.index(current_model_key)
               if current_model_key in MODEL_KEYS else 0)
        self.cmbModel.setCurrentIndex(idx)
        self.cmbModel.currentIndexChanged.connect(self._update_info)
        form_m.addRow("Model:", self.cmbModel)
        self.lblFormula = QLabel(); self.lblFormula.setWordWrap(True)
        form_m.addRow("Formula:", self.lblFormula)
        self.lblParams = QLabel()
        form_m.addRow("Parameters:", self.lblParams)
        self.lblNote = QLabel(); self.lblNote.setWordWrap(True)
        self.lblNote.setStyleSheet("color: #8890a0; font-size: 11px;")
        form_m.addRow("", self.lblNote)
        layout.addWidget(grp_model)
        self._update_info()

        # Operator knobs — Double-only.
        grp_opts = QGroupBox("Data handling")
        form_o = QFormLayout(grp_opts)
        self.cmbCompliance = QComboBox()
        self.cmbCompliance.addItem("Exclude clipped points (default)",
                                    "exclude_clipped")
        self.cmbCompliance.addItem("Include all points (legacy)",
                                    "include_all")
        idx_c = (0 if options.compliance_mode == "exclude_clipped" else 1)
        self.cmbCompliance.setCurrentIndex(idx_c)
        self.cmbCompliance.setToolTip(
            "How to treat compliance-clipped points.\n"
            "Exclude (default): keep them out of the Double fit; "
            "the compact HTML reports a Compliance row when this "
            "happened.\nInclude all: legacy behaviour for direct "
            "comparison.")
        form_o.addRow("Compliance:", self.cmbCompliance)

        self.spnHystThresh = QDoubleSpinBox()
        self.spnHystThresh.setRange(0.0, 100.0)
        self.spnHystThresh.setDecimals(1)
        self.spnHystThresh.setSuffix(" %")
        self.spnHystThresh.setValue(float(options.hysteresis_threshold_pct))
        self.spnHystThresh.setToolTip(
            "Forward/reverse divergence warning threshold as % of "
            "|I|_max.\nAbove this value, the Double-side analysis "
            "logs a 'plasma drift' warn entry.")
        form_o.addRow("Hysteresis warn:", self.spnHystThresh)
        layout.addWidget(grp_opts)

        # ── Uncertainty group ──────────────────────────────────────
        # Exposes the bootstrap T_e CI toggle + iteration count.  The
        # covariance-based CI is always shown when the fit returns a
        # finite sigma; bootstrap tightens it for skewed/non-Gaussian
        # residuals at the cost of ~1 s per analyze click.
        grp_unc = QGroupBox("Uncertainty")
        form_u = QFormLayout(grp_unc)
        self.chkBootstrap = QCheckBox("Enable bootstrap 95 % CI for T_e")
        self.chkBootstrap.setChecked(bool(options.bootstrap_enabled))
        self.chkBootstrap.setToolTip(
            "Adds a non-parametric residual-resampling 95 % "
            "confidence interval for T_e.\n"
            "Off (default): only the cheap covariance-based CI is "
            "shown.\n"
            "On: roughly +1 s per analyze click on a 60-point sweep; "
            "recommended when residuals look skewed or the covariance "
            "CI looks suspiciously tight.")
        form_u.addRow("Bootstrap:", self.chkBootstrap)

        self.spnBootstrapN = QSpinBox()
        self.spnBootstrapN.setRange(50, 2000)
        self.spnBootstrapN.setSingleStep(50)
        self.spnBootstrapN.setValue(int(options.bootstrap_n_iters))
        self.spnBootstrapN.setToolTip(
            "Number of bootstrap resamples.\n"
            "200 is a good default; higher values tighten the CI "
            "edges but cost linear extra time.  Bounded to 50..2000 "
            "so an accidental keyboard slip cannot freeze the GUI.")
        self.spnBootstrapN.setEnabled(self.chkBootstrap.isChecked())
        # Simple UX wire: grey the count when the toggle is off, so
        # the dialog does not suggest the number is in effect.
        self.chkBootstrap.toggled.connect(self.spnBootstrapN.setEnabled)
        form_u.addRow("Iterations:", self.spnBootstrapN)

        # n_i uncertainty-budget inputs — both default to 0 % which
        # reproduces the pre-existing "fit_only" CI.  Non-zero values
        # fold into the variance formula
        #     (σ_n/n)² = (σ_I/I)² + ¼·(σ_T/T)² + (σ_A/A)² + ¼·(σ_m/m)²
        # and the ``n_i_ci_note`` label updates accordingly
        # ("fit_only" / "fit+area" / "fit+mass" / "fit+area+mass")
        # so the CI shown in the result block is always honestly
        # labelled by what it contains.
        self.spnAreaUnc = QDoubleSpinBox()
        self.spnAreaUnc.setRange(0.0, 100.0)
        self.spnAreaUnc.setDecimals(1)
        self.spnAreaUnc.setSingleStep(1.0)
        self.spnAreaUnc.setSuffix(" %")
        self.spnAreaUnc.setValue(
            float(options.probe_area_rel_unc_pct))
        self.spnAreaUnc.setToolTip(
            "Relative 1-σ uncertainty on the probe collection area, "
            "as a percent.\n"
            "0 % (default) reproduces the pre-existing 'fit-only' "
            "CI.\n"
            "Typical lab calibration: 5–15 %.\n"
            "Folded into the n_i 95 % CI via "
            "(σ_n/n)² += (σ_A/A)².")
        form_u.addRow("Area σ:", self.spnAreaUnc)

        self.spnMassUnc = QDoubleSpinBox()
        self.spnMassUnc.setRange(0.0, 100.0)
        self.spnMassUnc.setDecimals(1)
        self.spnMassUnc.setSingleStep(1.0)
        self.spnMassUnc.setSuffix(" %")
        self.spnMassUnc.setValue(
            float(options.ion_mass_rel_unc_pct))
        self.spnMassUnc.setToolTip(
            "Relative 1-σ uncertainty on the effective ion mass, "
            "as a percent.\n"
            "0 % (default) treats the gas-mix mean mass as exact. "
            "A mixed-gas discharge with an imperfect composition "
            "estimate might warrant 5–20 %.\n"
            "Folded into the n_i 95 % CI via "
            "(σ_n/n)² += ¼·(σ_m/m)² (v_Bohm ∝ √m_i^(-1)).")
        form_u.addRow("m_i σ:", self.spnMassUnc)

        # Separate analysis-log window.  Off by default — the compact
        # summary in the acquisition log is what most operators want
        # during live work.  The persistent history file is written
        # regardless of this flag, so history/audit never depends on
        # the window being open.
        self.chkShowLog = QCheckBox("Show analysis log window")
        self.chkShowLog.setChecked(bool(options.show_analysis_log))
        self.chkShowLog.setToolTip(
            "Open the separate Analysis Log window on every Analyze "
            "click.\n"
            "Off (default): only the compact summary is shown in the "
            "main acquisition log.\n"
            "On: a dedicated window also pops up with newest-first "
            "entries and lets you reload the history file from disk.\n"
            "The persistent history file is written either way.")
        form_u.addRow("Analysis log:", self.chkShowLog)

        layout.addWidget(grp_unc)

        # OK / Cancel + Help — Help opens the new Double-probe help
        # dialog as a companion window.  HelpRole keeps the parent
        # dialog open while the help window is consulted.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Help)
        btns.accepted.connect(self._dlg.accept)
        btns.rejected.connect(self._dlg.reject)
        btns.helpRequested.connect(self._open_help)
        _scroll_top.addWidget(btns)

    def _update_info(self):
        key = self.cmbModel.currentData()
        md = self._models.get(key, {})
        self.lblFormula.setText(md.get("formula", ""))
        pnames = md.get("param_names", [])
        punits = md.get("param_units", [])
        self.lblParams.setText(", ".join(
            f"{n} [{u}]" if u else n for n, u in zip(pnames, punits)))
        if md.get("on_corrected"):
            self.lblNote.setText("Fits sheath-corrected data.")
        else:
            self.lblNote.setText(
                "Fits raw data (includes slope in model).")

    def exec(self):
        return self._dlg.exec()

    def get_model_key(self) -> str:
        return self.cmbModel.currentData()

    def get_options(self) -> DoubleAnalysisOptions:
        return DoubleAnalysisOptions(
            compliance_mode=str(self.cmbCompliance.currentData()),
            hysteresis_threshold_pct=float(self.spnHystThresh.value()),
            bootstrap_enabled=bool(self.chkBootstrap.isChecked()),
            bootstrap_n_iters=int(self.spnBootstrapN.value()),
            show_analysis_log=bool(self.chkShowLog.isChecked()),
            probe_area_rel_unc_pct=float(self.spnAreaUnc.value()),
            ion_mass_rel_unc_pct=float(self.spnMassUnc.value()),
        )

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------
    def _open_help(self) -> None:
        """Open the Double-probe analysis documentation dialog.

        Lazy-imported so the options dataclass module stays
        Qt-import-free for headless tests.
        """
        try:
            from dlp_double_help import open_double_help_dialog
        except Exception as exc:  # pragma: no cover - defensive
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self._dlg, "Help unavailable",
                f"Could not load Double-probe help:\n{exc!r}")
            return
        open_double_help_dialog(parent=self._dlg)
