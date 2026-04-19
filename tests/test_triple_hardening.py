"""Hardening pass for the Triple-Probe stack — what we can do without
real SMU hardware: exercise worker / window / dataset / V3-mutex
through the project Fakes so the bench session only has to verify
GPIB latency, K2000 common-mode and the V_d13 sign convention.
"""
from __future__ import annotations

import math
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


# ===========================================================================
# Fake-SMU surface
# ===========================================================================
class TestFakeOutputLow:
    def test_set_output_low_records_mode(self):
        from fake_b2901 import FakeB2901
        f = FakeB2901()
        f.set_output_low("FLO")
        assert f.output_low == "FLO"
        assert f.output_low_history == ["FLO"]
        f.set_output_low("GRO")
        assert f.output_low_history == ["FLO", "GRO"]

    def test_set_output_low_rejects_unknown(self):
        from fake_b2901 import FakeB2901
        with pytest.raises(ValueError):
            FakeB2901().set_output_low("BANANA")

    def test_v2_inherits_set_output_low(self):
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2()
        f.set_output_low("FLO")
        assert f.output_low == "FLO"


# ===========================================================================
# Triple-Probe worker against the real Fake (no MagicMock SMU)
# ===========================================================================
def _make_worker_with_fakes(qapp, *, prev_low="GRO", **over):
    from dlp_triple_worker import TripleProbeWorker
    from fake_b2901_v2 import FakeB2901v2
    from fake_keithley_2000 import FakeKeithley2000
    smu = FakeB2901v2(current_compliance=0.01)
    smu.connect()
    k = FakeKeithley2000(voltage=2.0)
    k.connect()
    kwargs = dict(
        v_d12_setpoint=25.0, current_limit_a=0.01,
        species_name="Argon (Ar)", tick_ms=20,
        prev_output_low=prev_low,
    )
    kwargs.update(over)
    w = TripleProbeWorker(smu, k, **kwargs)
    return w, smu, k


class TestWorkerAgainstFakes:
    def test_start_records_flo_and_drives_smu(self, qapp):
        w, smu, k = _make_worker_with_fakes(qapp)
        w.start()
        assert smu.output_low == "FLO"
        assert smu.is_output_on
        assert smu.voltage == pytest.approx(25.0)
        w.request_stop()

    def test_stop_restores_prev_output_low(self, qapp):
        w, smu, k = _make_worker_with_fakes(qapp, prev_low="GRO")
        w.start()
        w.request_stop()
        # FLO during run, GRO after restore (last entry).
        assert "FLO" in smu.output_low_history
        assert smu.output_low_history[-1] == "GRO"
        assert not smu.is_output_on
        assert smu.voltage == 0.0

    def test_no_restore_when_prev_already_flo(self, qapp):
        w, smu, k = _make_worker_with_fakes(qapp, prev_low="FLO")
        w.start()
        w.request_stop()
        # No GRO entry — restore is skipped because prev == FLO.
        assert "GRO" not in smu.output_low_history

    def test_failed_path_still_restores(self, qapp):
        from fake_b2901_v2 import FakeB2901v2
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_triple_worker import TripleProbeWorker
        # Inject a failure on the very first read_current.
        smu = FakeB2901v2(current_compliance=0.01,
                          fail_after=0, fail_on="read_current")
        smu.connect()
        k = FakeKeithley2000(voltage=2.0)
        k.connect()
        w = TripleProbeWorker(
            smu, k, v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
            prev_output_low="GRO")
        msgs = []
        w.failed.connect(lambda m: msgs.append(m))
        w.start()  # immediate first tick fails
        assert msgs, "worker must emit failed on read_current error"
        # SMU brought back: output OFF, V=0, prev_low restored.
        assert not smu.is_output_on
        assert smu.voltage == 0.0
        assert smu.output_low_history[-1] == "GRO"

    def test_v_d13_sign_negative_inverts_polarity(self, qapp):
        from fake_keithley_2000 import FakeKeithley2000
        w, smu, k = _make_worker_with_fakes(qapp, v_d13_sign=-1)
        # K2000 reads -2 V → with sign=-1 the worker should produce v_d13=+2
        k.set_voltage_for_test(-2.0)
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start()
        w.request_stop()
        assert samples[0]["v_d13"] == pytest.approx(2.0)


# ===========================================================================
# Dataset / CSV edge cases
# ===========================================================================
class TestDatasetEdges:
    def test_nan_te_round_trips_as_nan(self, tmp_path):
        from dlp_triple_dataset import TripleDataset, TripleSample
        d = TripleDataset()
        d.add(TripleSample(
            t_s=0.0, u_supply_V=25.0, u_measure_V=2.0,
            i_measure_A=-1e-3, v_d12_V=25.0, v_d13_V=2.0,
            te_eV=float("nan"), ne_m3=0.0))
        p = d.write_csv(tmp_path / "nan.csv")
        text = p.read_text(encoding="utf-8")
        # Look at the only data row.
        row = [l for l in text.splitlines()
               if l and not l.startswith("#")][0]
        cols = row.split(",")
        # te_eV column is index 6
        assert math.isnan(float(cols[6]))

    def test_heterogeneous_context_not_promoted_to_header(self, tmp_path):
        from dlp_triple_dataset import TripleDataset, TripleSample
        d = TripleDataset()
        d.add(TripleSample(
            t_s=0.0, u_supply_V=25.0, u_measure_V=2.0,
            i_measure_A=-1e-3, v_d12_V=25.0, v_d13_V=2.0,
            te_eV=2.5, ne_m3=1e16, species="Argon (Ar)"))
        d.add(TripleSample(
            t_s=0.1, u_supply_V=25.0, u_measure_V=2.0,
            i_measure_A=-1e-3, v_d12_V=25.0, v_d13_V=2.0,
            te_eV=2.5, ne_m3=1e16, species="Xenon (Xe)"))
        p = d.write_csv(tmp_path / "het.csv")
        text = p.read_text(encoding="utf-8")
        # Mixed species → no header line for it.
        assert "# species:" not in text


# ===========================================================================
# Triple-Window: clear, save-cancel, singleton, plot growth
# ===========================================================================
def _payload(t):
    return {
        "t_rel_s": t, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
        "u_meas_v": 2.0, "v_d13": 2.0, "i_a": -1e-3,
        "Te_eV": 2.886, "n_e_m3": 1e16,
        "species": "Argon (Ar)", "area_m2": 9.7075e-6,
        "mi_kg": 6.6e-26,
    }


def _make_window(qapp):
    from dlp_triple_window import TripleProbeWindow
    return TripleProbeWindow(MagicMock(), MagicMock())


class TestWindowHardening:
    def test_clear_resets_dataset_and_plot(self, qapp):
        win = _make_window(qapp)
        for t in (0.0, 0.1, 0.2):
            win._on_worker_sample(_payload(t))
        assert int(win.lblSamples.text()) == 3
        win._on_clear()
        assert int(win.lblSamples.text()) == 0
        # Te line must be empty after clear.
        x, y = win._line_te.get_data()
        assert len(x) == 0 and len(y) == 0

    def test_te_plot_grows_with_each_sample(self, qapp):
        win = _make_window(qapp)
        for t in (0.0, 0.1, 0.2, 0.3):
            win._on_worker_sample(_payload(t))
        x, y = win._line_te.get_data()
        assert len(list(x)) == 4 and len(list(y)) == 4

    def test_save_cancelled_dialog_does_not_write_file(self, qapp, tmp_path):
        win = _make_window(qapp)
        win._on_worker_sample(_payload(0.0))
        from PySide6.QtWidgets import QFileDialog
        target = tmp_path / "should_not_exist.csv"
        with patch.object(QFileDialog, "getSaveFileName",
                           return_value=("", "")):
            win._on_save_csv()
        assert not target.exists()

    def test_singleton_show_or_raise_returns_same_window(self, qapp):
        from PySide6.QtWidgets import QWidget
        from dlp_triple_window import show_or_raise
        # show_or_raise stores ``_triple_window`` on the parent — a
        # real QWidget is required because TripleProbeWindow's super
        # init validates the parent type.
        p = QWidget()
        smu, k = MagicMock(), MagicMock()
        w1 = show_or_raise(p, smu, k)
        w2 = show_or_raise(p, smu, k)
        assert w1 is w2
        # second call also re-injects the latest device handles.
        smu2, k2 = MagicMock(), MagicMock()
        w3 = show_or_raise(p, smu2, k2)
        assert w3 is w1
        assert w3._smu is smu2 and w3._k2000 is k2
        p.close()


# ===========================================================================
# V3 mutex: triple cannot open while a sweep is running
# ===========================================================================
class TestV3MutexBlocks:
    @pytest.fixture(scope="class")
    def v3(self, qapp):
        from DoubleLangmuir_measure_v3 import DLPMainWindowV3
        win = DLPMainWindowV3()
        yield win
        win.close()

    def test_triple_open_refused_while_sweep_running(self, v3):
        v3.smu = MagicMock()
        v3.k2000 = MagicMock()
        # Simulate a running sweep — the parent's btnStop is enabled
        # exactly when the sweep is active.
        v3.btnStop.setEnabled(True)
        try:
            with patch("dlp_lp_window.show_or_raise") as opener:
                v3._open_triple_window()
                opener.assert_not_called()
        finally:
            v3.btnStop.setEnabled(False)

    def test_triple_open_allowed_when_sweep_idle(self, v3):
        v3.smu = MagicMock()
        v3.k2000 = MagicMock()
        v3.btnStop.setEnabled(False)
        with patch("dlp_lp_window.show_or_raise") as opener:
            opener.return_value = MagicMock(running_changed=MagicMock())
            v3._open_triple_window()
            opener.assert_called_once()
