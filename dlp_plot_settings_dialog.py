"""Plot Settings dialog for the DLP main window.

Bundles the small, scientifically useful subset of plot controls so
the main window stays uncluttered:

* per-axis auto / manual range with min/max spinboxes,
* grid toggle,
* legend toggle,
* "Reset view" — re-enable autoscale on both axes.

The dialog is structurally consistent with the other DLP dialogs:
constructed via ``setup_scrollable_dialog`` so it stays usable on
small displays even if the option list grows in a future iteration.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)


_AXIS_BOUND = 1.0e9   # spinbox cap — well outside any real DLP plot range


class PlotSettingsDialog(QDialog):
    """Modal dialog that edits xlim/ylim/grid/legend on a Matplotlib Axes.

    The dialog is non-destructive: nothing is written back to the axes
    until :meth:`apply_to_axes` is called (or the helper method
    :meth:`accept_and_apply` after Ok).
    """

    def __init__(self, ax, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot Settings")
        self.setMinimumWidth(340)
        self._ax = ax

        # Plain QVBoxLayout — this dialog is small (8-ish widgets) and
        # never needs a scroll wrapper.  Avoids the QScrollArea churn
        # that can destabilise PySide6 + offscreen test runs when many
        # dialogs spin up back-to-back.
        layout = QVBoxLayout(self)
        scroll_top = layout

        # snapshot current state to seed the controls
        x_lo, x_hi = (float(v) for v in ax.get_xlim())
        y_lo, y_hi = (float(v) for v in ax.get_ylim())
        # autoscale on/off comes from ax._autoscaleXon (private) — safer
        # to use ax.get_autoscalex_on() / get_autoscaley_on().
        x_auto = bool(ax.get_autoscalex_on())
        y_auto = bool(ax.get_autoscaley_on())

        # ── Axes group ──────────────────────────────────────────────
        grp_ax = QGroupBox("Axes")
        fl = QFormLayout(grp_ax)

        self.chkAutoX = QCheckBox("Auto X")
        self.chkAutoX.setChecked(x_auto)
        self.chkAutoX.toggled.connect(self._refresh_enabled)
        fl.addRow(self.chkAutoX)

        self.spnXmin = self._make_spin(x_lo)
        self.spnXmax = self._make_spin(x_hi)
        fl.addRow("X min:", self.spnXmin)
        fl.addRow("X max:", self.spnXmax)

        self.chkAutoY = QCheckBox("Auto Y")
        self.chkAutoY.setChecked(y_auto)
        self.chkAutoY.toggled.connect(self._refresh_enabled)
        fl.addRow(self.chkAutoY)

        self.spnYmin = self._make_spin(y_lo)
        self.spnYmax = self._make_spin(y_hi)
        fl.addRow("Y min:", self.spnYmin)
        fl.addRow("Y max:", self.spnYmax)
        layout.addWidget(grp_ax)

        # ── Display group ───────────────────────────────────────────
        grp_disp = QGroupBox("Display")
        dv = QVBoxLayout(grp_disp)
        self.chkGrid = QCheckBox("Show grid")
        # ax has no public "is grid on?" — read the major-grid line
        # visibility from the first xaxis grid line, defaulting to True.
        self.chkGrid.setChecked(self._grid_currently_on(ax))
        dv.addWidget(self.chkGrid)
        self.chkLegend = QCheckBox("Show legend")
        self.chkLegend.setChecked(ax.get_legend() is not None)
        dv.addWidget(self.chkLegend)
        layout.addWidget(grp_disp)

        # ── Reset row ───────────────────────────────────────────────
        row_reset = QHBoxLayout()
        self.btnReset = QPushButton("Reset view")
        self.btnReset.setToolTip(
            "Re-enable autoscale on both axes.  Equivalent to ticking "
            "both 'Auto X' and 'Auto Y'.")
        self.btnReset.clicked.connect(self._reset_view)
        row_reset.addWidget(self.btnReset)
        row_reset.addStretch(1)
        layout.addLayout(row_reset)

        # ── Buttons ─────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        scroll_top.addWidget(btns)

        self._refresh_enabled()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _make_spin(value: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setDecimals(4)
        s.setRange(-_AXIS_BOUND, _AXIS_BOUND)
        s.setSingleStep(0.1)
        s.setValue(float(value))
        return s

    @staticmethod
    def _grid_currently_on(ax) -> bool:
        try:
            lines = ax.xaxis.get_gridlines()
            if not lines:
                return False
            return bool(lines[0].get_visible())
        except Exception:
            return True

    def _refresh_enabled(self) -> None:
        for w in (self.spnXmin, self.spnXmax):
            w.setEnabled(not self.chkAutoX.isChecked())
        for w in (self.spnYmin, self.spnYmax):
            w.setEnabled(not self.chkAutoY.isChecked())

    def _reset_view(self) -> None:
        self.chkAutoX.setChecked(True)
        self.chkAutoY.setChecked(True)

    # ------------------------------------------------------------------
    # data accessors / apply
    # ------------------------------------------------------------------
    def get_settings(self) -> dict:
        return {
            "x_auto": self.chkAutoX.isChecked(),
            "x_min": float(self.spnXmin.value()),
            "x_max": float(self.spnXmax.value()),
            "y_auto": self.chkAutoY.isChecked(),
            "y_min": float(self.spnYmin.value()),
            "y_max": float(self.spnYmax.value()),
            "grid": self.chkGrid.isChecked(),
            "legend": self.chkLegend.isChecked(),
        }

    def apply_to_axes(self, ax) -> None:
        s = self.get_settings()
        if s["x_auto"]:
            ax.set_autoscalex_on(True)
            ax.relim()
            ax.autoscale_view(scalex=True, scaley=False)
        else:
            lo, hi = s["x_min"], s["x_max"]
            if hi <= lo:
                hi = lo + 1e-9
            ax.set_autoscalex_on(False)
            ax.set_xlim(lo, hi)
        if s["y_auto"]:
            ax.set_autoscaley_on(True)
            ax.relim()
            ax.autoscale_view(scalex=False, scaley=True)
        else:
            lo, hi = s["y_min"], s["y_max"]
            if hi <= lo:
                hi = lo + 1e-9
            ax.set_autoscaley_on(False)
            ax.set_ylim(lo, hi)
        ax.grid(s["grid"])
        if s["legend"]:
            # only create a legend if there is at least one labelled
            # artist; otherwise Matplotlib spams a UserWarning.
            handles, labels = ax.get_legend_handles_labels()
            if labels:
                ax.legend()
        else:
            leg = ax.get_legend()
            if leg is not None:
                leg.remove()
