"""Tests for iteration 4b: live compliance LED in the main window.

The LED is fed from the per-point ``compl`` flag the worker already
emits — no extra SCPI polling.  Within a sweep the LED is sticky-red:
once a hit is recorded it stays red so the user can glance at it after
the run.  Connect / disconnect resets it to grey.
"""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from DoubleLangmuir_measure import DLPMainWindow  # noqa: E402
from DoubleLangmuir_measure_v2 import DLPMainWindowV2  # noqa: E402
from fake_b2901 import FakeB2901  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _stylesheet(win, led_attr: str) -> str:
    return getattr(win, led_attr).styleSheet()


def _color_for(win, state: str) -> str:
    key = win._COMPL_LED_COLOR_KEY[state]
    return win._theme[key]


# ---------------------------------------------------------------------------
# v1: widget exists and starts grey
# ---------------------------------------------------------------------------
class TestV1Widget:
    def test_led_widget_exists(self, qapp):
        win = DLPMainWindow()
        assert hasattr(win, "ledCompl")
        assert hasattr(win, "lblComplLed")

    def test_led_starts_idle_grey(self, qapp):
        win = DLPMainWindow()
        assert win._compl_led_state == "idle"
        assert _color_for(win, "idle") in _stylesheet(win, "ledCompl")

    def test_helper_maps_known_states(self, qapp):
        win = DLPMainWindow()
        for state in ("idle", "clear", "hit"):
            win._set_compliance_led(state)
            assert win._compl_led_state == state
            assert _color_for(win, state) in _stylesheet(win, "ledCompl")

    def test_helper_falls_back_on_unknown_state(self, qapp):
        win = DLPMainWindow()
        win._set_compliance_led("hit")
        win._set_compliance_led("nonsense")  # must not raise
        assert win._compl_led_state == "idle"


# ---------------------------------------------------------------------------
# v1: data-driven transitions through _on_point
# ---------------------------------------------------------------------------
class TestV1DataDriven:
    def _arm_window(self, qapp):
        """Return a window with sweep buffers/lines initialised so
        ``_on_point`` can be called directly without spinning a worker."""
        win = DLPMainWindow()
        # blank buffers (already empty after construction, but be explicit)
        win._v_soll.clear(); win._v_ist.clear()
        win._i_mean.clear(); win._i_std.clear()
        win._directions.clear(); win._compliance.clear()
        win._set_compliance_led("clear")  # mimic _start_sweep entry
        win.progress.setMaximum(10)
        return win

    def test_clean_point_keeps_led_green(self, qapp):
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-6, 0.0, False, "fwd")
        assert win._compl_led_state == "clear"
        assert _color_for(win, "clear") in _stylesheet(win, "ledCompl")

    def test_compliance_point_turns_led_red(self, qapp):
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        assert win._compl_led_state == "hit"
        assert _color_for(win, "hit") in _stylesheet(win, "ledCompl")

    def test_led_is_sticky_red_within_sweep(self, qapp):
        """Once compliance is hit the LED stays red for the rest of the
        sweep, even if subsequent points are clean."""
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        win._on_point(1, 10, 1.0, 1.0, 1e-6, 0.0, False, "fwd")
        win._on_point(2, 10, 2.0, 2.0, 1e-6, 0.0, False, "fwd")
        assert win._compl_led_state == "hit"

    def test_on_point_does_not_query_smu_compliance(self, qapp):
        """The helper must use the ``compl`` arg, not poll the SMU."""
        win = self._arm_window(qapp)
        win.smu = MagicMock()
        win._on_point(0, 10, 0.0, 0.0, 1e-6, 0.0, False, "fwd")
        win.smu.is_in_compliance.assert_not_called()


# ---------------------------------------------------------------------------
# v1: connect / disconnect / sweep-start reset behaviour
# ---------------------------------------------------------------------------
class TestV1Lifecycle:
    def test_disconnect_resets_led_to_idle(self, qapp):
        win = DLPMainWindow()
        win._set_compliance_led("hit")
        win.smu = FakeB2901()
        win.smu.connect()
        win.btnConnect.setText("Disconnect")
        win.chkSim.setChecked(True)
        win.chkSim.setEnabled(False)
        win._toggle_connect()  # disconnect branch
        assert win.smu is None
        assert win._compl_led_state == "idle"

    def test_sim_connect_resets_led_to_idle(self, qapp):
        win = DLPMainWindow()
        win._set_compliance_led("hit")  # leftover from a previous sweep
        win.chkSim.setChecked(True)
        win._toggle_connect()  # sim-connect branch
        assert win.smu is not None
        assert win._compl_led_state == "idle"
        win._toggle_connect()  # disconnect to clean up

    def test_start_sweep_flips_led_to_clear(self, qapp):
        win = DLPMainWindow()
        win.chkSim.setChecked(True)
        win._toggle_connect()  # sim-connect
        win._set_compliance_led("hit")  # simulate stale state
        # Use a tiny sweep so the worker doesn't have to do real work,
        # then capture the LED state immediately and tear down.
        win.spnVstart.setValue(-1.0)
        win.spnVstop.setValue(1.0)
        win.spnVstep.setValue(1.0)
        try:
            win._start_sweep()
            captured = win._compl_led_state
            if win._worker is not None:
                win._worker.request_stop()
            if win._thread is not None:
                win._thread.quit()
                win._thread.wait(500)
        finally:
            win._toggle_connect()  # disconnect
        assert captured == "clear"


# ---------------------------------------------------------------------------
# v2: inherits helper, has its own _on_point / _start_sweep / sim-connect
# ---------------------------------------------------------------------------
class TestV2Mirror:
    def _arm_window(self, qapp):
        win = DLPMainWindowV2()
        win._v_soll.clear(); win._v_ist.clear()
        win._i_mean.clear(); win._i_std.clear()
        win._directions.clear(); win._compliance.clear()
        win._set_compliance_led("clear")
        win.progress.setMaximum(10)
        return win

    def test_v2_has_led_widget(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "ledCompl")
        assert win._compl_led_state == "idle"

    def test_v2_clean_point_stays_clear(self, qapp):
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-6, 0.0, False, "fwd")
        assert win._compl_led_state == "clear"

    def test_v2_compliance_point_turns_red(self, qapp):
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        assert win._compl_led_state == "hit"

    def test_v2_led_sticky_red_within_sweep(self, qapp):
        win = self._arm_window(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        win._on_point(1, 10, 1.0, 1.0, 1e-6, 0.0, False, "fwd")
        assert win._compl_led_state == "hit"

    def test_v2_sim_connect_resets_led(self, qapp):
        win = DLPMainWindowV2()
        win._set_compliance_led("hit")
        win.chkSim.setChecked(True)
        win._toggle_connect()  # sim-connect branch (v2 override)
        try:
            assert win.smu is not None
            assert win._compl_led_state == "idle"
        finally:
            win._toggle_connect()  # disconnect

    def test_v2_on_point_does_not_query_smu(self, qapp):
        win = self._arm_window(qapp)
        win.smu = MagicMock()
        win._on_point(0, 10, 0.0, 0.0, 1e-6, 0.0, True, "fwd")
        win.smu.is_in_compliance.assert_not_called()


# ---------------------------------------------------------------------------
# Abort paths: LED must not falsely advertise "swept clean" when zero
# data points were collected.  When at least one point arrived, the
# sticky-red contract still holds.
# ---------------------------------------------------------------------------
class TestV1AbortPaths:
    def _armed(self, qapp):
        win = DLPMainWindow()
        win._set_compliance_led("clear")  # mimic post-_start_sweep state
        return win

    def test_on_fail_with_zero_points_clears_stale_green(self, qapp):
        win = self._armed(qapp)
        win._on_fail("connect lost before first point")
        assert win._compl_led_state == "idle"

    def test_on_stopped_with_zero_points_clears_stale_green(self, qapp):
        win = self._armed(qapp)
        win._on_stopped()
        assert win._compl_led_state == "idle"

    def test_on_fail_with_data_keeps_sticky_state(self, qapp):
        win = self._armed(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        # save_csv would touch the disk; bypass it cleanly.
        win._save_csv = lambda *a, **kw: None
        win._on_fail("read_current timeout")
        assert win._compl_led_state == "hit"

    def test_on_stopped_with_data_keeps_sticky_state(self, qapp):
        win = self._armed(qapp)
        win._on_point(0, 10, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        win._save_csv = lambda *a, **kw: None
        win._on_stopped()
        assert win._compl_led_state == "hit"

    def test_on_done_keeps_sticky_state_after_clean_sweep(self, qapp):
        win = self._armed(qapp)
        win._on_point(0, 2, 0.0, 0.0, 1e-6, 0.0, False, "fwd")
        win._on_point(1, 2, 1.0, 1.0, 1e-6, 0.0, False, "fwd")
        win._save_csv = lambda *a, **kw: None
        win._on_done(0.1)
        assert win._compl_led_state == "clear"


class TestV2AbortPaths:
    def _armed(self, qapp):
        win = DLPMainWindowV2()
        win.chkSave.setChecked(False)
        win._set_compliance_led("clear")
        win._sweep_finished = True
        win._sweep_finalized = False
        win._thread = None
        win._sweep_elapsed = 0.0
        win._sweep_n_expected = 0
        return win

    def test_finalize_failed_zero_points_clears_stale_green(self, qapp):
        win = self._armed(qapp)
        win._sweep_status = "failed"
        win._sweep_failure = "early error"
        win._do_finalize()
        assert win._compl_led_state == "idle"

    def test_finalize_aborted_zero_points_clears_stale_green(self, qapp):
        win = self._armed(qapp)
        win._sweep_status = "aborted"
        win._sweep_failure = ""
        win._do_finalize()
        assert win._compl_led_state == "idle"

    def test_finalize_aborted_with_data_keeps_sticky_red(self, qapp):
        win = self._armed(qapp)
        win._on_point(0, 5, 0.0, 0.0, 1e-3, 0.0, True, "fwd")
        win._sweep_status = "aborted"
        win._sweep_failure = ""
        win._do_finalize()
        assert win._compl_led_state == "hit"

    def test_finalize_completed_keeps_sticky_state(self, qapp):
        win = self._armed(qapp)
        win._on_point(0, 1, 0.0, 0.0, 1e-6, 0.0, False, "fwd")
        win._sweep_status = "completed"
        win._sweep_failure = ""
        win._do_finalize()
        assert win._compl_led_state == "clear"
