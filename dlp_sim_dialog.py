"""
Simulation-options dialog for the Double-Langmuir-Probe Monitor v2.

Configures noise, asymmetry, offset, drift, and correlation parameters
that are passed to FakeB2901v2 on connection.  Includes presets for
common use cases: ideal, realistic-light, realistic-medium.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDoubleSpinBox, QComboBox,
    QGroupBox, QDialogButtonBox,
)

PRESETS: dict[str, dict] = {
    "Ideal": {
        "noise_uA": 0.0,
        "noise_corr": 0.0,
        "asymmetry_pct": 0.0,
        "offset_uA": 0.0,
        "drift_nA_per_pt": 0.0,
    },
    "Realistic (light)": {
        "noise_uA": 1.0,
        "noise_corr": 0.3,
        "asymmetry_pct": 2.0,
        "offset_uA": 0.5,
        "drift_nA_per_pt": 0.1,
    },
    "Realistic (medium)": {
        "noise_uA": 5.0,
        "noise_corr": 0.7,
        "asymmetry_pct": 5.0,
        "offset_uA": 2.0,
        "drift_nA_per_pt": 0.5,
    },
}

DEFAULT_SIM_OPTIONS: dict = dict(PRESETS["Ideal"])


def sim_options_to_fake_kwargs(opts: dict) -> dict:
    """Convert GUI-unit sim options to FakeB2901v2 constructor kwargs.

    The ``model`` key is forwarded only when explicitly present in
    ``opts`` so existing call sites continue to inherit the
    FakeB2901v2 default (``double_langmuir``).  Callers that drive
    the sim from the method-mode selector (e.g. LPMainWindow) write
    ``opts["model"]`` themselves before connecting.
    """
    kw = {
        "noise_std": opts.get("noise_uA", 0) * 1e-6,
        "noise_corr": opts.get("noise_corr", 0),
        "asymmetry": opts.get("asymmetry_pct", 0) / 100.0,
        "i_offset": opts.get("offset_uA", 0) * 1e-6,
        "drift_per_point": opts.get("drift_nA_per_pt", 0) * 1e-9,
    }
    model = opts.get("model")
    if model:
        kw["model"] = model
    return kw


class SimulationOptionsDialog(QDialog):
    """Dialog for configuring simulation realism parameters."""

    def __init__(self, opts: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Simulation Options")
        self.setMinimumWidth(320)
        self._opts = dict(DEFAULT_SIM_OPTIONS)
        if opts:
            self._opts.update(opts)

        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)

        # preset selector
        grp_pre = QGroupBox("Preset")
        pl = QFormLayout(grp_pre)
        self.cmbPreset = QComboBox()
        self.cmbPreset.addItems(list(PRESETS.keys()))
        self.cmbPreset.currentTextChanged.connect(self._apply_preset)
        pl.addRow("Load preset:", self.cmbPreset)
        layout.addWidget(grp_pre)

        # parameters
        grp = QGroupBox("Parameters")
        form = QFormLayout(grp)

        self.spnNoise = _spin(0, 100, 2, " \u00b5A",
                               self._opts.get("noise_uA", 0))
        self.spnNoise.setToolTip("White noise standard deviation")
        form.addRow("Noise level:", self.spnNoise)

        self.spnCorr = _spin(0, 0.99, 2, "",
                              self._opts.get("noise_corr", 0))
        self.spnCorr.setToolTip("Noise autocorrelation (0 = white, "
                                 "0.9 = slow wander)")
        form.addRow("Noise correlation:", self.spnCorr)

        self.spnAsym = _spin(0, 20, 1, " %",
                              self._opts.get("asymmetry_pct", 0))
        self.spnAsym.setToolTip("Branch asymmetry: |I_sat+| vs |I_sat-|")
        form.addRow("Asymmetry:", self.spnAsym)

        self.spnOffset = _spin(-50, 50, 2, " \u00b5A",
                                self._opts.get("offset_uA", 0))
        self.spnOffset.setToolTip("Constant current offset")
        form.addRow("Current offset:", self.spnOffset)

        self.spnDrift = _spin(-10, 10, 2, " nA/pt",
                               self._opts.get("drift_nA_per_pt", 0))
        self.spnDrift.setToolTip("Linear current drift per measurement point")
        form.addRow("Drift:", self.spnDrift)

        layout.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        _scroll_top.addWidget(btns)

    def _apply_preset(self, name: str):
        p = PRESETS.get(name)
        if not p:
            return
        self.spnNoise.setValue(p["noise_uA"])
        self.spnCorr.setValue(p["noise_corr"])
        self.spnAsym.setValue(p["asymmetry_pct"])
        self.spnOffset.setValue(p["offset_uA"])
        self.spnDrift.setValue(p["drift_nA_per_pt"])

    def get_options(self) -> dict:
        return {
            "noise_uA": self.spnNoise.value(),
            "noise_corr": self.spnCorr.value(),
            "asymmetry_pct": self.spnAsym.value(),
            "offset_uA": self.spnOffset.value(),
            "drift_nA_per_pt": self.spnDrift.value(),
        }


def _spin(lo, hi, decimals, suffix, value):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    if suffix:
        s.setSuffix(suffix)
    s.setValue(value)
    return s
