"""Tests for the modal probe-cleaning dialog and its V3 wiring."""
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
    smu.read_voltage.return_value = -100.0
    smu.read_current.return_value = 0.05
    return smu


# ===========================================================================
# Dialog / validation
# ===========================================================================
class TestDialogStructure:
    def test_defaults(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        dlg = CleaningDialog(_smu_mock())
        assert dlg.spnDuration.value() == pytest.approx(10.0)
        assert dlg.spnVoltage.value() == pytest.approx(-100.0)
        assert dlg.spnCurrentLimit.value() == pytest.approx(0.1)

    def test_voltage_spinbox_only_negative(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        dlg = CleaningDialog(_smu_mock())
        assert dlg.spnVoltage.maximum() == 0
        assert dlg.spnVoltage.minimum() < 0

    def test_modal(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        dlg = CleaningDialog(_smu_mock())
        assert dlg.isModal()

    def test_buttons_present(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        dlg = CleaningDialog(_smu_mock())
        assert dlg.btnStart.isEnabled()
        assert not dlg.btnStop.isEnabled()


# ===========================================================================
# Cleaning flow
# ===========================================================================
class TestCleaningFlow:
    def test_start_writes_smu_in_correct_order(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu)
        dlg._on_start()
        smu.set_output_low.assert_called_with("GRO")
        smu.set_current_limit.assert_called_with(0.1)
        smu.set_voltage.assert_any_call(-100.0)
        smu.output.assert_any_call(True)
        assert dlg.is_running
        assert not dlg.btnStart.isEnabled()
        assert dlg.btnStop.isEnabled()
        dlg._on_stop()

    def test_voltage_zero_or_positive_aborts(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu)
        dlg.spnVoltage.setValue(0.0)
        dlg._on_start()
        smu.output.assert_not_called()
        assert not dlg.is_running

    def test_stop_brings_smu_to_safe_state(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu)
        dlg._on_start()
        dlg._on_stop()
        smu.set_voltage.assert_any_call(0.0)
        smu.output.assert_any_call(False)
        assert not dlg.is_running
        assert dlg.btnStart.isEnabled()
        assert not dlg.btnStop.isEnabled()

    def test_finish_brings_smu_to_safe_state(self, qapp):
        """When the timer naturally elapses the same shutdown runs."""
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu, duration_s=0.1)
        dlg._on_start()
        # Force the elapsed timer past the duration and tick once.
        dlg._duration_ms = -1
        dlg._tick()
        smu.set_voltage.assert_any_call(0.0)
        smu.output.assert_any_call(False)
        assert not dlg.is_running

    def test_close_aborts_running_cleaning(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu)
        dlg._on_start()
        dlg.reject()
        smu.set_voltage.assert_any_call(0.0)
        smu.output.assert_any_call(False)

    def test_restore_prev_output_low_when_not_gro(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu, prev_output_low="FLO")
        dlg._on_start()
        # During run: GRO forced.
        assert any(c.args == ("GRO",)
                   for c in smu.set_output_low.call_args_list)
        dlg._on_stop()
        # After run: original FLO restored.
        assert any(c.args == ("FLO",)
                   for c in smu.set_output_low.call_args_list)


# ===========================================================================
# Simulation mode (fake current 0.777 A)
# ===========================================================================
class TestSimulation:
    def test_sim_current_overrides_read_current(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        dlg = CleaningDialog(smu, sim_current_a=0.777)
        dlg._on_start()
        dlg._tick()
        assert "0.7770" in dlg.lblActualI.text()
        # smu.read_current must NOT have been queried at all.
        smu.read_current.assert_not_called()
        dlg._on_stop()

    def test_sim_mode_voltage_still_read_from_smu(self, qapp):
        from dlp_cleaning_dialog import CleaningDialog
        smu = _smu_mock()
        smu.read_voltage.return_value = -42.5
        dlg = CleaningDialog(smu, sim_current_a=0.777)
        dlg._on_start()
        dlg._tick()
        assert "-42.5" in dlg.lblActualV.text()
        dlg._on_stop()


# ===========================================================================
# V3 wiring
# ===========================================================================
@pytest.fixture(scope="module")
def v3_window(qapp):
    from DoubleLangmuir_measure_v3 import DLPMainWindowV3
    win = DLPMainWindowV3()
    yield win
    win.close()


class TestV3CleaningWiring:
    def test_button_connected(self, v3_window):
        # Iter scope guard: cleaning + triple are wired by now;
        # single + double remain signal-less.
        assert v3_window.btnMethodCleaning.receivers("2clicked()") >= 1
        assert v3_window.btnMethodTriple.receivers("2clicked()") >= 1
        for b in (v3_window.btnMethodSingle, v3_window.btnMethodDouble):
            assert b.receivers("2clicked()") == 0

    def test_open_without_connection_only_logs(self, v3_window):
        # No SMU connected → must NOT spawn a real dialog, just log.
        assert v3_window.smu is None
        with patch("dlp_cleaning_dialog.CleaningDialog") as mock_cls:
            v3_window._open_cleaning_dialog()
            mock_cls.assert_not_called()

    def test_open_with_sim_passes_fake_current(self, v3_window):
        # Activate sim mode the same way the SMU connect path does.
        from fake_b2901_v2 import FakeB2901v2
        v3_window.smu = FakeB2901v2()
        v3_window.smu.connect()
        try:
            with patch("dlp_cleaning_dialog.CleaningDialog") as mock_cls:
                v3_window._open_cleaning_dialog()
                mock_cls.assert_called_once()
                args, kwargs = mock_cls.call_args
                assert args and args[0] is v3_window.smu
                assert kwargs.get("sim_current_a") == pytest.approx(0.777)
        finally:
            v3_window.smu.close()
            v3_window.smu = None
