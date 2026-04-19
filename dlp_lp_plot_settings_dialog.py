"""Plot Settings dialog for the LP-measurement window (Te / n_e).

Mirrors the design of :mod:`dlp_plot_settings_dialog` (small, modal,
no scroll wrap because the option count stays low) and bundles two
axes in one dialog so the operator only needs one click.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)


_AXIS_BOUND = 1.0e30


class LPPlotSettingsDialog(QDialog):
    """Edit Auto/Manual + Min/Max for the Te and n_e axes in one dialog."""

    def __init__(self, ax_te, ax_ne,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot Settings — Te / n_e")
        self.setMinimumWidth(360)
        self._ax_te = ax_te
        self._ax_ne = ax_ne

        layout = QVBoxLayout(self)

        # Te axis
        self.grpTe = self._build_axis_group("Te axis [eV]", ax_te,
                                             logscale_default=False)
        layout.addWidget(self.grpTe)

        # n_e axis (log)
        self.grpNe = self._build_axis_group("n_e axis [m⁻³]", ax_ne,
                                             logscale_default=True)
        layout.addWidget(self.grpNe)

        # Reset row
        row = QHBoxLayout()
        self.btnReset = QPushButton("Reset view")
        self.btnReset.setToolTip(
            "Re-enable autoscale on both Te and n_e.")
        self.btnReset.clicked.connect(self._reset_all)
        row.addWidget(self.btnReset)
        row.addStretch(1)
        layout.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ------------------------------------------------------------------
    def _build_axis_group(self, title: str, ax,
                          *, logscale_default: bool) -> QGroupBox:
        grp = QGroupBox(title)
        fl = QFormLayout(grp)
        lo, hi = (float(v) for v in ax.get_ylim())
        chk = QCheckBox("Auto")
        chk.setChecked(bool(ax.get_autoscaley_on()))
        fl.addRow(chk)
        spn_lo = self._make_spin(lo)
        spn_hi = self._make_spin(hi)
        fl.addRow("Y min:", spn_lo)
        fl.addRow("Y max:", spn_hi)
        chk.toggled.connect(lambda on, a=spn_lo, b=spn_hi:
                            (a.setEnabled(not on), b.setEnabled(not on)))
        spn_lo.setEnabled(not chk.isChecked())
        spn_hi.setEnabled(not chk.isChecked())
        # cache widgets onto the groupbox so apply_to_axes() can read them
        grp._chk = chk            # type: ignore[attr-defined]
        grp._spn_lo = spn_lo      # type: ignore[attr-defined]
        grp._spn_hi = spn_hi      # type: ignore[attr-defined]
        grp._is_log = logscale_default  # type: ignore[attr-defined]
        return grp

    @staticmethod
    def _make_spin(value: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setDecimals(6)
        s.setRange(-_AXIS_BOUND, _AXIS_BOUND)
        s.setSingleStep(0.1)
        try:
            s.setValue(float(value))
        except Exception:
            s.setValue(0.0)
        return s

    def _reset_all(self) -> None:
        for grp in (self.grpTe, self.grpNe):
            grp._chk.setChecked(True)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    def get_settings(self) -> dict:
        def _read(grp):
            return {
                "auto": bool(grp._chk.isChecked()),
                "lo": float(grp._spn_lo.value()),
                "hi": float(grp._spn_hi.value()),
            }
        return {"te": _read(self.grpTe), "ne": _read(self.grpNe)}

    def apply_to_axes(self) -> None:
        s = self.get_settings()
        self._apply_one(self._ax_te, s["te"])
        self._apply_one(self._ax_ne, s["ne"])

    @staticmethod
    def _apply_one(ax, conf: dict) -> None:
        if conf["auto"]:
            ax.set_autoscaley_on(True)
            ax.relim()
            ax.autoscale_view(scalex=False, scaley=True)
        else:
            lo, hi = conf["lo"], conf["hi"]
            if hi <= lo:
                hi = lo + 1e-12
            ax.set_autoscaley_on(False)
            try:
                ax.set_ylim(lo, hi)
            except Exception:
                pass
