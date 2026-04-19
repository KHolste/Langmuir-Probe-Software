"""User-configurable options for the Single-probe analysis.

Surfaces the previously-hardcoded Single-pipeline knobs through a
small Qt dialog so operators can review them without code changes.
Defaults are chosen to **match the current shipping behavior** —
opening the dialog and clicking OK without touching anything must
produce the same numeric result as the legacy hardcoded path.

The options dataclass is the single source of truth and can be
constructed from / serialised to a plain dict for persistence
through the existing JSON config (analog to ``_sim_options``).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


# Allowed values surfaced in the dialog.  Te-window factors map
# directly to the existing 3.0 / 5.0 fallback in fit_te_semilog;
# 2.0 is added as a "tighter window for low-noise sweeps" choice.
TE_WINDOW_FACTORS = (2.0, 3.0, 5.0)
COMPLIANCE_MODES = ("exclude_clipped", "include_all")
# Plasma-potential estimator: derivative knee detection vs the
# legacy log-linear / electron-sat intersection.  "auto" picks the
# derivative method when its quality flags as "high", else
# intersection — keeps current shipping behaviour as the default
# fallback path.
V_P_METHODS = ("auto", "derivative", "intersection")


@dataclass
class SingleAnalysisOptions:
    """Operator-facing knobs for the Single-probe pipeline ONLY.

    The compliance-handling and hysteresis-threshold knobs were
    historically here AND silently leaked into Double behaviour.
    They are kept here (Single still needs them for its own
    pipeline) but no longer affect Double — Double has its own
    independent copies in :class:`dlp_double_options.DoubleAnalysisOptions`.
    Each dialog now governs only its own method.

    Defaults match the current shipping behavior so adding the
    dialog is behavior-neutral.
    """
    # Width of the (V_f, V_f + factor*T_e_seed) semilog T_e fit
    # window.  3.0 is the historic default; 2.0 = tighter (less
    # bias from sheath onset), 5.0 = wider (more S/N at the cost
    # of more sheath influence).
    te_window_factor: float = 3.0

    # Robust Huber-loss linear fit for the semilog T_e step.
    # Default True since the previous hardening pass (outlier-
    # resistant against single clipped or noisy points).
    robust_te_fit: bool = True

    # Compliance handling.  "exclude_clipped" drops points whose
    # `_compliance` flag is True (current behavior).  "include_all"
    # uses every acquired point — restores the pre-hardening
    # behavior for operators who want to inspect raw data effects.
    compliance_mode: str = "exclude_clipped"

    # Hysteresis warning threshold (max |I_fwd - I_rev| as % of
    # |I|_max).  Below this, no hysteresis warning is emitted.
    hysteresis_threshold_pct: float = 5.0

    # Bootstrap T_e confidence-interval toggle.  Off by default —
    # the helper exists in dlp_single_analysis but is not yet
    # surfaced in the result HTML; this flag is the future hook.
    bootstrap_enabled: bool = False
    bootstrap_n_iters: int = 200

    # Plasma-potential estimator.  "auto" (default) picks the
    # derivative-based knee detector when it scores "high"
    # confidence, else falls back to the legacy log-linear /
    # electron-sat intersection.  Operators can lock the choice via
    # this option.
    v_p_method: str = "auto"

    # ------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SingleAnalysisOptions":
        if not data:
            return cls()
        defaults = asdict(cls())
        merged = {**defaults, **{k: v for k, v in data.items()
                                 if k in defaults}}
        # Light validation — fall back to defaults on bogus values.
        if merged["te_window_factor"] not in TE_WINDOW_FACTORS:
            merged["te_window_factor"] = 3.0
        if merged["compliance_mode"] not in COMPLIANCE_MODES:
            merged["compliance_mode"] = "exclude_clipped"
        try:
            merged["hysteresis_threshold_pct"] = float(
                merged["hysteresis_threshold_pct"])
        except (TypeError, ValueError):
            merged["hysteresis_threshold_pct"] = 5.0
        try:
            merged["bootstrap_n_iters"] = int(merged["bootstrap_n_iters"])
        except (TypeError, ValueError):
            merged["bootstrap_n_iters"] = 200
        if merged.get("v_p_method") not in V_P_METHODS:
            merged["v_p_method"] = "auto"
        return cls(**merged)


# ---------------------------------------------------------------------------
# Qt dialog — minimal scrollable form.  Imports kept local so the
# dataclass + helpers above stay importable in headless contexts.
# ---------------------------------------------------------------------------
def open_single_options_dialog(options: SingleAnalysisOptions,
                                 parent=None) -> SingleAnalysisOptions | None:
    """Modal helper.  Returns the (possibly modified) options on
    OK, or ``None`` on Cancel — same convention as Qt's standard
    dialog idioms.  Convenience wrapper around the class below.
    """
    dlg = SingleAnalysisOptionsDialog(options, parent=parent)
    if dlg.exec() == dlg.DialogCode.Accepted:
        return dlg.get_options()
    return None


class SingleAnalysisOptionsDialog:
    """Wrapper around QDialog that lazily builds the Qt widgets.
    Lives in a class but is intentionally Qt-import-isolated so
    importing :mod:`dlp_single_options` doesn't require Qt at all.
    """
    def __init__(self, options: SingleAnalysisOptions, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QGroupBox, QFormLayout, QComboBox,
            QCheckBox, QDoubleSpinBox, QSpinBox, QDialogButtonBox)
        self._options = options
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle("Single-Probe Analysis Options")
        self._dlg.setMinimumWidth(380)
        self.DialogCode = QDialog.DialogCode

        try:
            from utils import setup_scrollable_dialog
            layout, _scroll_top = setup_scrollable_dialog(self._dlg)
        except Exception:
            from PySide6.QtWidgets import QVBoxLayout
            layout = QVBoxLayout(self._dlg)
            _scroll_top = layout

        grp = QGroupBox("Te fit")
        form = QFormLayout(grp)
        self.cmbTeWindow = QComboBox()
        for f in TE_WINDOW_FACTORS:
            self.cmbTeWindow.addItem(f"{f:.1f} \u00d7 Te seed", f)
        idx = max(0, list(TE_WINDOW_FACTORS).index(options.te_window_factor))
        self.cmbTeWindow.setCurrentIndex(idx)
        self.cmbTeWindow.setToolTip(
            "Width of the semilog Te fit window above V_f.\n"
            "3.0 (default) matches the historic behaviour.\n"
            "2.0: tighter, less sheath influence (low-noise sweeps).\n"
            "5.0: wider, more S/N (noisy sweeps).")
        form.addRow("Window width:", self.cmbTeWindow)

        self.chkRobust = QCheckBox("Huber-loss (outlier resistant)")
        self.chkRobust.setChecked(bool(options.robust_te_fit))
        self.chkRobust.setToolTip(
            "Robust linear regression for the semilog Te slope.\n"
            "Falls back to OLS when SciPy is unavailable.")
        form.addRow("Method:", self.chkRobust)
        layout.addWidget(grp)

        grp2 = QGroupBox("Data handling")
        form2 = QFormLayout(grp2)
        self.cmbCompliance = QComboBox()
        self.cmbCompliance.addItem("Exclude clipped points (default)",
                                    "exclude_clipped")
        self.cmbCompliance.addItem("Include all points (legacy)",
                                    "include_all")
        idx_c = (0 if options.compliance_mode == "exclude_clipped" else 1)
        self.cmbCompliance.setCurrentIndex(idx_c)
        self.cmbCompliance.setToolTip(
            "How to treat points marked as compliance-clipped during "
            "the sweep.\nExclude: keep them out of the fit (default, "
            "transparent in HTML provenance row).\nInclude all: "
            "restore pre-hardening behaviour for direct comparison.")
        form2.addRow("Compliance:", self.cmbCompliance)

        self.spnHystThresh = QDoubleSpinBox()
        self.spnHystThresh.setRange(0.0, 100.0)
        self.spnHystThresh.setDecimals(1)
        self.spnHystThresh.setSuffix(" %")
        self.spnHystThresh.setValue(float(options.hysteresis_threshold_pct))
        self.spnHystThresh.setToolTip(
            "Forward/reverse divergence threshold as % of |I|_max.\n"
            "Above this, a 'plasma drift' warning is emitted.")
        form2.addRow("Hysteresis warn:", self.spnHystThresh)
        layout.addWidget(grp2)

        # Plasma-potential method selector — placed alongside the
        # other "Data handling" knobs so the operator sees it before
        # the advanced CI panel.  Auto is the safe default.
        self.cmbVpMethod = QComboBox()
        self.cmbVpMethod.addItem(
            "Auto (derivative when reliable, intersection else)",
            "auto")
        self.cmbVpMethod.addItem(
            "Derivative (smoothed dI/dV peak)", "derivative")
        self.cmbVpMethod.addItem(
            "Intersection (legacy log-linear vs e-sat)",
            "intersection")
        try:
            idx_v = list(V_P_METHODS).index(
                getattr(options, "v_p_method", "auto"))
        except ValueError:
            idx_v = 0
        self.cmbVpMethod.setCurrentIndex(idx_v)
        self.cmbVpMethod.setToolTip(
            "Estimator for the plasma potential V_p.\n"
            "Auto (default): derivative knee detection when its\n"
            "  prominence scores 'high' confidence; otherwise the\n"
            "  legacy intersection method.\n"
            "Derivative: forces the smoothed dI/dV peak; falls back\n"
            "  to intersection if the derivative cannot be evaluated.\n"
            "Intersection: forces the legacy method.\n"
            "Both candidates are always reported in the HTML for\n"
            "cross-checking.")
        form2.addRow("V_p method:", self.cmbVpMethod)

        grp3 = QGroupBox("Confidence interval (advanced)")
        form3 = QFormLayout(grp3)
        self.chkBootstrap = QCheckBox("Bootstrap Te CI")
        self.chkBootstrap.setChecked(bool(options.bootstrap_enabled))
        self.chkBootstrap.setToolTip(
            "Enable bootstrap 95% CI for Te.  Adds a small CPU cost\n"
            "per analysis; result currently logged, not yet shown\n"
            "in the compact HTML.")
        form3.addRow("CI:", self.chkBootstrap)
        self.spnBootIters = QSpinBox()
        self.spnBootIters.setRange(50, 2000)
        self.spnBootIters.setSingleStep(50)
        self.spnBootIters.setValue(int(options.bootstrap_n_iters))
        form3.addRow("Iterations:", self.spnBootIters)
        layout.addWidget(grp3)

        # OK / Cancel + an explicit "Help" button that opens the
        # full Single-probe analysis documentation dialog.  Help has
        # role HelpRole so Qt does NOT auto-close the parent dialog
        # when it is clicked.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Help)
        btns.accepted.connect(self._dlg.accept)
        btns.rejected.connect(self._dlg.reject)
        btns.helpRequested.connect(self._open_help)
        self._btn_box = btns
        _scroll_top.addWidget(btns)

        # Make the dialog comfortably tall on first open.  Width is
        # already constrained via setMinimumWidth above; height grew
        # past the window's default sizeHint after the V_p method
        # combobox + bootstrap CI panel were added, which on smaller
        # Windows-PC displays meant operators had to resize before
        # they could see the OK button.  500 px keeps every group
        # box visible on a 768-px display without scrolling.
        self._dlg.setMinimumHeight(500)
        self._dlg.resize(self._dlg.minimumWidth() + 40, 520)

    def _open_help(self) -> None:
        """Open the Single-probe analysis documentation dialog as a
        non-modal companion window.  Imported lazily so the options
        dataclass module stays Qt-import-free for headless tests."""
        try:
            from dlp_single_help import open_single_help_dialog
        except Exception as exc:  # pragma: no cover -- defensive
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self._dlg, "Help unavailable",
                f"Could not load help documentation:\n{exc!r}")
            return
        open_single_help_dialog(parent=self._dlg)

    def exec(self):
        return self._dlg.exec()

    def get_options(self) -> SingleAnalysisOptions:
        return SingleAnalysisOptions(
            te_window_factor=float(self.cmbTeWindow.currentData()),
            robust_te_fit=bool(self.chkRobust.isChecked()),
            compliance_mode=str(self.cmbCompliance.currentData()),
            hysteresis_threshold_pct=float(self.spnHystThresh.value()),
            bootstrap_enabled=bool(self.chkBootstrap.isChecked()),
            bootstrap_n_iters=int(self.spnBootIters.value()),
            v_p_method=str(self.cmbVpMethod.currentData()),
        )
