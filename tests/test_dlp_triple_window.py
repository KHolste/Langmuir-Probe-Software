"""Phase-4 tests: non-modal Triple-Probe window + V3 wiring."""
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


def _smu_mock():
    smu = MagicMock(name="smu")
    smu.read_current.return_value = -1e-3
    smu.read_voltage.return_value = 25.0
    return smu


def _k2000_mock():
    k = MagicMock(name="k2000")
    k.read_voltage.return_value = 2.0
    return k


def _make_window(qapp, smu=None, k=None):
    from dlp_triple_window import TripleProbeWindow
    return TripleProbeWindow(smu or _smu_mock(), k or _k2000_mock())


# ===========================================================================
# Window structure
# ===========================================================================
class TestWindowStructure:
    def test_widgets_exist(self, qapp):
        win = _make_window(qapp)
        for name in ("spnVd12", "spnCompliance", "lblArea", "lblGasMix",
                     "cmbSign", "cmbEqMode", "spnTick",
                     "lblT", "lblUsupply", "lblUmeas", "lblImeas",
                     "lblTe", "lblNe", "lblSamples",
                     "btnStart", "btnStop", "btnSave", "btnClear"):
            assert hasattr(win, name), name

    def test_window_is_not_modal(self, qapp):
        win = _make_window(qapp)
        assert not win.isModal()

    def test_v_d13_sign_combo_has_both_polarities(self, qapp):
        win = _make_window(qapp)
        assert win.cmbSign.count() == 2
        data = {win.cmbSign.itemData(i) for i in range(win.cmbSign.count())}
        assert data == {+1, -1}

    def test_initial_buttons(self, qapp):
        win = _make_window(qapp)
        assert win.btnStart.isEnabled()
        assert not win.btnStop.isEnabled()
        assert not win.btnSave.isEnabled()


# ===========================================================================
# Worker integration
# ===========================================================================
class TestWorkerWiring:
    def test_start_without_devices_logs_and_does_not_construct_worker(
            self, qapp):
        from dlp_triple_window import TripleProbeWindow
        win = TripleProbeWindow(None, None)
        with patch("dlp_lp_window.TripleProbeWorker") as mock_cls:
            win._on_start()
            mock_cls.assert_not_called()

    def test_start_constructs_worker_with_user_settings(self, qapp):
        win = _make_window(qapp)
        win.spnVd12.setValue(30.0)
        win.spnCompliance.setValue(0.005)
        win.cmbSign.setCurrentIndex(1)  # -1
        with patch("dlp_lp_window.TripleProbeWorker") as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.is_running = False
            win._on_start()
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["v_d12_setpoint"] == pytest.approx(30.0)
            assert kwargs["current_limit_a"] == pytest.approx(0.005)
            assert kwargs["v_d13_sign"] == -1

    def test_sample_signal_grows_dataset_and_updates_labels(self, qapp):
        win = _make_window(qapp)
        payload = {
            "t_rel_s": 0.10, "v_d12_setpoint": 25.0,
            "v_d12_actual": 25.0, "u_meas_v": 2.0,
            "v_d13": 2.0, "i_a": -1e-3,
            "Te_eV": 2.886, "n_e_m3": 1e16,
            "species": "Argon (Ar)", "area_m2": 9.7075e-6,
            "mi_kg": 6.6e-26,
        }
        win._on_worker_sample(payload)
        assert win.lblSamples.text() == "1"
        assert "2.0" in win.lblUmeas.text() or "2.000" in win.lblUmeas.text()
        assert win.lblTe.text().startswith("2.886") or "2.89" in win.lblTe.text()

    def test_stopped_signal_resets_buttons_and_emits_running_changed(
            self, qapp):
        win = _make_window(qapp)
        states = []
        win.running_changed.connect(lambda r: states.append(r))
        # Pretend a worker exists.
        win._worker = MagicMock(is_running=False)
        win._on_worker_stopped("user")
        assert win._worker is None
        assert win.btnStart.isEnabled()
        assert states == [False]

    def test_failed_signal_marks_status_and_drops_worker(self, qapp):
        win = _make_window(qapp)
        win._worker = MagicMock(is_running=False)
        win._on_worker_failed("VISA timeout")
        assert win._worker is None
        assert "FAILED" in win.lblStatus.text()


# ===========================================================================
# Dataset / Save CSV
# ===========================================================================
class TestSaveCsv:
    def test_save_button_disabled_when_dataset_empty(self, qapp):
        win = _make_window(qapp)
        assert not win.btnSave.isEnabled()

    def test_save_writes_file_with_meta(self, qapp, tmp_path):
        win = _make_window(qapp)
        # Feed a couple of samples.
        for t in (0.0, 0.1):
            win._on_worker_sample({
                "t_rel_s": t, "v_d12_setpoint": 25.0,
                "v_d12_actual": 25.0, "u_meas_v": 2.0,
                "v_d13": 2.0, "i_a": -1e-3,
                "Te_eV": 2.886, "n_e_m3": 1e16,
                "species": "Argon (Ar)", "area_m2": 9.7075e-6,
                "mi_kg": 6.6e-26,
            })
        # Save button enabled.
        win._refresh_button_state()
        assert win.btnSave.isEnabled()

        target = tmp_path / "triple_save.csv"
        from PySide6.QtWidgets import QFileDialog
        with patch.object(QFileDialog, "getSaveFileName",
                           return_value=(str(target), "CSV (*.csv)")):
            win._on_save_csv()
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert "# Samples: 2" in text
        assert "# V_d12_setpoint_V: 25" in text


# ===========================================================================
# V3 wiring
# ===========================================================================
@pytest.fixture(scope="module")
def v3_window(qapp):
    from DoubleLangmuir_measure_v3 import DLPMainWindowV3
    win = DLPMainWindowV3()
    yield win
    win.close()


class TestV3TripleWiring:
    def test_button_connected(self, v3_window):
        assert v3_window.btnMethodTriple.receivers("2clicked()") >= 1

    def test_open_without_smu_only_logs(self, v3_window):
        v3_window.smu = None
        v3_window.k2000 = None
        with patch("dlp_lp_window.show_or_raise") as mock_open:
            v3_window._open_triple_window()
            mock_open.assert_not_called()

    def test_open_with_devices_calls_show_or_raise(self, v3_window):
        v3_window.smu = MagicMock()
        v3_window.k2000 = MagicMock()
        with patch("dlp_lp_window.show_or_raise") as mock_open:
            mock_inst = MagicMock()
            mock_inst.running_changed = MagicMock()
            mock_open.return_value = mock_inst
            v3_window._open_triple_window()
            mock_open.assert_called_once()

    def test_running_changed_locks_sweep_start(self, v3_window):
        # Both states reflect on the Sweep Start button.
        v3_window.btnStart.setEnabled(True)
        v3_window._on_triple_running_changed(True)
        assert not v3_window.btnStart.isEnabled()
        v3_window._on_triple_running_changed(False)
        assert v3_window.btnStart.isEnabled()
