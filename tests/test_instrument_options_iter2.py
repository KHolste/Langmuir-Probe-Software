"""Tests for iteration 2 of the Instrument-Options dialog.

Scope:
    * Driver setters: set_autozero, set_averaging, set_source_delay
    * Options-model: normalize / validate / get_nplc with custom NPLC
    * Apply-path: order + defensive fallback for drivers that lack
      the new setters (e.g. FakeB2901)
    * Dialog-roundtrip for the new widgets
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
    validate_options,
    get_nplc,
    NPLC_MIN, NPLC_MAX,
    SOURCE_DELAY_MAX_S, HW_AVG_COUNT_MAX,
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

    # ── Auto-Zero ─────────────────────────────────────────────────────
    @pytest.mark.parametrize("state", ["OFF", "ON", "ONCE"])
    def test_autozero_writes_enum_state(self, state):
        smu = self._smu()
        smu.set_autozero(state)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert f":SENS:AZER:STAT {state}" in cmds

    def test_autozero_accepts_lowercase(self):
        smu = self._smu()
        smu.set_autozero("once")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:AZER:STAT ONCE" in cmds

    def test_autozero_rejects_unknown_state(self):
        smu = self._smu()
        with pytest.raises(ValueError):
            smu.set_autozero("sometimes")

    # ── Averaging ────────────────────────────────────────────────────
    def test_averaging_enables_with_count_and_mode(self):
        smu = self._smu()
        smu.set_averaging(True, count=8, mode="MOV")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:AVER:TCON MOV" in cmds
        assert ":SENS:AVER:COUN 8" in cmds
        assert ":SENS:AVER ON" in cmds

    def test_averaging_disabled_still_writes_count(self):
        """Disabling must still write count/mode so a later enable
        reuses the configured value instead of some stale one."""
        smu = self._smu()
        smu.set_averaging(False, count=16, mode="REP")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:AVER:TCON REP" in cmds
        assert ":SENS:AVER:COUN 16" in cmds
        assert ":SENS:AVER OFF" in cmds

    def test_averaging_clamps_count(self):
        smu = self._smu()
        smu.set_averaging(True, count=10000, mode="REP")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:AVER:COUN 100" in cmds
        smu._inst.reset_mock()
        smu.set_averaging(True, count=0, mode="REP")
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:AVER:COUN 1" in cmds

    def test_averaging_rejects_unknown_mode(self):
        smu = self._smu()
        with pytest.raises(ValueError):
            smu.set_averaging(True, count=4, mode="FOO")

    # ── Source-Delay ─────────────────────────────────────────────────
    def test_source_delay_writes_seconds(self):
        smu = self._smu()
        smu.set_source_delay(0.005)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert any(c.startswith(":SOUR:DEL ") and "0.005" in c
                   for c in cmds)

    def test_source_delay_zero_is_valid(self):
        smu = self._smu()
        smu.set_source_delay(0.0)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert any(c.startswith(":SOUR:DEL ") for c in cmds)

    def test_source_delay_rejects_negative(self):
        smu = self._smu()
        with pytest.raises(ValueError):
            smu.set_source_delay(-0.001)


# ===========================================================================
# Options model: normalize / validate / get_nplc
# ===========================================================================
class TestOptionsModel:
    def test_defaults_include_iter2_fields(self):
        for key in ("custom_nplc_enabled", "custom_nplc", "autozero",
                    "source_delay_s", "hw_avg_enabled",
                    "hw_avg_count", "hw_avg_mode"):
            assert key in DEFAULT_INSTRUMENT_OPTIONS, key

    def test_normalize_upper_cases_enum_values(self):
        out = normalize_options({"autozero": "once", "hw_avg_mode": "mov"})
        assert out["autozero"] == "ONCE"
        assert out["hw_avg_mode"] == "MOV"

    def test_normalize_falls_back_on_garbage_values(self):
        out = normalize_options({
            "autozero": "sometimes",
            "hw_avg_mode": "weird",
            "hw_avg_count": "abc",
            "source_delay_s": "not-a-number",
        })
        assert out["autozero"] == "ON"
        assert out["hw_avg_mode"] == "REP"
        assert out["hw_avg_count"] == 4
        assert out["source_delay_s"] == 0.0

    def test_get_nplc_prefers_custom_when_enabled(self):
        assert get_nplc({"custom_nplc_enabled": True,
                         "custom_nplc": 0.25}) == pytest.approx(0.25)

    def test_get_nplc_falls_back_to_preset_when_disabled(self):
        assert get_nplc({"custom_nplc_enabled": False,
                         "speed_preset": "Slow (10)"}) == pytest.approx(10.0)

    def test_get_nplc_guards_bad_custom_value(self):
        # A string that cannot be converted should not crash get_nplc.
        assert get_nplc({"custom_nplc_enabled": True,
                         "custom_nplc": "nope"}) == pytest.approx(0.1)

    # ── Validator ────────────────────────────────────────────────────
    def test_validator_passes_defaults(self):
        assert validate_options(normalize_options(None)) == []

    def test_validator_flags_custom_nplc_out_of_range(self):
        msgs = validate_options(normalize_options(
            {"custom_nplc_enabled": True, "custom_nplc": 0.0001}))
        assert any("Custom NPLC" in m for m in msgs)
        msgs = validate_options(normalize_options(
            {"custom_nplc_enabled": True, "custom_nplc": 500.0}))
        assert any("Custom NPLC" in m for m in msgs)

    def test_validator_flags_source_delay_too_large(self):
        msgs = validate_options(normalize_options(
            {"source_delay_s": SOURCE_DELAY_MAX_S + 1.0}))
        assert any("Source delay" in m for m in msgs)

    def test_validator_flags_hw_avg_count_out_of_range(self):
        msgs = validate_options(normalize_options(
            {"hw_avg_enabled": True, "hw_avg_count": 10_000}))
        # Iteration-2 behaviour: normalize clamps, so validate actually
        # gets a clamped-but-still-valid value; the raw path (dialog
        # bypasses normalize) is what we test here.
        # Use a raw dict that skips normalize:
        raw = dict(DEFAULT_INSTRUMENT_OPTIONS)
        raw["hw_avg_enabled"] = True
        raw["hw_avg_count"] = HW_AVG_COUNT_MAX + 10
        msgs = validate_options(raw)
        assert any("HW averaging" in m for m in msgs)


# ===========================================================================
# Apply-path: order + defensive fallback
# ===========================================================================
class _RecordingSMU:
    def __init__(self, *, support_iter2: bool = True):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.support_iter2 = support_iter2
        self.current_compliance = None

    # iteration-1 methods (always present)
    def set_nplc(self, v):
        self.calls.append(("set_nplc", (v,), {}))

    def enable_output_protection(self, v):
        self.calls.append(("enable_output_protection", (v,), {}))

    def set_current_range(self, v):
        self.calls.append(("set_current_range", (v,), {}))

    def set_current_limit(self, v):
        self.current_compliance = v
        self.calls.append(("set_current_limit", (v,), {}))

    # iteration-2 methods – only if supported
    def set_autozero(self, state):
        if not self.support_iter2:
            raise AttributeError
        self.calls.append(("set_autozero", (state,), {}))

    def set_averaging(self, enabled, count=1, mode="REP"):
        if not self.support_iter2:
            raise AttributeError
        self.calls.append(("set_averaging", (enabled,),
                           {"count": count, "mode": mode}))

    def set_source_delay(self, seconds):
        if not self.support_iter2:
            raise AttributeError
        self.calls.append(("set_source_delay", (seconds,), {}))


class TestApplyPath:
    def test_apply_order_includes_iter2_setters_between_nplc_and_range(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options({
            "autozero": "Once",
            "hw_avg_enabled": True,
            "hw_avg_count": 6,
            "hw_avg_mode": "MOV",
            "source_delay_s": 0.002,
        }))
        order = [c[0] for c in smu.calls]
        # required relative order per §5 of the plan
        for a, b in [
            ("set_nplc", "set_source_delay"),
            ("set_source_delay", "set_autozero"),
            ("set_autozero", "set_averaging"),
            ("set_averaging", "enable_output_protection"),
            ("enable_output_protection", "set_current_range"),
            ("set_current_range", "set_current_limit"),
        ]:
            assert order.index(a) < order.index(b), (a, b, order)

    def test_apply_skips_iter2_setter_when_driver_lacks_it(self):
        """An older driver / FakeB2901 has no set_autozero etc. – the
        apply path must log and move on without crashing."""
        from fake_b2901 import FakeB2901
        fake = FakeB2901()
        fake.connect()
        # None of set_autozero / set_averaging / set_source_delay exists
        # on FakeB2901; must not raise.
        apply_instrument_options(fake, normalize_options({
            "autozero": "ONCE",
            "hw_avg_enabled": True,
            "hw_avg_count": 4,
            "hw_avg_mode": "REP",
            "source_delay_s": 0.003,
        }))
        # Compliance still comes through via set_current_limit.
        assert fake.current_compliance is not None

    def test_apply_swallows_setter_exception(self):
        """If a setter is present but raises (e.g. unsupported SCPI on a
        specific model), the rest of the configuration must still run."""
        smu = _RecordingSMU()

        def boom(*a, **kw):
            raise RuntimeError("unsupported on this model")

        smu.set_autozero = boom  # type: ignore
        apply_instrument_options(smu, normalize_options(None))
        # set_current_limit must have been called after the failure.
        assert any(c[0] == "set_current_limit" for c in smu.calls)

    def test_apply_uses_custom_nplc_value(self):
        smu = _RecordingSMU()
        apply_instrument_options(smu, normalize_options({
            "custom_nplc_enabled": True, "custom_nplc": 0.42,
        }))
        nplc_args = [c[1][0] for c in smu.calls if c[0] == "set_nplc"]
        assert nplc_args == [pytest.approx(0.42)]


# ===========================================================================
# Dialog roundtrip
# ===========================================================================
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestDialog:
    def _make(self, opts=None):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        return InstrumentOptionsDialog(opts)

    def test_default_has_new_widgets(self, qapp):
        dlg = self._make()
        for name in ("cmbAutoZero", "chkCustomNplc", "spnCustomNplc",
                     "spnSourceDelay", "chkHwAvg",
                     "spnHwAvgCount", "cmbHwAvgMode"):
            assert hasattr(dlg, name), name

    def test_default_values_are_conservative(self, qapp):
        dlg = self._make()
        assert dlg.cmbAutoZero.currentText() == "ON"
        assert dlg.chkHwAvg.isChecked() is False
        assert dlg.spnSourceDelay.value() == pytest.approx(0.0)
        assert dlg.chkCustomNplc.isChecked() is False

    def test_custom_nplc_disables_preset_combo(self, qapp):
        dlg = self._make()
        assert dlg.cmbSpeed.isEnabled() is True
        dlg.chkCustomNplc.setChecked(True)
        assert dlg.cmbSpeed.isEnabled() is False
        assert dlg.spnCustomNplc.isEnabled() is True
        dlg.chkCustomNplc.setChecked(False)
        assert dlg.cmbSpeed.isEnabled() is True

    def test_hw_avg_toggle_disables_subfields(self, qapp):
        dlg = self._make()
        assert dlg.spnHwAvgCount.isEnabled() is False
        assert dlg.cmbHwAvgMode.isEnabled() is False
        dlg.chkHwAvg.setChecked(True)
        assert dlg.spnHwAvgCount.isEnabled() is True
        assert dlg.cmbHwAvgMode.isEnabled() is True

    def test_get_options_roundtrip(self, qapp):
        dlg = self._make({
            "custom_nplc_enabled": True, "custom_nplc": 0.33,
            "autozero": "OFF",
            "source_delay_s": 0.004,
            "hw_avg_enabled": True,
            "hw_avg_count": 12,
            "hw_avg_mode": "MOV",
        })
        out = dlg.get_options()
        assert out["custom_nplc_enabled"] is True
        assert out["custom_nplc"] == pytest.approx(0.33)
        assert out["autozero"] == "OFF"
        assert out["source_delay_s"] == pytest.approx(0.004)
        assert out["hw_avg_enabled"] is True
        assert out["hw_avg_count"] == 12
        assert out["hw_avg_mode"] == "MOV"

    def test_preset_precise_enables_hw_averaging(self, qapp):
        dlg = self._make()
        dlg.cmbPreset.setCurrentText("Precise (slow)")
        opts = dlg.get_options()
        assert opts["hw_avg_enabled"] is True
        assert opts["autozero"] == "ON"
        assert opts["source_delay_s"] > 0

    def test_preset_does_not_force_custom_nplc_off(self, qapp):
        """Presets must leave the user's Custom-NPLC flag untouched –
        otherwise expert tuning would silently revert on preset clicks."""
        dlg = self._make({"custom_nplc_enabled": True, "custom_nplc": 0.7})
        dlg.cmbPreset.setCurrentText("Fast (magnetron)")
        opts = dlg.get_options()
        assert opts["custom_nplc_enabled"] is True
        assert opts["custom_nplc"] == pytest.approx(0.7)

    def test_ok_disabled_on_invalid_custom_nplc(self, qapp):
        """Validator feedback must disable the OK button."""
        dlg = self._make({"custom_nplc_enabled": True, "custom_nplc": 0.1})
        assert dlg._btn_ok.isEnabled() is True
        # Directly setting a value outside the spinbox range is clipped
        # by Qt.  Emulate the invalid state by pushing the option dict
        # through the validator instead:
        dlg.spnCustomNplc.setRange(0.0, 1000.0)  # open up briefly
        dlg.spnCustomNplc.setValue(500.0)
        dlg._refresh_warnings()
        assert dlg._btn_ok.isEnabled() is False
        assert "Custom NPLC" in dlg.lblWarn.text()
