"""Tests for DoubleLangmuir_measure helpers and acquisition logic."""
from __future__ import annotations

import csv
import math
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# Allow running from repo root
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from DoubleLangmuir_measure import (
    build_voltage_list, build_sweep_voltages,
    write_csv, make_csv_path, DLPScanWorker, DLPMainWindow,
)
from fake_b2901 import FakeB2901, SimulatedTimeout, make_visa_timeout


# ── build_voltage_list ────────────────────────────────────────────────


class TestBuildVoltageList:

    def test_ascending(self):
        v = build_voltage_list(-10, 10, 1.0)
        assert v[0] == pytest.approx(-10.0)
        assert v[-1] == pytest.approx(10.0)
        assert len(v) == 21

    def test_descending(self):
        v = build_voltage_list(10, -10, 1.0)
        assert v[0] == pytest.approx(10.0)
        assert v[-1] == pytest.approx(-10.0)
        assert len(v) == 21

    def test_single_point(self):
        v = build_voltage_list(5, 5, 1.0)
        assert len(v) == 1
        assert v[0] == pytest.approx(5.0)

    def test_fractional_step(self):
        v = build_voltage_list(0, 1, 0.25)
        assert len(v) == 5
        np.testing.assert_allclose(v, [0, 0.25, 0.5, 0.75, 1.0])

    def test_zero_step_raises(self):
        with pytest.raises(ValueError):
            build_voltage_list(-10, 10, 0)

    def test_negative_step_raises(self):
        with pytest.raises(ValueError):
            build_voltage_list(-10, 10, -1)


# ── CSV helpers ───────────────────────────────────────────────────────


class TestCsv:

    def test_roundtrip(self, tmp_path):
        meta = {"Date": "2026-04-15 12:00:00", "V_start_V": "-10", "Points": "3"}
        v_soll = [-10.0, 0.0, 10.0]
        v_ist  = [-9.998, 0.001, 10.002]
        i_mean = [1e-3, 0.0, -1e-3]
        i_std  = [1e-6, 0.0, 1e-6]

        path = tmp_path / "test.csv"
        write_csv(path, meta, v_soll, i_mean, i_std, v_ist)

        # read back — modern header carries the versioned schema
        # banner (see dlp_csv_schema); the old product-identity
        # string was retired with the schema v1 rollout.
        text = path.read_text(encoding="utf-8")
        header_lines = [l for l in text.splitlines() if l.startswith("#")]
        assert any("Langmuir Probe Measurement Export" in l
                   for l in header_lines)
        assert any("Schema: lp-measurement-csv v" in l
                   for l in header_lines)
        assert any("V_start_V: -10" in l for l in header_lines)

        data_lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert len(data_lines) == 3
        row0 = data_lines[0].split(",")
        assert float(row0[0]) == pytest.approx(-10.0)
        assert float(row0[2]) == pytest.approx(1e-3)

    def test_empty_data(self, tmp_path):
        path = tmp_path / "empty.csv"
        write_csv(path, {}, [], [], [], [])
        text = path.read_text(encoding="utf-8")
        # Schema banner must still appear even on an empty dataset so
        # post-mortem inspection knows which layout to read against.
        assert "Langmuir Probe Measurement Export" in text
        assert "Schema: lp-measurement-csv v" in text
        data_lines = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert len(data_lines) == 0


class TestMakeCsvPath:

    def test_routes_into_method_subfolder_with_unified_name(self, tmp_path):
        # New unified scheme: <base>/<method>/LP_<ts>_<method>.csv,
        # default method = "double" when caller doesn't pass one.
        p = make_csv_path(tmp_path, "DLP")
        assert p.parent == tmp_path / "double"
        assert p.name.startswith("LP_")
        assert p.name.endswith("_double.csv")
        assert p.suffix == ".csv"

    def test_method_arg_overrides_default(self, tmp_path):
        p = make_csv_path(tmp_path, method="single")
        assert p.parent == tmp_path / "single"
        assert p.name.endswith("_single.csv")
        # Legacy ``prefix`` argument is intentionally ignored now
        # — kept signature-compatible only.
        p2 = make_csv_path(tmp_path, "TEST", method="triple")
        assert "TEST" not in p2.name
        assert p2.name.endswith("_triple.csv")


# ── FakeB2901 ─────────────────────────────────────────────────────────


class TestFakeB2901:

    def test_connect_returns_idn(self):
        f = FakeB2901()
        assert "SIMULATED" in f.connect()

    def test_tanh_shape(self):
        f = FakeB2901(i_sat=1e-3, te_eV=3.0, seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(-50.0)
        i_neg = f.read_current()
        f.set_voltage(0.0)
        i_zero = f.read_current()
        f.set_voltage(50.0)
        i_pos = f.read_current()
        assert i_neg < 0
        assert abs(i_zero) < 1e-9  # tanh(0) = 0, noise_std=0
        assert i_pos > 0
        assert abs(i_neg + i_pos) < 1e-9  # antisymmetric

    def test_compliance_clipping(self):
        f = FakeB2901(i_sat=10e-3, current_compliance=1e-3, seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(100.0)
        assert f.read_current() == pytest.approx(1e-3)
        f.set_voltage(-100.0)
        assert f.read_current() == pytest.approx(-1e-3)

    def test_output_off_returns_zero(self):
        f = FakeB2901(i_sat=1e-3, seed=0)
        f.connect()
        f.set_voltage(50.0)
        assert f.read_current() == 0.0  # output still off

    def test_close_disables_output(self):
        f = FakeB2901()
        f.connect()
        f.output(True)
        f.close()
        assert not f.is_output_on

    def test_deterministic_with_seed(self):
        """Two instances with same seed produce identical readings."""
        a = FakeB2901(noise_std=1e-5, seed=42)
        b = FakeB2901(noise_std=1e-5, seed=42)
        for inst in (a, b):
            inst.connect(); inst.output(True)
        for v in [-10, 0, 10]:
            a.set_voltage(v); b.set_voltage(v)
            assert a.read_current() == b.read_current()


# ── DLPScanWorker (hardware-independent) ──────────────────────────────


def _make_sweep(v_start, v_stop, v_step, bidir=False):
    """Shorthand: build sweep list from scalar params."""
    return build_sweep_voltages(v_start, v_stop, v_step, bidir)


def _run_worker_sync(worker: DLPScanWorker) -> dict:
    """Run the worker synchronously (no QThread) and collect results."""
    results: dict = {"points": [], "finished": None, "failed": None, "stopped": False}

    def on_point(idx, n, vs, vi, im, sd, compl, direction):
        results["points"].append((idx, n, vs, vi, im, sd, compl, direction))
    def on_finished(elapsed):
        results["finished"] = elapsed
    def on_failed(msg):
        results["failed"] = msg
    def on_stopped():
        results["stopped"] = True

    worker.point.connect(on_point)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.stopped.connect(on_stopped)
    worker.run()
    return results


class TestScanWorkerWithFake:

    def test_full_sweep(self):
        fake = FakeB2901(i_sat=2e-3, seed=0)
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["finished"] is not None
        assert res["failed"] is None
        assert not res["stopped"]
        assert len(res["points"]) == 11
        # shutdown: output off, voltage 0
        assert not fake.is_output_on
        assert fake.voltage == 0.0

    def test_stop_aborts_and_shuts_down(self):
        fake = FakeB2901(seed=0)
        fake.connect()
        sweep = _make_sweep(-50, 50, 1.0)  # 101 points

        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        # pre-set stop so loop exits immediately
        worker.request_stop()
        res = _run_worker_sync(worker)

        assert res["stopped"]
        assert len(res["points"]) == 0
        assert not fake.is_output_on
        assert fake.voltage == 0.0

    def test_csv_from_sweep(self, tmp_path):
        fake = FakeB2901(i_sat=1e-3, seed=0)
        fake.connect()
        sweep = _make_sweep(-2, 2, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=2)
        res = _run_worker_sync(worker)

        v_soll = [p[2] for p in res["points"]]
        v_ist  = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std  = [p[5] for p in res["points"]]

        path = tmp_path / "sweep.csv"
        write_csv(path, {"Points": str(len(v_soll))}, v_soll, i_mean, i_std, v_ist)

        text = path.read_text(encoding="utf-8")
        data = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert len(data) == 5

    def test_averages_reduce_noise(self):
        fake = FakeB2901(i_sat=1e-3, noise_std=1e-4, seed=7)
        fake.connect()
        sweep1 = [(10.0, "fwd")]
        w1 = DLPScanWorker(fake, sweep1, settle_s=0.0, n_avg=1)
        # reset rng
        fake._rng.seed(7); fake.output(False)
        r1 = _run_worker_sync(w1)
        std_1 = r1["points"][0][5]

        fake2 = FakeB2901(i_sat=1e-3, noise_std=1e-4, seed=7)
        fake2.connect()
        w2 = DLPScanWorker(fake2, sweep1, settle_s=0.0, n_avg=50)
        r2 = _run_worker_sync(w2)
        std_50 = r2["points"][0][5]
        # averaging with 50 samples should yield smaller std
        assert std_1 == 0.0  # n_avg=1 → ddof=1 gives 0
        assert std_50 > 0.0  # n_avg=50 → real spread


# ── FakeB2901 failure injection ──────────────────────────────────────


class TestFakeB2901Failures:

    def test_fail_on_connect(self):
        f = FakeB2901(fail_after=0, fail_on="connect")
        with pytest.raises(SimulatedTimeout, match="connect"):
            f.connect()

    def test_fail_after_n_set_voltage(self):
        f = FakeB2901(fail_after=3, fail_on="set_voltage", seed=0)
        f.connect()
        f.output(True)
        for _ in range(3):
            f.set_voltage(1.0)  # calls 1-3 succeed
        with pytest.raises(SimulatedTimeout, match="set_voltage"):
            f.set_voltage(2.0)  # call 4 fails

    def test_fail_fires_once(self):
        """After the fault fires, subsequent calls succeed (cleanup safe)."""
        f = FakeB2901(fail_after=0, fail_on="set_voltage", seed=0)
        f.connect()
        f.output(True)
        with pytest.raises(SimulatedTimeout):
            f.set_voltage(5.0)
        # second call must succeed (cleanup path)
        f.set_voltage(0.0)
        assert f.voltage == 0.0

    def test_fail_on_read_current(self):
        f = FakeB2901(fail_after=2, fail_on="read_current", seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(10.0)
        f.read_current()  # 1
        f.read_current()  # 2
        with pytest.raises(SimulatedTimeout):
            f.read_current()  # 3 → boom

    def test_custom_exception_type(self):
        f = FakeB2901(fail_after=0, fail_on="read_voltage",
                       fail_exc=RuntimeError)
        f.connect()
        with pytest.raises(RuntimeError):
            f.read_voltage()

    def test_unfailed_methods_unaffected(self):
        """fail_on restricts to one method; others keep working."""
        f = FakeB2901(fail_after=0, fail_on="read_current", seed=0)
        f.connect()  # OK
        f.output(True)  # OK
        f.set_voltage(5.0)  # OK
        f.read_voltage()  # OK
        with pytest.raises(SimulatedTimeout):
            f.read_current()  # fails


# ── Worker failure-handling tests ─────────────────────────────────────


class TestScanWorkerFailure:

    def _assert_safe_state(self, fake: FakeB2901):
        """Verify the instrument is in a safe post-failure state."""
        assert not fake.is_output_on, "Output must be OFF after failure"
        assert fake.voltage == 0.0, "Voltage must be 0 V after failure"

    def test_read_current_failure_mid_sweep(self):
        """Exception in read_current → failed signal, safe shutdown."""
        fake = FakeB2901(
            seed=0, fail_after=3, fail_on="read_current",
        )
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)  # 11 pts
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert "read_current" in res["failed"]
        assert res["finished"] is None
        # first 3 read_current calls succeed → 3 points acquired
        assert len(res["points"]) == 3
        self._assert_safe_state(fake)

    def test_set_voltage_failure_mid_sweep(self):
        """Exception in set_voltage → failed signal, safe shutdown."""
        fake = FakeB2901(
            seed=0, fail_after=3, fail_on="set_voltage",
        )
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)  # 11 pts
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert "set_voltage" in res["failed"]
        # 3 set_voltage calls succeed → 3 points emitted
        assert len(res["points"]) == 3
        self._assert_safe_state(fake)

    def test_output_enable_failure(self):
        """Exception on initial output(True) → failed, no points, safe."""
        fake = FakeB2901(
            seed=0, fail_after=0, fail_on="output",
        )
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert len(res["points"]) == 0
        # fault already fired → cleanup output(False) succeeds
        assert not fake.is_output_on

    def test_partial_sweep_csv(self, tmp_path):
        """Partial sweep data after failure can be saved to CSV."""
        fake = FakeB2901(
            seed=0, i_sat=1e-3,
            fail_after=3, fail_on="read_current",
        )
        fake.connect()
        sweep = _make_sweep(-2, 2, 1.0)  # 5 pts
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert len(res["points"]) == 3

        # write partial data just like the GUI does
        v_soll = [p[2] for p in res["points"]]
        v_ist  = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std  = [p[5] for p in res["points"]]
        path = tmp_path / "partial.csv"
        write_csv(path, {"Points": str(len(v_soll))}, v_soll, i_mean, i_std, v_ist)

        text = path.read_text(encoding="utf-8")
        data = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert len(data) == 3
        assert "Points: 3" in text

    def test_timeout_exception_type(self):
        """SimulatedTimeout is a proper Exception subclass."""
        fake = FakeB2901(
            seed=0, fail_after=1, fail_on="read_current",
            fail_exc=SimulatedTimeout,
        )
        fake.connect()
        sweep = _make_sweep(0, 5, 5.0)  # 2 pts
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert "Simulated failure" in res["failed"]
        self._assert_safe_state(fake)

    def test_runtime_error_injection(self):
        """Worker handles arbitrary exception types."""
        fake = FakeB2901(
            seed=0, fail_after=0, fail_on="set_voltage",
            fail_exc=RuntimeError,
        )
        fake.connect()
        sweep = _make_sweep(0, 5, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        assert res["failed"] is not None
        assert len(res["points"]) == 0
        self._assert_safe_state(fake)


# ── Error message detail tests ───────────────────────────────────────


class TestErrorMessageDetail:
    """Worker error messages must contain exception type and operation."""

    def test_message_contains_exception_type(self):
        fake = FakeB2901(seed=0, fail_after=0, fail_on="set_voltage")
        fake.connect()
        worker = DLPScanWorker(fake, [(1.0, "fwd")], settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert "SimulatedTimeout" in res["failed"]

    def test_message_contains_operation(self):
        fake = FakeB2901(seed=0, fail_after=1, fail_on="read_current")
        fake.connect()
        worker = DLPScanWorker(fake, [(5.0, "fwd"), (10.0, "fwd")], settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert "read_current" in res["failed"]
        assert "[during" in res["failed"]

    def test_message_contains_runtime_error_type(self):
        fake = FakeB2901(seed=0, fail_after=0, fail_on="set_voltage",
                          fail_exc=RuntimeError)
        fake.connect()
        worker = DLPScanWorker(fake, [(1.0, "fwd")], settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert "RuntimeError" in res["failed"]


# ── CSV run-status metadata tests ────────────────────────────────────


class TestCsvRunStatus:

    def test_completed_sweep_csv(self, tmp_path):
        """Successful sweep: Run_Status=completed, no Failure_Reason."""
        fake = FakeB2901(seed=0, i_sat=1e-3)
        fake.connect()
        sweep = _make_sweep(-2, 2, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        path = tmp_path / "ok.csv"
        meta = {"Points": str(len(res["points"])), "Run_Status": "completed"}
        v_soll = [p[2] for p in res["points"]]
        v_ist = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std = [p[5] for p in res["points"]]
        write_csv(path, meta, v_soll, i_mean, i_std, v_ist)

        text = path.read_text(encoding="utf-8")
        assert "Run_Status: completed" in text
        assert "Failure_Reason" not in text

    def test_failed_sweep_csv(self, tmp_path):
        """Failed sweep: Run_Status=failed, Failure_Reason present."""
        fake = FakeB2901(seed=0, i_sat=1e-3, fail_after=2, fail_on="read_current")
        fake.connect()
        sweep = _make_sweep(-2, 2, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        path = tmp_path / "fail.csv"
        meta = {
            "Points": str(len(res["points"])),
            "Run_Status": "failed",
            "Failure_Reason": res["failed"],
        }
        v_soll = [p[2] for p in res["points"]]
        v_ist = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std = [p[5] for p in res["points"]]
        write_csv(path, meta, v_soll, i_mean, i_std, v_ist)

        text = path.read_text(encoding="utf-8")
        assert "Run_Status: failed" in text
        assert "Failure_Reason:" in text
        data = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert len(data) == 2  # only partial points

    def test_aborted_sweep_csv(self, tmp_path):
        """User-stopped sweep: Run_Status=aborted."""
        fake = FakeB2901(seed=0, i_sat=1e-3)
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        worker.request_stop()
        res = _run_worker_sync(worker)

        path = tmp_path / "abort.csv"
        meta = {"Points": "0", "Run_Status": "aborted"}
        write_csv(path, meta, [], [], [], [])

        text = path.read_text(encoding="utf-8")
        assert "Run_Status: aborted" in text


# ── VISA-compatible timeout tests ────────────────────────────────────


class TestVisaTimeout:

    def test_make_visa_timeout_returns_exception(self):
        exc = make_visa_timeout("test timeout")
        assert isinstance(exc, Exception)

    def test_visa_timeout_flag(self):
        """visa_timeout=True uses pyvisa VisaIOError if available."""
        fake = FakeB2901(fail_after=0, fail_on="read_current",
                          visa_timeout=True, seed=0)
        fake.connect()
        fake.output(True)
        with pytest.raises(Exception) as exc_info:
            fake.read_current()
        # must be either VisaIOError or SimulatedTimeout
        ename = type(exc_info.value).__name__
        assert ename in ("VisaIOError", "SimulatedTimeout")

    def test_visa_timeout_worker_safe_shutdown(self):
        """Worker handles VISA timeout and shuts down safely."""
        fake = FakeB2901(seed=0, fail_after=2, fail_on="read_current",
                          visa_timeout=True)
        fake.connect()
        sweep = _make_sweep(-5, 5, 1.0)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert res["failed"] is not None
        assert not fake.is_output_on
        assert fake.voltage == 0.0


# ── GUI state recovery tests ─────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp():
    """Provide a QApplication instance for GUI tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestGuiStateRecovery:

    def _make_window(self, qapp, fake):
        """Create a DLPMainWindow connected to a FakeB2901."""
        win = DLPMainWindow()
        win.smu = fake
        win.chkSim.setChecked(True)
        win.chkSim.setEnabled(False)
        win.btnConnect.setText("Disconnect")
        return win

    def test_buttons_after_failure(self, qapp):
        """After a failed sweep, Start is enabled and Stop is disabled."""
        fake = FakeB2901(seed=0, fail_after=1, fail_on="read_current")
        fake.connect()
        win = self._make_window(qapp, fake)

        # simulate _start_sweep + immediate synchronous worker run
        sweep = _make_sweep(-2, 2, 1.0)
        win._set_sweep_ui(True)
        assert not win.btnStart.isEnabled()
        assert win.btnStop.isEnabled()

        # run worker synchronously
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert res["failed"] is not None

        # simulate the _on_fail callback
        win._on_fail(res["failed"])

        assert win.btnStart.isEnabled(), "Start must be re-enabled"
        assert not win.btnStop.isEnabled(), "Stop must be disabled"
        assert "ERROR" in win.lblStatus.text()

    def test_buttons_after_stop(self, qapp):
        """After user stop, buttons return to idle state."""
        fake = FakeB2901(seed=0)
        fake.connect()
        win = self._make_window(qapp, fake)

        win._set_sweep_ui(True)
        win._on_stopped()

        assert win.btnStart.isEnabled()
        assert not win.btnStop.isEnabled()
        assert win.lblStatus.text() == "Stopped"

    def test_buttons_after_success(self, qapp):
        """After successful sweep, buttons return to idle state."""
        fake = FakeB2901(seed=0)
        fake.connect()
        win = self._make_window(qapp, fake)

        win._set_sweep_ui(True)
        win._on_done(1.5)

        assert win.btnStart.isEnabled()
        assert not win.btnStop.isEnabled()
        assert "Done" in win.lblStatus.text()

    def test_spinboxes_reenabled_after_failure(self, qapp):
        """Sweep parameter spinboxes are re-enabled after failure."""
        fake = FakeB2901(seed=0, fail_after=0, fail_on="set_voltage")
        fake.connect()
        win = self._make_window(qapp, fake)

        win._set_sweep_ui(True)
        for w in (win.spnVstart, win.spnVstop, win.spnVstep,
                  win.spnSettle, win.spnAvg, win.spnCompl):
            assert not w.isEnabled()

        win._on_fail("test error")

        for w in (win.spnVstart, win.spnVstop, win.spnVstep,
                  win.spnSettle, win.spnAvg, win.spnCompl):
            assert w.isEnabled(), f"{w.objectName()} must be re-enabled"


# ── build_sweep_voltages ─────────────────────────────────────────────


class TestBuildSweepVoltages:

    def test_forward_only(self):
        sv = build_sweep_voltages(-5, 5, 5.0, bidirectional=False)
        assert [(v, d) for v, d in sv] == [
            (-5.0, "fwd"), (0.0, "fwd"), (5.0, "fwd")]

    def test_bidirectional_no_duplicate_turning_point(self):
        sv = build_sweep_voltages(-5, 5, 5.0, bidirectional=True)
        volts = [v for v, _ in sv]
        dirs = [d for _, d in sv]
        assert volts == [-5.0, 0.0, 5.0, 0.0, -5.0]
        assert dirs == ["fwd", "fwd", "fwd", "rev", "rev"]
        # turning point (5.0) appears exactly once
        assert volts.count(5.0) == 1

    def test_bidirectional_single_point(self):
        sv = build_sweep_voltages(0, 0, 1.0, bidirectional=True)
        assert len(sv) == 1
        assert sv[0] == (0.0, "fwd")

    def test_bidir_step_1(self):
        sv = build_sweep_voltages(0, 3, 1.0, bidirectional=True)
        volts = [v for v, _ in sv]
        assert volts == [0, 1, 2, 3, 2, 1, 0]
        fwd_count = sum(1 for _, d in sv if d == "fwd")
        rev_count = sum(1 for _, d in sv if d == "rev")
        assert fwd_count == 4
        assert rev_count == 3


# ── FakeB2901 resistor model ────────────────────────────────────────


class TestFakeB2901Resistor:

    def test_linear_iv(self):
        f = FakeB2901(model="resistor", resistance=1000.0, seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(10.0)
        assert f.read_current() == pytest.approx(0.01)
        f.set_voltage(-5.0)
        assert f.read_current() == pytest.approx(-0.005)

    def test_resistor_compliance(self):
        f = FakeB2901(model="resistor", resistance=100.0,
                       current_compliance=0.01, seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(10.0)  # would be 0.1 A without clipping
        assert f.read_current() == pytest.approx(0.01)
        assert f.is_in_compliance()

    def test_resistor_no_compliance(self):
        f = FakeB2901(model="resistor", resistance=10000.0,
                       current_compliance=0.1, seed=0)
        f.connect()
        f.output(True)
        f.set_voltage(1.0)  # 0.1 mA, well below limit
        f.read_current()
        assert not f.is_in_compliance()

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            FakeB2901(model="plasma")


# ── Compliance flag in worker output ─────────────────────────────────


class TestComplianceFlag:

    def test_compliance_flagged_in_points(self):
        """Points hitting compliance carry compl=True."""
        fake = FakeB2901(model="resistor", resistance=100.0,
                          current_compliance=0.01, seed=0)
        fake.connect()
        # 5V → 50mA → clipped; 0.5V → 5mA → OK
        sweep = [(5.0, "fwd"), (0.5, "fwd")]
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        assert res["points"][0][6] is True   # 5V → compliance
        assert res["points"][1][6] is False  # 0.5V → no compliance

    def test_compliance_in_csv(self, tmp_path):
        fake = FakeB2901(model="resistor", resistance=100.0,
                          current_compliance=0.01, seed=0)
        fake.connect()
        sweep = [(5.0, "fwd"), (0.5, "fwd")]
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        path = tmp_path / "compl.csv"
        v_soll = [p[2] for p in res["points"]]
        v_ist = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std = [p[5] for p in res["points"]]
        dirs = [p[7] for p in res["points"]]
        compls = [p[6] for p in res["points"]]
        write_csv(path, {}, v_soll, i_mean, i_std, v_ist, dirs, compls)

        text = path.read_text(encoding="utf-8")
        data = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        assert data[0].endswith(",fwd,1")   # compliance hit
        assert data[1].endswith(",fwd,0")   # no compliance


# ── Bidirectional sweep worker test ──────────────────────────────────


class TestBidirectionalWorker:

    def test_bidir_directions_in_points(self):
        fake = FakeB2901(seed=0, i_sat=1e-3)
        fake.connect()
        sweep = build_sweep_voltages(-2, 2, 2.0, bidirectional=True)
        # expect: [-2,0,2,0,-2] → fwd,fwd,fwd,rev,rev
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)
        dirs = [p[7] for p in res["points"]]
        assert dirs == ["fwd", "fwd", "fwd", "rev", "rev"]

    def test_bidir_csv_has_dir_column(self, tmp_path):
        fake = FakeB2901(seed=0, i_sat=1e-3)
        fake.connect()
        sweep = build_sweep_voltages(-1, 1, 1.0, bidirectional=True)
        worker = DLPScanWorker(fake, sweep, settle_s=0.0, n_avg=1)
        res = _run_worker_sync(worker)

        path = tmp_path / "bidir.csv"
        v_soll = [p[2] for p in res["points"]]
        v_ist = [p[3] for p in res["points"]]
        i_mean = [p[4] for p in res["points"]]
        i_std = [p[5] for p in res["points"]]
        dirs = [p[7] for p in res["points"]]
        compls = [p[6] for p in res["points"]]
        write_csv(path, {}, v_soll, i_mean, i_std, v_ist, dirs, compls)

        text = path.read_text(encoding="utf-8")
        assert "dir,compl" in text
        data = [l for l in text.splitlines() if not l.startswith("#") and l.strip()]
        fwd_rows = [r for r in data if ",fwd," in r]
        rev_rows = [r for r in data if ",rev," in r]
        assert len(fwd_rows) == 3  # -1, 0, 1
        assert len(rev_rows) == 2  # 0, -1


# ── Config save/load ─────────────────────────────────────────────────


class TestConfigRoundtrip:

    def test_get_and_apply(self, qapp):
        win = DLPMainWindow()
        win.spnVstart.setValue(-30.0)
        win.spnVstop.setValue(30.0)
        win.spnVstep.setValue(0.25)
        win.spnSettle.setValue(0.5)
        win.spnAvg.setValue(7)
        win.spnCompl.setValue(15.0)
        win.chkBidir.setChecked(True)
        win.chkSim.setChecked(True)

        cfg = win.get_config()
        assert cfg["v_start"] == -30.0
        assert cfg["bidirectional"] is True

        # apply to a fresh window
        win2 = DLPMainWindow()
        win2.apply_config(cfg)
        assert win2.spnVstart.value() == pytest.approx(-30.0)
        assert win2.spnVstop.value() == pytest.approx(30.0)
        assert win2.spnVstep.value() == pytest.approx(0.25)
        assert win2.spnSettle.value() == pytest.approx(0.5)
        assert win2.spnAvg.value() == 7
        assert win2.spnCompl.value() == pytest.approx(15.0)
        assert win2.chkBidir.isChecked()
        assert win2.chkSim.isChecked()

    def test_json_roundtrip(self, qapp, tmp_path):
        import json
        win = DLPMainWindow()
        win.spnVstart.setValue(-20.0)
        win.spnVstep.setValue(0.1)
        win.chkBidir.setChecked(True)

        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(win.get_config(), indent=2),
                         encoding="utf-8")

        win2 = DLPMainWindow()
        cfg = json.loads(path.read_text(encoding="utf-8"))
        win2.apply_config(cfg)
        assert win2.spnVstart.value() == pytest.approx(-20.0)
        assert win2.spnVstep.value() == pytest.approx(0.1)
        assert win2.chkBidir.isChecked()
