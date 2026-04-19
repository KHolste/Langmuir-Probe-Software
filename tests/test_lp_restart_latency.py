"""Stop→Start latency hardening for the LP window + Triple worker."""
from __future__ import annotations

import os
import pathlib
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
class TestWorkerRestoreSkipsNoOp:
    def test_restore_skipped_when_prev_was_flo(self, qapp):
        """Skipping the no-op set_output_low restore is the main
        latency win on real hardware."""
        from dlp_triple_worker import TripleProbeWorker
        smu = MagicMock(); smu.read_voltage.return_value = 25.0
        smu.read_current.return_value = -3e-4
        k = MagicMock(); k.read_voltage.return_value = 3.0
        w = TripleProbeWorker(
            smu, k, v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
            prev_output_low="FLO")
        w.start(); w.request_stop()
        # During start: FLO forced once.
        flo_calls = [c for c in smu.set_output_low.call_args_list
                     if c.args == ("FLO",)]
        assert flo_calls
        # Restore must NOT have written FLO (or anything else) again.
        # Only the start-time call exists.
        all_calls = list(smu.set_output_low.call_args_list)
        assert len(all_calls) == 1
        assert all_calls[0].args == ("FLO",)

    def test_restore_runs_when_prev_was_gro(self, qapp):
        from dlp_triple_worker import TripleProbeWorker
        smu = MagicMock(); smu.read_voltage.return_value = 25.0
        smu.read_current.return_value = -3e-4
        k = MagicMock(); k.read_voltage.return_value = 3.0
        w = TripleProbeWorker(
            smu, k, v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
            prev_output_low="GRO")
        w.start(); w.request_stop()
        modes = [c.args[0] for c in smu.set_output_low.call_args_list]
        # FLO during run, GRO restored after.
        assert modes[0] == "FLO"
        assert modes[-1] == "GRO"


# ---------------------------------------------------------------------------
class TestWindowTeardownAndRestart:
    def _make(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        smu = MagicMock(); smu.read_voltage.return_value = 25.0
        smu.read_current.return_value = -3e-4
        k = MagicMock(); k.read_voltage.return_value = 3.0
        return LPMeasurementWindow(smu, k)

    def test_stop_drops_worker_reference(self, qapp):
        win = self._make(qapp)
        win._on_start()
        assert win._worker is not None
        win._worker.request_stop()
        # _on_worker_stopped runs synchronously via the worker emit;
        # _teardown_worker should have nulled the reference.
        assert win._worker is None

    def test_repeat_start_stop_does_not_accumulate_workers(self, qapp):
        from dlp_triple_worker import TripleProbeWorker
        win = self._make(qapp)
        for _ in range(5):
            win._on_start()
            assert win._worker is not None
            win._worker.request_stop()
            assert win._worker is None
        # No more than one TripleProbeWorker should remain as a Qt
        # descendant of the window after the cycle.
        children = win.findChildren(TripleProbeWorker)
        assert len(children) == 0

    def test_restart_is_fast(self, qapp):
        """Wall-clock guard: with mocks the start/stop loop must be
        well under 100 ms per round-trip; on real hardware the same
        path is now also linear in SCPI-call count, not in stale
        worker count."""
        win = self._make(qapp)
        t0 = time.perf_counter()
        for _ in range(5):
            win._on_start()
            win._worker.request_stop()
        elapsed = time.perf_counter() - t0
        # 5 × < 100 ms each.  Generous bound to absorb CI jitter.
        assert elapsed < 1.0, elapsed
