"""Plot Settings dialog + integration into V3 main window."""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _ax_with_data(qapp):
    from matplotlib.figure import Figure
    fig = Figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1, 2], [0, 1, 0])
    return ax


# ---------------------------------------------------------------------------
class TestPlotSettingsDialog:
    def test_widgets_present(self, qapp):
        from dlp_plot_settings_dialog import PlotSettingsDialog
        ax = _ax_with_data(qapp)
        dlg = PlotSettingsDialog(ax)
        for name in ("chkAutoX", "chkAutoY", "spnXmin", "spnXmax",
                     "spnYmin", "spnYmax", "chkGrid", "chkLegend",
                     "btnReset"):
            assert hasattr(dlg, name), name

    def test_get_settings_returns_full_dict(self, qapp):
        from dlp_plot_settings_dialog import PlotSettingsDialog
        ax = _ax_with_data(qapp)
        dlg = PlotSettingsDialog(ax)
        s = dlg.get_settings()
        for k in ("x_auto", "x_min", "x_max", "y_auto", "y_min",
                  "y_max", "grid", "legend"):
            assert k in s

    def test_apply_manual_xlim_ylim(self, qapp):
        from dlp_plot_settings_dialog import PlotSettingsDialog
        ax = _ax_with_data(qapp)
        dlg = PlotSettingsDialog(ax)
        dlg.chkAutoX.setChecked(False)
        dlg.spnXmin.setValue(-5.0)
        dlg.spnXmax.setValue(7.0)
        dlg.chkAutoY.setChecked(False)
        dlg.spnYmin.setValue(-1.0)
        dlg.spnYmax.setValue(2.5)
        dlg.apply_to_axes(ax)
        assert ax.get_xlim() == pytest.approx((-5.0, 7.0))
        assert ax.get_ylim() == pytest.approx((-1.0, 2.5))

    def test_reset_view_re_enables_autoscale(self, qapp):
        from dlp_plot_settings_dialog import PlotSettingsDialog
        ax = _ax_with_data(qapp)
        dlg = PlotSettingsDialog(ax)
        dlg.chkAutoX.setChecked(False)
        dlg.chkAutoY.setChecked(False)
        dlg._reset_view()
        assert dlg.chkAutoX.isChecked()
        assert dlg.chkAutoY.isChecked()

    def test_grid_toggle_applied(self, qapp):
        from dlp_plot_settings_dialog import PlotSettingsDialog
        ax = _ax_with_data(qapp)
        dlg = PlotSettingsDialog(ax)
        dlg.chkGrid.setChecked(False)
        dlg.apply_to_axes(ax)
        # grid invisible after apply
        assert all(not g.get_visible() for g in ax.xaxis.get_gridlines())


# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def v3_window(qapp):
    """Module-scoped V3 instance — reuses one main window across the
    integration tests so we don't churn the Qt heap (the offscreen
    platform plus repeated DLPMainWindowV3 construction has been
    observed to trigger access violations under heavy fixture churn).
    """
    from DoubleLangmuir_measure_v3 import DLPMainWindowV3
    win = DLPMainWindowV3()
    yield win
    win.close()


class TestV3PlotSettingsIntegration:
    """Integration tests that do NOT actually instantiate the
    PlotSettingsDialog inside the V3 window context — that path has
    been verified manually and triggers offscreen-platform Qt heap
    issues when chained with the standalone dialog tests above.  We
    verify the wiring via mocking the dialog class itself."""

    def test_button_present_in_main_window(self, v3_window):
        assert hasattr(v3_window, "btnPlotSettings")
        assert v3_window.btnPlotSettings.toolTip().lower().startswith(
            "open the plot settings")

    def test_log_label_present(self, v3_window):
        assert hasattr(v3_window, "lblLog")
        assert v3_window.lblLog.text() == "Log"

    def test_open_dialog_handler_calls_dialog_constructor(self, v3_window):
        """Wire-check: clicking the button (or calling the slot) must
        construct a PlotSettingsDialog with the live axes.  We mock
        the class so no real dialog widgets are created here."""
        from unittest.mock import MagicMock
        import dlp_plot_settings_dialog as mod
        original = mod.PlotSettingsDialog
        try:
            mock_cls = MagicMock(name="PlotSettingsDialog")
            mock_inst = mock_cls.return_value
            mock_inst.exec.return_value = original.DialogCode.Rejected
            mod.PlotSettingsDialog = mock_cls
            v3_window._open_plot_settings()
            mock_cls.assert_called_once()
            # ax must have been forwarded as the first positional arg.
            args, kwargs = mock_cls.call_args
            assert args and args[0] is v3_window.ax
        finally:
            mod.PlotSettingsDialog = original
