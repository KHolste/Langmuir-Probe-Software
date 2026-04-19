"""Clear-Plot-during-run + Approx./Exact UX wiring."""
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


def _payload(t, te=4.33, ne=1e17):
    return {
        "t_rel_s": t, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
        "u_meas_v": 3.0, "v_d13": 3.0, "i_a": -3e-4,
        "Te_eV": te, "n_e_m3": ne,
        "species": "Argon (Ar)", "area_m2": 9.7075e-6, "mi_kg": 6.6e-26,
    }


# ===========================================================================
# Eq-mode UX
# ===========================================================================
class TestEqModeCombo:
    def test_combo_present_with_two_items(self, qapp):
        win = _make(qapp)
        assert hasattr(win, "cmbEqMode")
        assert not hasattr(win, "chkEq10")
        assert win.cmbEqMode.count() == 2

    def test_default_is_approx(self, qapp):
        win = _make(qapp)
        assert win.cmbEqMode.currentText() == "Approx."
        assert win.cmbEqMode.currentData() is False

    def test_exact_item_carries_true(self, qapp):
        win = _make(qapp)
        win.cmbEqMode.setCurrentIndex(1)
        assert win.cmbEqMode.currentText() == "Exact"
        assert win.cmbEqMode.currentData() is True

    def test_default_combo_value_reaches_worker(self, qapp):
        win = _make(qapp)
        # SMU + K2000 are MagicMocks → start path passes the kwarg
        # through.  Patch the worker class itself so we can read what
        # got passed without spinning a real Qt timer.
        from dlp_triple_worker import TripleProbeWorker  # noqa: F401
        with patch("dlp_lp_window.TripleProbeWorker") as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.is_running = False
            win._on_start()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["prefer_eq10"] is False  # Approx. default

    def test_exact_choice_propagates_to_worker(self, qapp):
        win = _make(qapp)
        win.cmbEqMode.setCurrentIndex(1)  # Exact
        with patch("dlp_lp_window.TripleProbeWorker") as mock_cls:
            mock_cls.return_value.is_running = False
            win._on_start()
            assert mock_cls.call_args.kwargs["prefer_eq10"] is True


# ===========================================================================
# Clear Plot — works during a run, preserves dataset
# ===========================================================================
class TestClearPlotDuringRun:
    def test_clear_button_label_says_plot(self, qapp):
        win = _make(qapp)
        assert "Plot" in win.btnClear.text()

    def test_clear_button_always_enabled(self, qapp):
        win = _make(qapp)
        # Simulate a running worker.
        win._worker = MagicMock(is_running=True)
        win._refresh_button_state()
        assert win.btnClear.isEnabled()
        # And idle.
        win._worker = None
        win._refresh_button_state()
        assert win.btnClear.isEnabled()

    def test_clear_during_run_keeps_dataset(self, qapp):
        win = _make(qapp)
        # Feed three samples, pretend the worker is still running.
        for t in (0.0, 0.1, 0.2):
            win._on_worker_sample(_payload(t))
        win._worker = MagicMock(is_running=True)
        assert len(win._dataset) == 3
        win._on_clear()
        # Plot lines empty, but dataset and sample counter intact.
        assert win._line_te.get_data()[0].size == 0 \
            if hasattr(win._line_te.get_data()[0], "size") \
            else len(list(win._line_te.get_data()[0])) == 0
        assert len(win._dataset) == 3
        assert win.lblSamples.text() == "3"

    def test_clear_during_run_does_not_stop_worker(self, qapp):
        win = _make(qapp)
        worker_mock = MagicMock(is_running=True)
        win._worker = worker_mock
        win._on_clear()
        worker_mock.request_stop.assert_not_called()

    def test_clear_when_idle_resets_dataset(self, qapp):
        win = _make(qapp)
        for t in (0.0, 0.1):
            win._on_worker_sample(_payload(t))
        # Worker is None → idle path.
        win._on_clear()
        assert len(win._dataset) == 0
        assert win.lblSamples.text() == "0"

    def test_subsequent_samples_after_clear_re_grow_plot(self, qapp):
        win = _make(qapp)
        # Pretend the worker is running for the entire test.
        win._worker = MagicMock(is_running=True)
        win._on_worker_sample(_payload(0.0))
        win._on_clear()
        # New samples come in after the clear.
        win._on_worker_sample(_payload(1.0))
        win._on_worker_sample(_payload(1.1))
        x_te, y_te = win._line_te.get_data()
        assert len(list(x_te)) == 2
        # Dataset has all three (1 before clear + 2 after) because
        # the running-clear path leaves the dataset alone.
        assert len(win._dataset) == 3
