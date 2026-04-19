"""Tests for iteration 4c: Remote-Sense / 4-wire support.

Covers:
* driver setter writes the canonical :SYST:RSEN command
* defensive fallback to legacy :SENS:REM when the canonical form raises
* default option is False (2-wire) so existing setups stay unchanged
* normalize_options coerces truthy/falsey forms into a strict bool
* presets do NOT carry remote_sense (hardware-setup hoheit beim Nutzer)
* apply_instrument_options invokes set_remote_sense at the end
* dialog roundtrip preserves the user's choice
* defensive behaviour with FakeB2901 (which has no setter)
"""
from __future__ import annotations

import os
import pathlib
import sys
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# Driver setter
# ---------------------------------------------------------------------------
class TestDriverSetter:
    def _smu(self):
        smu = KeysightB2901PSU()
        smu._inst = MagicMock()
        return smu

    @pytest.mark.parametrize("enabled,token", [(True, "ON"), (False, "OFF")])
    def test_writes_canonical_syst_rsen(self, enabled, token):
        smu = self._smu()
        smu.set_remote_sense(enabled)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert f":SYST:RSEN {token}" in cmds
        # the legacy form must NOT be issued when the canonical write
        # succeeded — otherwise older firmware would receive duplicate
        # writes and we'd lose the diagnostic value of the fallback.
        assert not any(c.startswith(":SENS:REM") for c in cmds)

    def test_coerces_truthy_value(self):
        smu = self._smu()
        smu.set_remote_sense(1)
        smu.set_remote_sense(0)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SYST:RSEN ON" in cmds
        assert ":SYST:RSEN OFF" in cmds

    def test_falls_back_to_legacy_form_when_canonical_raises(self):
        smu = self._smu()
        seen: list[str] = []

        def fake_write(cmd: str) -> None:
            seen.append(cmd)
            if cmd.startswith(":SYST:RSEN"):
                raise RuntimeError("not supported on this firmware")

        smu._inst.write.side_effect = fake_write
        smu.set_remote_sense(True)
        assert seen == [":SYST:RSEN ON", ":SENS:REM ON"]

    def test_propagates_failure_when_both_forms_raise(self):
        """If even the legacy form fails, the caller (apply-path) is in
        charge of deciding what to do — the setter must not silently
        swallow a hard error."""
        smu = self._smu()
        smu._inst.write.side_effect = RuntimeError("nope")
        with pytest.raises(RuntimeError):
            smu.set_remote_sense(True)


# ---------------------------------------------------------------------------
# Options model
# ---------------------------------------------------------------------------
class TestOptionsModel:
    def test_default_is_false(self):
        assert DEFAULT_INSTRUMENT_OPTIONS["remote_sense"] is False

    def test_normalize_coerces_truthy(self):
        assert normalize_options({"remote_sense": 1})["remote_sense"] is True
        assert normalize_options({"remote_sense": "yes"})["remote_sense"] is True

    def test_normalize_coerces_falsy(self):
        assert normalize_options({"remote_sense": 0})["remote_sense"] is False
        assert normalize_options({"remote_sense": ""})["remote_sense"] is False
        assert normalize_options({"remote_sense": None})["remote_sense"] is False

    def test_normalize_fills_default_when_missing(self):
        assert normalize_options({})["remote_sense"] is False

    def test_presets_do_not_touch_remote_sense(self):
        """Hardware-setup decisions stay in user hand — same rationale
        as iter 4a output_low / beep.  A preset that silently flipped
        4-wire on would brick a 2-wire cable harness."""
        for name, preset in INSTRUMENT_PRESETS.items():
            assert "remote_sense" not in preset, name


# ---------------------------------------------------------------------------
# Apply-path
# ---------------------------------------------------------------------------
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
    def set_remote_sense(self, e):
        self.calls.append(("set_remote_sense", (e,), {}))


class TestApplyPath:
    def test_apply_calls_remote_sense_with_user_value(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options({"remote_sense": True}))
        rs = next(c for c in smu.calls if c[0] == "set_remote_sense")
        assert rs[1][0] is True

    def test_apply_calls_remote_sense_off_by_default(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options(None))
        rs = next(c for c in smu.calls if c[0] == "set_remote_sense")
        assert rs[1][0] is False

    def test_apply_writes_remote_sense_after_compliance_and_4a_setters(self):
        """Order matters: remote_sense sits at the very end so it
        survives every preceding configuration step (and a *RST + re-
        apply still toggles back to the user's choice)."""
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options(
            {"remote_sense": True, "beep": True, "output_low": "FLO"}))
        order = [c[0] for c in smu.calls]
        i_compl = order.index("set_current_limit")
        i_ol = order.index("set_output_low")
        i_beep = order.index("set_beep")
        i_rs = order.index("set_remote_sense")
        assert i_compl < i_rs
        assert i_ol < i_rs
        assert i_beep < i_rs

    def test_apply_tolerates_missing_setter_on_fake(self):
        from fake_b2901 import FakeB2901
        fake = FakeB2901()
        fake.connect()
        # FakeB2901 has no set_remote_sense — must not abort the rest.
        apply_instrument_options(fake, normalize_options(
            {"remote_sense": True, "compliance_A": 0.005}))
        assert fake.current_compliance == pytest.approx(0.005)

    def test_apply_tolerates_missing_setter_on_fake_v2(self):
        from fake_b2901_v2 import FakeB2901v2
        fake = FakeB2901v2()
        fake.connect()
        apply_instrument_options(fake, normalize_options(
            {"remote_sense": True, "compliance_A": 0.007}))
        assert fake.current_compliance == pytest.approx(0.007)

    def test_apply_continues_when_remote_sense_raises(self):
        smu = _RecordingSMU()
        smu.set_remote_sense = MagicMock(side_effect=RuntimeError("nope"))
        # Other setters earlier in the chain must still have run.
        apply_instrument_options(smu, normalize_options(
            {"remote_sense": True, "beep": True}))
        names = [c[0] for c in smu.calls]
        assert "set_current_limit" in names
        assert "set_beep" in names


# ---------------------------------------------------------------------------
# Dialog roundtrip + UI guards
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestDialogRoundtrip:
    def _make(self, opts=None):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        return InstrumentOptionsDialog(opts)

    def test_widget_exists_and_default_unchecked(self, qapp):
        dlg = self._make()
        assert hasattr(dlg, "chkRemoteSense")
        assert dlg.chkRemoteSense.isChecked() is False

    def test_widget_reflects_initial_option(self, qapp):
        dlg = self._make({"remote_sense": True})
        assert dlg.chkRemoteSense.isChecked() is True

    def test_get_options_emits_user_choice(self, qapp):
        dlg = self._make({"remote_sense": True})
        opts = dlg.get_options()
        assert opts["remote_sense"] is True
        dlg.chkRemoteSense.setChecked(False)
        assert dlg.get_options()["remote_sense"] is False

    def test_preset_change_does_not_flip_remote_sense(self, qapp):
        """Selecting any preset must leave the user's 4-wire choice
        alone — same contract as output_low / beep."""
        dlg = self._make({"remote_sense": True})
        for name in INSTRUMENT_PRESETS:
            dlg.chkRemoteSense.setChecked(True)
            dlg._apply_preset(name)
            assert dlg.chkRemoteSense.isChecked() is True, name

    def test_tooltip_warns_about_wiring(self, qapp):
        """A user reading the tooltip must be told that wrong wiring +
        4-wire ON breaks the measurement.  We assert on a load-bearing
        keyword so the warning can't be silently weakened later."""
        dlg = self._make()
        tip = dlg.chkRemoteSense.toolTip().lower()
        assert "sense" in tip
        assert "wiring" in tip or "leads" in tip
