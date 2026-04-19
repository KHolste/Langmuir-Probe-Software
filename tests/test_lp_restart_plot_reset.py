"""Restart-plot-reset hardening — fresh visual canvas at every Start
without losing the underlying dataset.  This was the actual driver
behind the multi-second Sim-mode restart latency: the first tick of
a restarted run rebuilt the entire previous-run line."""
from __future__ import annotations

import os
import pathlib
import sys
import time
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
    from dlp_lp_window import LPMeasurementWindow
    smu = MagicMock(); smu.read_voltage.return_value = 25.0
    smu.read_current.return_value = -3e-4
    k = MagicMock(); k.read_voltage.return_value = 3.0
    return LPMeasurementWindow(smu, k)


def _payload(t, te=4.33, ne=1e17):
    return {
        "t_rel_s": t, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
        "u_meas_v": 3.0, "v_d13": 3.0, "i_a": -3e-4,
        "Te_eV": te, "n_e_m3": ne,
        "species": "Argon (Ar)", "area_m2": 9.7075e-6, "mi_kg": 6.6e-26,
    }


# ---------------------------------------------------------------------------
class TestPlotResetHelper:
    """The helper itself — called from ``_on_start`` before the new
    worker fires its first tick.  Tested in isolation so the test
    does not depend on whether the worker emits synchronously."""

    def test_reset_helper_empties_plot_buffers(self, qapp):
        win = _make(qapp)
        for t_idx in range(50):
            win._on_worker_sample(_payload(t_idx * 0.25))
        win._reset_plot_for_new_run()
        x, y = win._line_te.get_data()
        assert len(list(x)) == 0 and len(list(y)) == 0
        x2, y2 = win._line_ne.get_data()
        assert len(list(x2)) == 0 and len(list(y2)) == 0

    def test_reset_helper_keeps_dataset_intact(self, qapp):
        win = _make(qapp)
        for t_idx in range(50):
            win._on_worker_sample(_payload(t_idx * 0.25))
        win._reset_plot_for_new_run()
        # Dataset survives — Save CSV still exports the prior history.
        assert len(win._dataset) == 50

    def test_reset_helper_resets_live_labels(self, qapp):
        win = _make(qapp)
        win._on_worker_sample(_payload(0.0))
        assert win.lblTe.text() != "\u2014 eV"
        win._reset_plot_for_new_run()
        assert win.lblTe.text() == "\u2014 eV"
        assert win.lblNe.text().startswith("\u2014")

    def test_restart_after_long_run_is_fast(self, qapp):
        """The whole point: a long previous run must not slow down
        the next Start.  With the buffer-reset in place the first
        new tick paints onto an empty line, not onto a 1000-point
        rebuild — should stay well under 500 ms even with this many
        prior samples."""
        win = _make(qapp)
        for t_idx in range(1000):
            win._on_worker_sample(_payload(t_idx * 0.05))
        t0 = time.perf_counter()
        win._on_start()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, elapsed


# ---------------------------------------------------------------------------
class TestNoFunctionalRegression:
    def test_first_start_unaffected(self, qapp):
        """First start: no prior data, reset is a cheap no-op."""
        win = _make(qapp)
        t0 = time.perf_counter()
        win._on_start()
        assert win._worker is not None
        assert (time.perf_counter() - t0) < 0.5
        win._worker.request_stop()

    def test_clear_during_run_still_works(self, qapp):
        """The mid-run Clear Plot path is unaffected."""
        win = _make(qapp)
        for t in (0.0, 0.1, 0.2):
            win._on_worker_sample(_payload(t))
        win._worker = MagicMock(is_running=True)
        win._on_clear()
        x, _ = win._line_te.get_data()
        assert len(list(x)) == 0
        # Dataset still intact during a running clear.
        assert len(win._dataset) == 3
