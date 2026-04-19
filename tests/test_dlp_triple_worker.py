"""Tests for the Phase-2 Triple-Probe worker."""
from __future__ import annotations

import math
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


def _smu_mock(*, current=-1e-3, voltage_actual=25.0):
    smu = MagicMock(name="smu")
    smu.read_current.return_value = current
    smu.read_voltage.return_value = voltage_actual
    return smu


def _k2000_mock(voltage=2.0):
    k = MagicMock(name="k2000")
    k.read_voltage.return_value = voltage
    return k


def _make_worker(qapp, smu, k2000, **overrides):
    from dlp_triple_worker import TripleProbeWorker
    kwargs = dict(
        v_d12_setpoint=25.0,
        current_limit_a=0.01,
        species_name="Argon (Ar)",
        tick_ms=20,
    )
    kwargs.update(overrides)
    return TripleProbeWorker(smu, k2000, **kwargs)


# ===========================================================================
# Construction / validation
# ===========================================================================
class TestConstruction:
    def test_rejects_non_positive_v_d12(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        with pytest.raises(ValueError):
            _make_worker(qapp, smu, k, v_d12_setpoint=0.0)

    def test_rejects_invalid_sign(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        with pytest.raises(ValueError):
            _make_worker(qapp, smu, k, v_d13_sign=0)

    def test_rejects_non_positive_current_limit(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        with pytest.raises(ValueError):
            _make_worker(qapp, smu, k, current_limit_a=0.0)


# ===========================================================================
# Start sequence
# ===========================================================================
class TestStart:
    def test_start_forces_floating_and_writes_smu(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        w = _make_worker(qapp, smu, k)
        # Capture started + a sample emission to confirm a tick fired.
        started_calls = []
        w.started.connect(lambda: started_calls.append(True))
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start()
        smu.set_output_low.assert_called_with("FLO")
        smu.set_current_limit.assert_called_with(0.01)
        smu.set_voltage.assert_any_call(25.0)
        smu.output.assert_any_call(True)
        assert started_calls == [True]
        # immediate first tick should have produced one sample
        assert len(samples) == 1
        w.request_stop()

    def test_start_failure_emits_failed_and_restores(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        smu.output.side_effect = RuntimeError("output fault")
        w = _make_worker(qapp, smu, k)
        msgs = []
        w.failed.connect(lambda m: msgs.append(m))
        w.start()
        assert msgs and "start failed" in msgs[0]
        # _safe_restore must have brought the SMU back
        smu.set_voltage.assert_any_call(0.0)


# ===========================================================================
# Tick
# ===========================================================================
class TestTick:
    def test_sample_payload_shape(self, qapp):
        smu, k = _smu_mock(current=-2e-3, voltage_actual=25.5), _k2000_mock(2.0)
        w = _make_worker(qapp, smu, k)
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start()
        w.request_stop()
        s = samples[0]
        for key in ("t_rel_s", "v_d12_setpoint", "v_d12_actual", "u_meas_v",
                    "v_d13", "i_a", "Te_eV", "n_e_m3", "species",
                    "area_m2", "mi_kg"):
            assert key in s
        assert s["i_a"] == pytest.approx(-2e-3)
        assert s["v_d12_actual"] == pytest.approx(25.5)
        assert s["u_meas_v"] == pytest.approx(2.0)
        # Default sign is +1 → v_d13 == u_meas.
        assert s["v_d13"] == pytest.approx(2.0)
        assert math.isfinite(s["Te_eV"]) and s["Te_eV"] > 0

    def test_v_d13_sign_inverts_polarity(self, qapp):
        smu, k = _smu_mock(), _k2000_mock(voltage=-2.0)
        w = _make_worker(qapp, smu, k, v_d13_sign=-1)
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start()
        w.request_stop()
        # Raw u_meas = -2 V; sign = -1 → v_d13 = +2 V (analysable).
        assert samples[0]["v_d13"] == pytest.approx(2.0)
        assert samples[0]["Te_eV"] > 0

    def test_smu_read_failure_stops_with_failed(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        smu.read_current.side_effect = RuntimeError("VISA timeout")
        w = _make_worker(qapp, smu, k)
        msgs = []
        w.failed.connect(lambda m: msgs.append(m))
        w.start()
        # start triggered an immediate tick that failed.
        assert msgs and "SMU read failed" in msgs[0]
        smu.output.assert_any_call(False)
        assert not w.is_running

    def test_k2000_read_failure_stops_with_failed(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        k.read_voltage.side_effect = RuntimeError("GPIB timeout")
        w = _make_worker(qapp, smu, k)
        msgs = []
        w.failed.connect(lambda m: msgs.append(m))
        w.start()
        assert msgs and "K2000 read failed" in msgs[0]
        smu.output.assert_any_call(False)


# ===========================================================================
# Stop / cleanup / restore
# ===========================================================================
class TestStop:
    def test_stop_emits_stopped_and_restores_smu(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        w = _make_worker(qapp, smu, k)
        reasons = []
        w.stopped.connect(lambda r: reasons.append(r))
        w.start()
        w.request_stop()
        smu.set_voltage.assert_any_call(0.0)
        smu.output.assert_any_call(False)
        assert reasons == ["user"]
        assert not w.is_running

    def test_restores_prev_output_low_when_not_flo(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        w = _make_worker(qapp, smu, k, prev_output_low="GRO")
        w.start()
        # During run: FLO forced.
        assert any(c.args == ("FLO",)
                   for c in smu.set_output_low.call_args_list)
        w.request_stop()
        # After run: original GRO restored.
        assert any(c.args == ("GRO",)
                   for c in smu.set_output_low.call_args_list)

    def test_double_stop_is_a_noop(self, qapp):
        smu, k = _smu_mock(), _k2000_mock()
        w = _make_worker(qapp, smu, k)
        reasons = []
        w.stopped.connect(lambda r: reasons.append(r))
        w.start()
        w.request_stop()
        w.request_stop()
        assert reasons == ["user"]


# ===========================================================================
# End-to-end with the project Fakes
# ===========================================================================
class TestWithProjectFakes:
    def test_runs_against_fake_b2901_and_fake_k2000(self, qapp):
        from fake_b2901_v2 import FakeB2901v2
        from fake_keithley_2000 import FakeKeithley2000
        smu = FakeB2901v2(current_compliance=0.01)
        smu.connect()
        k = FakeKeithley2000(voltage=2.5)
        k.connect()
        from dlp_triple_worker import TripleProbeWorker
        w = TripleProbeWorker(
            smu, k,
            v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
        )
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start()
        w.request_stop()
        assert samples, "worker must emit at least one sample on start()"
        s0 = samples[0]
        # Fake K2000 returns the configured voltage verbatim.
        assert s0["u_meas_v"] == pytest.approx(2.5)
        # SMU bias was driven by the worker.
        assert s0["v_d12_setpoint"] == pytest.approx(25.0)
        smu.close(); k.close()
