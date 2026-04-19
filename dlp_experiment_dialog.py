"""
Experiment-parameter dialog for the Double-Langmuir-Probe Monitor v2.

Gas species selection, flow rates (sccm / mg/s), and effective ion mass
calculation for up to 3 gas components.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QDoubleSpinBox, QComboBox, QLabel, QGroupBox, QDialogButtonBox,
)

# ── gas data (M in g/mol = Da for atomic ions) ──────────────────────

GAS_DATA: dict[str, float] = {
    "Ar":  39.948,
    "He":   4.003,
    "Ne":  20.180,
    "Xe": 131.293,
    "Kr":  83.798,
    "N2":  28.014,
    "O2":  31.998,
    "H2":   2.016,
}

# STP conversion: 1 sccm → mol/s  (0 °C, 101325 Pa, ideal gas)
SCCM_TO_MOL_S = 101325.0 * (1e-6 / 60.0) / (8.314 * 273.15)  # ≈ 7.436e-7


def sccm_to_mgs(sccm: float, M_gmol: float) -> float:
    """Convert sccm to mg/s for a gas with molar mass *M_gmol* (g/mol)."""
    return sccm * SCCM_TO_MOL_S * M_gmol * 1000.0  # g→mg


def mgs_to_sccm(mgs: float, M_gmol: float) -> float:
    """Convert mg/s to sccm."""
    if M_gmol <= 0:
        return 0.0
    return mgs / (SCCM_TO_MOL_S * M_gmol * 1000.0)


def effective_ion_mass_kg(gases: list[dict]) -> float | None:
    """Flow-weighted mean ion mass in kg from a gas list.

    Each entry: {"gas": str, "flow_sccm": float}.
    Returns None if total flow is zero.
    """
    total = sum(g.get("flow_sccm", 0) for g in gases)
    if total <= 0:
        return None
    m_sum = 0.0
    for g in gases:
        f = g.get("flow_sccm", 0)
        M = GAS_DATA.get(g.get("gas", ""), 0)
        m_sum += f * M
    # g/mol → kg: *1e-3, then per Avogadro: /6.022e23
    return (m_sum / total) * 1.6605e-27  # u → kg


DEFAULT_EXPERIMENT_PARAMS: dict = {
    "gases": [
        {"gas": "Ar", "flow_sccm": 0.1},
        {"gas": "", "flow_sccm": 0.0},
        {"gas": "", "flow_sccm": 0.0},
    ],
}


class ExperimentParameterDialog(QDialog):
    """Dialog for gas species and flow-rate entry (up to 3 components)."""

    def __init__(self, params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Experiment Parameters")
        self.setMinimumWidth(420)
        self._params = _deep_copy_params(params or DEFAULT_EXPERIMENT_PARAMS)

        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)
        grp = QGroupBox("Gas Composition")
        grid = QGridLayout(grp)

        grid.addWidget(QLabel("Gas"), 0, 0)
        grid.addWidget(QLabel("Flow (sccm)"), 0, 1)
        grid.addWidget(QLabel("Flow (mg/s)"), 0, 2)

        self._gas_combos: list[QComboBox] = []
        self._flow_spins: list[QDoubleSpinBox] = []
        self._mgs_labels: list[QLabel] = []

        gases = self._params.get("gases", DEFAULT_EXPERIMENT_PARAMS["gases"])
        for i in range(3):
            g = gases[i] if i < len(gases) else {"gas": "", "flow_sccm": 0}
            cmb = QComboBox()
            cmb.addItems(["(none)"] + sorted(GAS_DATA.keys()))
            cmb.setMinimumWidth(80)
            cmb.setMaxVisibleItems(len(GAS_DATA) + 1)
            gas_name = g.get("gas", "")
            _set_combo(cmb, gas_name if gas_name else "(none)")
            cmb.currentTextChanged.connect(self._update_mgs)
            grid.addWidget(cmb, i + 1, 0)

            spn = QDoubleSpinBox()
            spn.setRange(0, 9999)
            spn.setDecimals(1)
            spn.setSuffix(" sccm")
            spn.setValue(g.get("flow_sccm", 0))
            spn.valueChanged.connect(self._update_mgs)
            grid.addWidget(spn, i + 1, 1)

            lbl = QLabel("0.000 mg/s")
            lbl.setMinimumWidth(90)
            grid.addWidget(lbl, i + 1, 2)

            self._gas_combos.append(cmb)
            self._flow_spins.append(spn)
            self._mgs_labels.append(lbl)

        layout.addWidget(grp)

        # summary
        self._lblSummary = QLabel()
        layout.addWidget(self._lblSummary)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        _scroll_top.addWidget(btns)

        self._update_mgs()

    def _gas_name(self, i: int) -> str:
        t = self._gas_combos[i].currentText().strip()
        return "" if t == "(none)" else t

    def _update_mgs(self):
        total_sccm = 0.0
        m_sum = 0.0
        for i in range(3):
            gas = self._gas_name(i)
            sccm = self._flow_spins[i].value()
            M = GAS_DATA.get(gas, 0)
            if M > 0 and sccm > 0:
                mgs = sccm_to_mgs(sccm, M)
                self._mgs_labels[i].setText(f"{mgs:.3f} mg/s")
                total_sccm += sccm
                m_sum += sccm * M
            else:
                self._mgs_labels[i].setText("— mg/s")

        if total_sccm > 0:
            m_eff = m_sum / total_sccm
            self._lblSummary.setText(
                f"Total: {total_sccm:.1f} sccm | "
                f"Mean M = {m_eff:.2f} g/mol ({m_eff:.2f} u)")
        else:
            self._lblSummary.setText("No gas flow configured.")

    def get_params(self) -> dict:
        gases = []
        for i in range(3):
            gas = self._gas_name(i)
            sccm = self._flow_spins[i].value()
            if gas and sccm > 0:
                gases.append({"gas": gas, "flow_sccm": sccm})
        return {"gases": gases}


def _set_combo(combo, text):
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentText(text)


def _deep_copy_params(p):
    import copy
    return copy.deepcopy(p)
