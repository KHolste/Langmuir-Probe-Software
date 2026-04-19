"""
Probe-parameter dialog for the Double-Langmuir-Probe Monitor v2.

Provides a QDialog for entering, viewing, and persisting the geometric
and experimental parameters of a double-Langmuir probe.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QDoubleSpinBox, QComboBox, QLineEdit, QTextEdit,
    QPushButton, QGroupBox, QLabel, QDialogButtonBox, QCheckBox,
)

GEOMETRIES = ["cylindrical", "planar"]
MATERIALS = ["tungsten", "molybdenum", "platinum", "stainless steel", "other"]

DEFAULT_PROBE_PARAMS: dict = {
    "probe_id": "",
    "geometry": "cylindrical",
    "electrode_length_mm": 5.0,
    "electrode_radius_mm": 0.1,
    "electrode_area_mm2": None,        # None → auto-compute from geometry
    "electrode_spacing_mm": 3.0,
    "material": "tungsten",
    "notes": "",
}


def compute_electrode_area(geometry: str, length_mm: float,
                            radius_mm: float) -> float:
    """Compute the collecting area of one electrode in mm²."""
    if geometry == "cylindrical":
        return 2.0 * math.pi * radius_mm * length_mm
    else:  # planar
        return math.pi * radius_mm ** 2


def probe_params_for_csv(params: dict) -> dict:
    """Return a flat dict of probe params suitable for CSV metadata."""
    area = params.get("electrode_area_mm2")
    if area is None:
        area = compute_electrode_area(
            params.get("geometry", "cylindrical"),
            params.get("electrode_length_mm", 0),
            params.get("electrode_radius_mm", 0),
        )
    out = {}
    if params.get("probe_id"):
        out["Probe_ID"] = params["probe_id"]
    out["Probe_Geometry"] = params.get("geometry", "")
    out["Exposed_Length_mm"] = f"{params.get('electrode_length_mm', 0):.3f}"
    out["Electrode_Radius_mm"] = f"{params.get('electrode_radius_mm', 0):.4f}"
    out["Geometric_Area_mm2"] = f"{area:.4f}"
    out["Electrode_Spacing_mm"] = f"{params.get('electrode_spacing_mm', 0):.2f}"
    if params.get("material"):
        out["Probe_Material"] = params["material"]
    return out


class ProbeParameterDialog(QDialog):
    """Dialog for entering double-Langmuir probe geometry parameters."""

    def __init__(self, params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Probe Parameters")
        self.setMinimumWidth(360)
        self._params = dict(DEFAULT_PROBE_PARAMS)
        if params:
            self._params.update(params)

        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)

        # ID + Material row
        grp_id = QGroupBox("Identification")
        form_id = QFormLayout(grp_id)
        self.txtProbeId = QLineEdit(self._params.get("probe_id", ""))
        form_id.addRow("Probe ID:", self.txtProbeId)
        self.cmbMaterial = QComboBox()
        self.cmbMaterial.addItems(MATERIALS)
        self.cmbMaterial.setEditable(True)
        _set_combo(self.cmbMaterial, self._params.get("material", "tungsten"))
        form_id.addRow("Material:", self.cmbMaterial)
        layout.addWidget(grp_id)

        # Geometry
        grp_geo = QGroupBox("Geometry")
        form_geo = QFormLayout(grp_geo)
        self.cmbGeometry = QComboBox()
        self.cmbGeometry.addItems(GEOMETRIES)
        _set_combo(self.cmbGeometry, self._params.get("geometry", "cylindrical"))
        self.cmbGeometry.currentTextChanged.connect(self._update_area)
        form_geo.addRow("Type:", self.cmbGeometry)

        self.spnLength = _make_spin(0.01, 200.0, 2, " mm",
                                     self._params.get("electrode_length_mm", 5))
        self.spnLength.setToolTip(
            "Exposed electrode length that is actually in contact "
            "with the plasma (not the part inside the holder).")
        self.spnLength.valueChanged.connect(self._update_area)
        form_geo.addRow("Exposed length:", self.spnLength)

        self.spnRadius = _make_spin(0.001, 50.0, 3, " mm",
                                     self._params.get("electrode_radius_mm", 0.1))
        self.spnRadius.valueChanged.connect(self._update_area)
        form_geo.addRow("Electrode radius:", self.spnRadius)

        self.spnSpacing = _make_spin(0.1, 100.0, 2, " mm",
                                      self._params.get("electrode_spacing_mm", 3))
        form_geo.addRow("Electrode spacing:", self.spnSpacing)

        # area: auto-computed geometric surface, optional manual override
        area_row = QHBoxLayout()
        self.spnArea = _make_spin(0.001, 10000.0, 4, " mm\u00b2", 0)
        self.spnArea.setToolTip(
            "Geometric surface area of one electrode (A_geo).\n"
            "This is NOT the effective collecting area A_eff,\n"
            "which depends on sheath expansion and plasma conditions.")
        self.chkAreaAuto = QCheckBox("auto")
        self.chkAreaAuto.setChecked(self._params.get("electrode_area_mm2") is None)
        self.chkAreaAuto.toggled.connect(self._on_area_auto_toggled)
        area_row.addWidget(self.spnArea)
        area_row.addWidget(self.chkAreaAuto)
        form_geo.addRow("Geometric area (A_geo):", area_row)
        self._update_area()

        if self._params.get("electrode_area_mm2") is not None:
            self.spnArea.setValue(self._params["electrode_area_mm2"])

        layout.addWidget(grp_geo)

        # Notes
        grp_notes = QGroupBox("Notes")
        nv = QVBoxLayout(grp_notes)
        self.txtNotes = QTextEdit()
        self.txtNotes.setMaximumHeight(60)
        self.txtNotes.setPlainText(self._params.get("notes", ""))
        nv.addWidget(self.txtNotes)
        layout.addWidget(grp_notes)

        # buttons — pinned outside the scroll area so they stay
        # visible on small displays while the form scrolls.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        _scroll_top.addWidget(btns)

    def _on_area_auto_toggled(self, auto: bool):
        self.spnArea.setReadOnly(auto)
        if auto:
            self._update_area()

    def _update_area(self):
        if self.chkAreaAuto.isChecked():
            a = compute_electrode_area(
                self.cmbGeometry.currentText(),
                self.spnLength.value(),
                self.spnRadius.value(),
            )
            self.spnArea.setValue(a)

    def get_params(self) -> dict:
        """Return the current parameter values as a dict."""
        area_auto = self.chkAreaAuto.isChecked()
        return {
            "probe_id": self.txtProbeId.text().strip(),
            "geometry": self.cmbGeometry.currentText(),
            "electrode_length_mm": self.spnLength.value(),
            "electrode_radius_mm": self.spnRadius.value(),
            "electrode_area_mm2": None if area_auto else self.spnArea.value(),
            "electrode_spacing_mm": self.spnSpacing.value(),
            "material": self.cmbMaterial.currentText(),
            "notes": self.txtNotes.toPlainText().strip(),
        }

    def get_geometric_area_mm2(self) -> float:
        """Return the geometric electrode surface area (auto or manual)."""
        return self.spnArea.value()

    # backward compat alias
    get_effective_area_mm2 = get_geometric_area_mm2


# ── helpers ──────────────────────────────────────────────────────────

def _make_spin(lo, hi, decimals, suffix, value):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setValue(value)
    return s


def _set_combo(combo: QComboBox, text: str):
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentText(text)
