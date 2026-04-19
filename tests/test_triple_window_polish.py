"""Polish contract for the Triple-Probe window: dual-axis Te+n_e
plot, button hierarchy, and status pane styling."""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make(qapp):
    from dlp_triple_window import TripleProbeWindow
    return TripleProbeWindow(MagicMock(), MagicMock())


def _payload(t, te=2.886, ne=1e16):
    return {
        "t_rel_s": t, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
        "u_meas_v": 2.0, "v_d13": 2.0, "i_a": -1e-3,
        "Te_eV": te, "n_e_m3": ne,
        "species": "Argon (Ar)", "area_m2": 9.7075e-6, "mi_kg": 6.6e-26,
    }


# ---------------------------------------------------------------------------
class TestPlotDualAxis:
    def test_two_axes_present(self, qapp):
        win = _make(qapp)
        # ax + twinx → exactly two axes on the figure.
        assert len(win._fig.axes) == 2
        assert hasattr(win, "_line_te")
        assert hasattr(win, "_line_ne")

    def test_n_e_axis_is_log_scale(self, qapp):
        win = _make(qapp)
        assert win._ax2.get_yscale() == "log"

    def test_te_axis_is_linear(self, qapp):
        win = _make(qapp)
        assert win._ax.get_yscale() == "linear"

    def test_both_traces_present_and_labelled(self, qapp):
        # The window now uses two stacked subplots instead of a
        # twinx pair, so a combined legend is no longer required —
        # assert that both Line2D objects exist and carry the
        # documented labels.
        win = _make(qapp)
        assert win._line_te.get_label() == "Te"
        assert win._line_ne.get_label() == "n_e"

    def test_sample_grows_both_lines(self, qapp):
        win = _make(qapp)
        for t in (0.0, 0.1, 0.2):
            win._on_worker_sample(_payload(t))
        x_te, y_te = win._line_te.get_data()
        x_ne, y_ne = win._line_ne.get_data()
        assert len(list(x_te)) == 3 and len(list(x_ne)) == 3

    def test_zero_or_nan_n_e_is_filtered_from_log_axis(self, qapp):
        win = _make(qapp)
        win._on_worker_sample(_payload(0.0, te=2.5, ne=0.0))   # filtered
        win._on_worker_sample(_payload(0.1, te=2.5, ne=float("nan")))  # filtered
        win._on_worker_sample(_payload(0.2, te=2.5, ne=1e16))  # keeps
        _, y_ne = win._line_ne.get_data()
        assert len(list(y_ne)) == 1

    def test_clear_resets_both_lines(self, qapp):
        win = _make(qapp)
        for t in (0.0, 0.1, 0.2):
            win._on_worker_sample(_payload(t))
        win._on_clear()
        _, y_te = win._line_te.get_data()
        _, y_ne = win._line_ne.get_data()
        assert len(list(y_te)) == 0 and len(list(y_ne)) == 0


# ---------------------------------------------------------------------------
class TestButtonHierarchy:
    def test_start_and_stop_have_larger_min_height(self, qapp):
        win = _make(qapp)
        assert win.btnStart.minimumHeight() >= 32
        assert win.btnStop.minimumHeight() >= 32

    def test_save_and_clear_smaller_than_primary(self, qapp):
        win = _make(qapp)
        assert win.btnSave.minimumHeight() < win.btnStart.minimumHeight()
        assert win.btnClear.minimumHeight() < win.btnStart.minimumHeight()

    def test_status_pane_has_visible_padding(self, qapp):
        win = _make(qapp)
        assert win.lblStatus.minimumHeight() >= 24
        assert "padding" in win.lblStatus.styleSheet()


# ---------------------------------------------------------------------------
class TestNoFunctionalRegression:
    def test_buttons_still_present(self, qapp):
        win = _make(qapp)
        for n in ("btnStart", "btnStop", "btnSave", "btnClear",
                  "cmbSign", "spnVd12", "lblTe", "lblNe"):
            assert hasattr(win, n)
