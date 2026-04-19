"""Tests for the new LPmeasurement window: dark theme, two stacked
plots, plot-settings dialog, auto-save."""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make(qapp):
    from dlp_lp_window import LPMeasurementWindow
    return LPMeasurementWindow(MagicMock(), MagicMock())


def _payload(t, te=2.886, ne=1e16):
    return {
        "t_rel_s": t, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
        "u_meas_v": 2.0, "v_d13": 2.0, "i_a": -1e-3,
        "Te_eV": te, "n_e_m3": ne,
        "species": "Argon (Ar)", "area_m2": 9.7075e-6, "mi_kg": 6.6e-26,
    }


# ---------------------------------------------------------------------------
class TestStructure:
    def test_two_separate_subplots(self, qapp):
        win = _make(qapp)
        # Exactly two axes (Te + n_e), not a twinx pair.
        assert len(win._fig.axes) == 2
        assert hasattr(win, "_ax_te") and hasattr(win, "_ax_ne")
        # n_e on log scale by default.
        assert win._ax_ne.get_yscale() == "log"

    def test_dark_plot_theme_applied(self, qapp):
        win = _make(qapp)
        # Figure background must NOT be the matplotlib default 'white'.
        fig_bg = win._fig.get_facecolor()
        assert fig_bg[:3] != (1.0, 1.0, 1.0), fig_bg
        # And the axes face is the configured plot_bg.
        ax_bg = win._ax_te.get_facecolor()
        assert ax_bg[:3] != (1.0, 1.0, 1.0), ax_bg

    def test_legacy_axis_aliases_present(self, qapp):
        win = _make(qapp)
        assert win._ax is win._ax_te
        assert win._ax2 is win._ax_ne


# ---------------------------------------------------------------------------
class TestPlotSettingsDialog:
    def test_button_present_and_opens_dialog(self, qapp):
        win = _make(qapp)
        assert hasattr(win, "btnPlotSettings")
        from dlp_lp_plot_settings_dialog import LPPlotSettingsDialog
        with patch("LPmeasurement.LPPlotSettingsDialog"
                    if False else
                    "dlp_lp_plot_settings_dialog.LPPlotSettingsDialog"
                    ) as mock_cls:
            mock_cls.return_value.exec.return_value = (
                LPPlotSettingsDialog.DialogCode.Rejected)
            win._on_plot_settings()
            mock_cls.assert_called_once()

    def test_apply_to_axes_sets_manual_limits(self, qapp):
        from dlp_lp_plot_settings_dialog import LPPlotSettingsDialog
        win = _make(qapp)
        dlg = LPPlotSettingsDialog(win._ax_te, win._ax_ne)
        # Manual range on Te axis.
        dlg.grpTe._chk.setChecked(False)
        dlg.grpTe._spn_lo.setValue(0.0)
        dlg.grpTe._spn_hi.setValue(5.0)
        # Manual range on n_e axis (positive only — log).
        dlg.grpNe._chk.setChecked(False)
        dlg.grpNe._spn_lo.setValue(1e14)
        dlg.grpNe._spn_hi.setValue(1e17)
        dlg.apply_to_axes()
        assert win._ax_te.get_ylim() == pytest.approx((0.0, 5.0))


# ---------------------------------------------------------------------------
class TestAutoSave:
    def test_widgets_present(self, qapp):
        win = _make(qapp)
        for n in ("chkAutoSave", "editAutoSavePath", "btnBrowse"):
            assert hasattr(win, n)
        assert win.editAutoSavePath.text().endswith(".csv")

    def test_browse_overrides_path(self, qapp, tmp_path):
        win = _make(qapp)
        target = tmp_path / "manual_target.csv"
        from PySide6.QtWidgets import QFileDialog
        with patch.object(QFileDialog, "getSaveFileName",
                           return_value=(str(target), "CSV (*.csv)")):
            win._on_browse_autosave()
        assert win.editAutoSavePath.text() == str(target)

    def test_autosave_writes_file_on_stop(self, qapp, tmp_path):
        win = _make(qapp)
        win.chkAutoSave.setChecked(True)
        target = tmp_path / "autosave.csv"
        win.editAutoSavePath.setText(str(target))
        # Feed two samples through the worker-callback path.
        win._on_worker_sample(_payload(0.0))
        win._on_worker_sample(_payload(0.1))
        # Pretend the worker just stopped naturally.
        win._worker = MagicMock(is_running=False)
        win._on_worker_stopped("user")
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "# Samples: 2" in text
        assert "# V_d12_setpoint_V: 25" in text

    def test_autosave_skipped_when_disabled(self, qapp, tmp_path):
        win = _make(qapp)
        win.chkAutoSave.setChecked(False)
        target = tmp_path / "should_not_exist.csv"
        win.editAutoSavePath.setText(str(target))
        win._on_worker_sample(_payload(0.0))
        win._worker = MagicMock(is_running=False)
        win._on_worker_stopped("user")
        assert not target.exists()

    def test_autosave_skipped_when_no_samples(self, qapp, tmp_path):
        win = _make(qapp)
        win.chkAutoSave.setChecked(True)
        target = tmp_path / "empty.csv"
        win.editAutoSavePath.setText(str(target))
        win._worker = MagicMock(is_running=False)
        win._on_worker_stopped("user")
        assert not target.exists()


# ---------------------------------------------------------------------------
class TestLegacyShim:
    def test_dlp_triple_window_reexports_new_class(self, qapp):
        from dlp_triple_window import (TripleProbeWindow,
                                        LPMeasurementWindow,
                                        show_or_raise as legacy_show)
        from dlp_lp_window import (LPMeasurementWindow as NewWin,
                                    show_or_raise as new_show)
        assert TripleProbeWindow is NewWin
        assert LPMeasurementWindow is NewWin
        assert legacy_show is new_show


# ---------------------------------------------------------------------------
class TestNoFunctionalRegression:
    def test_buttons_and_widgets_intact(self, qapp):
        win = _make(qapp)
        for n in ("btnStart", "btnStop", "btnSave", "btnClear",
                  "btnPlotSettings", "btnBrowse", "chkAutoSave",
                  "editAutoSavePath", "cmbSign", "spnVd12", "lblTe",
                  "lblNe", "lblStatus"):
            assert hasattr(win, n)
