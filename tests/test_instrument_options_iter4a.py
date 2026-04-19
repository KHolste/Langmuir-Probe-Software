"""Tests for iteration 4a: Advanced / Diagnostics group of the
Instrument-Options dialog – Output-Low, Beep, IDN display, Reset flow.
"""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_instrument_dialog import (  # noqa: E402
    DEFAULT_INSTRUMENT_OPTIONS,
    INSTRUMENT_PRESETS,
    apply_instrument_options,
    normalize_options,
)
from keysight_b2901 import KeysightB2901PSU  # noqa: E402


# ===========================================================================
# Driver setters
# ===========================================================================
class TestDriverSetters:
    def _smu(self):
        smu = KeysightB2901PSU()
        smu._inst = MagicMock()
        return smu

    @pytest.mark.parametrize("mode", ["GRO", "FLO"])
    def test_output_low_writes_mode(self, mode):
        smu = self._smu()
        smu.set_output_low(mode)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert f":OUTP:LOW {mode}" in cmds

    def test_output_low_accepts_lowercase(self):
        smu = self._smu()
        smu.set_output_low("flo")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":OUTP:LOW FLO" in cmds

    def test_output_low_rejects_unknown(self):
        smu = self._smu()
        with pytest.raises(ValueError):
            smu.set_output_low("GND")

    def test_beep_writes_on_and_off(self):
        smu = self._smu()
        smu.set_beep(True)
        smu.set_beep(False)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SYST:BEEP:STAT ON" in cmds
        assert ":SYST:BEEP:STAT OFF" in cmds

    def test_beep_coerces_truthy_value(self):
        smu = self._smu()
        smu.set_beep(1)
        smu.set_beep(0)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SYST:BEEP:STAT ON" in cmds
        assert ":SYST:BEEP:STAT OFF" in cmds

    def test_factory_reset_issues_rst_and_cls(self):
        smu = self._smu()
        smu.factory_reset()
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert cmds[-2:] == ["*RST", "*CLS"]


# ===========================================================================
# Options model
# ===========================================================================
class TestOptionsModel:
    def test_defaults_include_output_low_and_beep(self):
        assert DEFAULT_INSTRUMENT_OPTIONS["output_low"] == "GRO"
        assert DEFAULT_INSTRUMENT_OPTIONS["beep"] is False

    def test_normalize_upper_cases_output_low(self):
        out = normalize_options({"output_low": "flo"})
        assert out["output_low"] == "FLO"

    def test_normalize_falls_back_on_garbage_output_low(self):
        out = normalize_options({"output_low": "middle"})
        assert out["output_low"] == "GRO"

    def test_normalize_coerces_beep(self):
        assert normalize_options({"beep": 1})["beep"] is True
        assert normalize_options({"beep": 0})["beep"] is False

    def test_presets_do_not_include_output_low_or_beep(self):
        """Requirement: Output-Low / Beep are hardware-setup choices and
        must not be silently overridden when a preset is picked."""
        for name, p in INSTRUMENT_PRESETS.items():
            assert "output_low" not in p, name
            assert "beep" not in p, name


# ===========================================================================
# Apply-path: output-low + beep are written last; defensive on older drivers
# ===========================================================================
class _RecordingSMU:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.current_compliance = None

    def set_nplc(self, v): self.calls.append(("set_nplc", (v,), {}))
    def enable_output_protection(self, v):
        self.calls.append(("enable_output_protection", (v,), {}))
    def set_current_range(self, v):
        self.calls.append(("set_current_range", (v,), {}))
    def set_current_limit(self, v):
        self.current_compliance = v
        self.calls.append(("set_current_limit", (v,), {}))
    def set_autozero(self, s): self.calls.append(("set_autozero", (s,), {}))
    def set_averaging(self, e, count=1, mode="REP"):
        self.calls.append(("set_averaging", (e,), {"count": count, "mode": mode}))
    def set_source_delay(self, s):
        self.calls.append(("set_source_delay", (s,), {}))
    def set_output_low(self, m):
        self.calls.append(("set_output_low", (m,), {}))
    def set_beep(self, e):
        self.calls.append(("set_beep", (e,), {}))


class TestApplyPath:
    def test_apply_calls_output_low_and_beep_after_compliance(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options(
            {"output_low": "FLO", "beep": True}))
        order = [c[0] for c in smu.calls]
        # Output-Low / Beep sit at the end of the chain so they
        # reliably override the connect-time hardcoded defaults.
        i_compl = order.index("set_current_limit")
        i_ol = order.index("set_output_low")
        i_beep = order.index("set_beep")
        assert i_compl < i_ol
        assert i_compl < i_beep

    def test_apply_passes_user_values(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options(
            {"output_low": "FLO", "beep": True}))
        ol = next(c[1][0] for c in smu.calls if c[0] == "set_output_low")
        bp = next(c[1][0] for c in smu.calls if c[0] == "set_beep")
        assert ol == "FLO"
        assert bp is True

    def test_apply_tolerates_missing_setters_on_fake(self):
        from fake_b2901 import FakeB2901
        fake = FakeB2901()
        fake.connect()
        # Fake has no set_output_low / set_beep / factory_reset – the
        # apply-path must swallow those gracefully.
        apply_instrument_options(fake, normalize_options(
            {"output_low": "FLO", "beep": True}))
        assert fake.current_compliance is not None

    def test_apply_continues_when_setter_raises(self):
        smu = _RecordingSMU()
        smu.set_output_low = MagicMock(side_effect=RuntimeError("nope"))
        apply_instrument_options(smu, normalize_options({"beep": True}))
        # Beep must still have been called even though output_low raised.
        assert any(c[0] == "set_beep" for c in smu.calls)


# ===========================================================================
# Dialog: Advanced widgets exist, state reacts to parent context
# ===========================================================================
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeParent:
    """Minimal stand-in for the DLP main window as seen by the dialog."""
    def __init__(self, *, smu=None, idn_text="", sweep_running=False):
        from PySide6.QtWidgets import QLabel, QPushButton, QWidget
        self._widget = QWidget()
        self.smu = smu
        self.lblIdn = QLabel(idn_text)
        self.btnStop = QPushButton("Stop")
        self.btnStop.setEnabled(sweep_running)

    def deleteLater(self):  # pragma: no cover – satisfy Qt parent APIs
        self._widget.deleteLater()


class TestDialogAdvanced:
    def _make(self, parent=None, opts=None):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        return InstrumentOptionsDialog(opts, parent=getattr(parent, "_widget", parent))

    def _make_with_parent(self, parent_stub, opts=None):
        """Inject ``parent_stub`` by monkey-patching ``parent()`` on the
        dialog, because ``InstrumentOptionsDialog.__init__`` expects a
        real QWidget parent."""
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog(opts, parent=parent_stub._widget)
        # override the parent() call to return our stub's data
        dlg.parent = lambda: parent_stub  # type: ignore
        dlg._refresh_advanced_state()
        return dlg

    def test_widgets_exist(self, qapp):
        dlg = self._make()
        for name in ("lblIdnValue", "cmbOutputLow", "chkBeep", "btnReset"):
            assert hasattr(dlg, name), name

    def test_idn_shows_placeholder_when_parent_missing(self, qapp):
        dlg = self._make()
        assert "(not connected)" in dlg.lblIdnValue.text()

    def test_idn_picked_up_from_parent_label(self, qapp):
        parent = _FakeParent(smu=MagicMock(), idn_text="Keysight,B2901A,MY,1.0")
        dlg = self._make_with_parent(parent)
        assert "B2901" in dlg.lblIdnValue.text()

    def test_reset_disabled_without_connection(self, qapp):
        parent = _FakeParent(smu=None, idn_text="")
        dlg = self._make_with_parent(parent)
        assert dlg.btnReset.isEnabled() is False

    def test_reset_disabled_while_sweep_running(self, qapp):
        parent = _FakeParent(smu=MagicMock(), sweep_running=True)
        dlg = self._make_with_parent(parent)
        assert dlg.btnReset.isEnabled() is False

    def test_reset_enabled_when_connected_and_idle(self, qapp):
        parent = _FakeParent(smu=MagicMock(), sweep_running=False,
                              idn_text="Keysight,B2901A,MY,1.0")
        dlg = self._make_with_parent(parent)
        assert dlg.btnReset.isEnabled() is True

    def test_get_options_roundtrip(self, qapp):
        dlg = self._make(opts={"output_low": "FLO", "beep": True})
        opts = dlg.get_options()
        assert opts["output_low"] == "FLO"
        assert opts["beep"] is True


# ===========================================================================
# Reset flow: confirm, sweep guard, re-apply
# ===========================================================================
class TestResetFlow:
    def _make_dialog(self, parent_stub):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog(parent=parent_stub._widget)
        dlg.parent = lambda: parent_stub  # type: ignore
        dlg._refresh_advanced_state()
        return dlg

    def test_reset_cancel_does_nothing(self, qapp):
        smu = MagicMock()
        parent = _FakeParent(smu=smu, idn_text="Keysight,B2901A")
        dlg = self._make_dialog(parent)
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "exec",
                           return_value=QMessageBox.StandardButton.Cancel):
            dlg._on_reset_clicked()
        smu.factory_reset.assert_not_called()

    def test_reset_accept_runs_rst_and_reapplies(self, qapp, monkeypatch):
        smu = MagicMock(spec=["factory_reset", "set_nplc",
                              "enable_output_protection",
                              "set_current_range", "set_current_limit",
                              "set_autozero", "set_averaging",
                              "set_source_delay",
                              "set_output_low", "set_beep",
                              "_write"])
        parent = _FakeParent(smu=smu, idn_text="Keysight,B2901A")
        dlg = self._make_dialog(parent)
        from PySide6.QtWidgets import QMessageBox
        applied = {}

        def fake_apply(s, opts):
            applied["smu"] = s
            applied["opts"] = dict(opts)

        monkeypatch.setattr("dlp_instrument_dialog.apply_instrument_options",
                             fake_apply)
        with patch.object(QMessageBox, "exec",
                           return_value=QMessageBox.StandardButton.Ok):
            dlg._on_reset_clicked()

        smu.factory_reset.assert_called_once()
        assert applied.get("smu") is smu
        # Re-applied options must contain our iteration-4a fields.
        assert "output_low" in applied["opts"]
        assert "beep" in applied["opts"]

    def test_reset_without_smu_is_a_noop(self, qapp, monkeypatch):
        parent = _FakeParent(smu=None)
        dlg = self._make_dialog(parent)

        calls = []
        monkeypatch.setattr("dlp_instrument_dialog.apply_instrument_options",
                             lambda *a, **kw: calls.append(1))
        dlg._on_reset_clicked()
        assert calls == []

    def test_reset_blocked_by_sweep_running(self, qapp, monkeypatch):
        smu = MagicMock()
        parent = _FakeParent(smu=smu, sweep_running=True)
        dlg = self._make_dialog(parent)

        calls = []
        monkeypatch.setattr("dlp_instrument_dialog.apply_instrument_options",
                             lambda *a, **kw: calls.append(1))
        dlg._on_reset_clicked()  # must not call reset while sweep runs
        smu.factory_reset.assert_not_called()
        assert calls == []

    def test_reset_falls_back_to_raw_write_when_factory_reset_missing(
            self, qapp, monkeypatch):
        """Older driver / FakeB2901 has no factory_reset – the fallback
        path must issue *RST and *CLS via _write instead."""
        smu = MagicMock(spec=["_write"])
        parent = _FakeParent(smu=smu)
        dlg = self._make_dialog(parent)
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr("dlp_instrument_dialog.apply_instrument_options",
                             lambda *a, **kw: None)
        with patch.object(QMessageBox, "exec",
                           return_value=QMessageBox.StandardButton.Ok):
            dlg._on_reset_clicked()

        writes = [c.args[0] for c in smu._write.call_args_list]
        assert "*RST" in writes
        assert "*CLS" in writes
