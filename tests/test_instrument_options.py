"""Tests for the extended Instrument-Options dialog and apply path."""
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
    apply_instrument_options,
    normalize_options,
    validate_options,
    get_nplc,
)
from keysight_b2901 import KeysightB2901PSU  # noqa: E402


# ---------------------------------------------------------------------------
# normalize_options / validate_options – pure data layer
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_none_returns_defaults(self):
        out = normalize_options(None)
        for k, v in DEFAULT_INSTRUMENT_OPTIONS.items():
            assert out[k] == v

    def test_partial_dict_is_filled_with_defaults(self):
        out = normalize_options({"speed_preset": "Slow (10)"})
        assert out["speed_preset"] == "Slow (10)"
        assert out["compliance_A"] == DEFAULT_INSTRUMENT_OPTIONS["compliance_A"]
        assert out["autorange"] is True
        assert out["current_range_A"] is None  # autorange overrides

    def test_autorange_true_forces_range_none(self):
        out = normalize_options({"autorange": True, "current_range_A": 0.001})
        assert out["current_range_A"] is None

    def test_autorange_false_keeps_user_range(self):
        out = normalize_options({"autorange": False, "current_range_A": 0.01})
        assert out["current_range_A"] == 0.01


class TestValidate:
    def test_default_options_pass(self):
        assert validate_options(normalize_options(None)) == []

    def test_zero_compliance_is_flagged(self):
        msgs = validate_options(normalize_options({"compliance_A": 0.0}))
        assert any("Compliance" in m for m in msgs)

    def test_manual_range_required_when_autorange_off(self):
        opts = normalize_options(
            {"autorange": False, "current_range_A": None})
        msgs = validate_options(opts)
        assert any("Manual current range" in m for m in msgs)

    def test_compliance_above_range_is_flagged(self):
        opts = normalize_options(
            {"autorange": False, "current_range_A": 1e-3,
             "compliance_A": 0.05})
        msgs = validate_options(opts)
        assert any("exceeds the selected current range" in m for m in msgs)

    def test_autorange_on_skips_range_compliance_check(self):
        # Autorange ON: compliance > nominal range is not flagged (the
        # SMU will pick a suitable range itself).
        opts = normalize_options({"autorange": True,
                                   "compliance_A": 1.0})
        assert validate_options(opts) == []


# ---------------------------------------------------------------------------
# Driver: set_current_range
# ---------------------------------------------------------------------------
class TestDriverRange:
    def _smu(self):
        smu = KeysightB2901PSU()
        smu._inst = MagicMock()  # bypass real VISA
        return smu

    def test_none_enables_autorange(self):
        smu = self._smu()
        smu.set_current_range(None)
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:CURR:RANG:AUTO ON" in cmds

    def test_autorange_mode_failure_is_swallowed(self):
        smu = self._smu()
        # second write (the AUTO:MODE one) raises – must not propagate
        smu._inst.write.side_effect = [None, Exception("not supported")]
        smu.set_current_range(None)  # must not raise

    def test_fixed_range_disables_autorange(self):
        smu = self._smu()
        smu.set_current_range(0.005)  # 5 mA → rounded up to 10 mA
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert ":SENS:CURR:RANG:AUTO OFF" in cmds
        assert any(c.startswith(":SENS:CURR:RANG ") and "0.01" in c
                   for c in cmds)

    def test_fixed_range_too_large_clamped_to_max(self):
        smu = self._smu()
        smu.set_current_range(10.0)  # above 3 A ceiling
        cmds = [c.args[0] for c in smu._inst.write.call_args_list]
        assert any("3" in c for c in cmds if c.startswith(":SENS:CURR:RANG "))

    def test_negative_range_rejected(self):
        smu = self._smu()
        with pytest.raises(ValueError):
            smu.set_current_range(-1e-3)


# ---------------------------------------------------------------------------
# apply_instrument_options – orchestration
# ---------------------------------------------------------------------------
class _StubSMU:
    """Captures the order and arguments of driver calls."""

    def __init__(self, *, support_protection=True, support_range=True):
        self.calls: list[tuple[str, object]] = []
        self.current_compliance = None
        self.support_protection = support_protection
        self.support_range = support_range

    def set_nplc(self, v):
        self.calls.append(("set_nplc", v))

    def enable_output_protection(self, v):
        if not self.support_protection:
            raise RuntimeError("not supported")
        self.calls.append(("enable_output_protection", v))

    def set_current_range(self, v):
        if not self.support_range:
            raise RuntimeError("not supported")
        self.calls.append(("set_current_range", v))

    def set_current_limit(self, v):
        self.current_compliance = v
        self.calls.append(("set_current_limit", v))


class TestApplyPath:
    def test_apply_with_autorange_writes_none_to_range(self):
        smu = _StubSMU()
        apply_instrument_options(smu, {
            "speed_preset": "Fast (0.1)",
            "output_protection": True,
            "autorange": True,
            "compliance_A": 0.020,
        })
        names = [c[0] for c in smu.calls]
        assert names == ["set_nplc", "enable_output_protection",
                         "set_current_range", "set_current_limit"]
        rng_call = next(c for c in smu.calls if c[0] == "set_current_range")
        assert rng_call[1] is None
        assert smu.current_compliance == pytest.approx(0.020)

    def test_apply_with_fixed_range_passes_value(self):
        smu = _StubSMU()
        apply_instrument_options(smu, {
            "speed_preset": "Medium (1)",
            "output_protection": False,
            "autorange": False,
            "current_range_A": 0.010,
            "compliance_A": 0.005,
        })
        rng = next(c[1] for c in smu.calls if c[0] == "set_current_range")
        assert rng == pytest.approx(0.010)
        prot = next(c[1] for c in smu.calls
                    if c[0] == "enable_output_protection")
        assert prot is False

    def test_failure_in_one_setter_does_not_abort_others(self):
        smu = _StubSMU(support_protection=False)
        apply_instrument_options(smu, normalize_options(None))
        names = [c[0] for c in smu.calls]
        # protection write raised → swallowed; the rest still ran
        assert "set_nplc" in names
        assert "set_current_range" in names
        assert "set_current_limit" in names

    def test_fallback_writes_scpi_when_setter_missing(self):
        """Older drivers / fakes without set_current_range fall back to
        a defensive ``_write`` invocation."""
        smu = MagicMock(spec=["set_nplc", "enable_output_protection",
                              "set_current_limit", "_write"])
        apply_instrument_options(smu, normalize_options(
            {"autorange": False, "current_range_A": 0.1,
             "compliance_A": 0.05}))
        writes = [call.args[0] for call in smu._write.call_args_list]
        assert ":SENS:CURR:RANG:AUTO OFF" in writes
        assert any(w.startswith(":SENS:CURR:RANG ") for w in writes)

    def test_no_setter_no_writer_does_not_crash(self):
        """If a stub is so minimal it has neither setter nor _write, the
        apply path still finishes without raising."""
        smu = MagicMock(spec=["set_nplc", "enable_output_protection",
                              "set_current_limit"])
        apply_instrument_options(smu, normalize_options(None))


# ---------------------------------------------------------------------------
# Apply path runs against the real FakeB2901v2 (drop-in replacement)
# ---------------------------------------------------------------------------
class TestFakeIntegration:
    def test_apply_against_fakeb2901v2(self):
        from fake_b2901_v2 import FakeB2901v2
        fake = FakeB2901v2()
        fake.connect()
        # FakeB2901v2 inherits from FakeB2901 which absorbs **_kw, so the
        # missing setter must not raise.  We just verify the call cycle.
        apply_instrument_options(fake, {
            "speed_preset": "Fast (0.1)",
            "output_protection": True,
            "autorange": False,
            "current_range_A": 0.010,
            "compliance_A": 0.008,
        })
        # compliance ends up applied via set_current_limit
        assert fake.current_compliance == pytest.approx(0.008)


# ---------------------------------------------------------------------------
# Dialog round-trip
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestDialog:
    def _make(self, opts=None):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        return InstrumentOptionsDialog(opts)

    def test_default_dialog_has_compliance_field(self, qapp):
        dlg = self._make()
        assert dlg.spnCompliance.value() == pytest.approx(0.010)
        assert dlg.chkAutorange.isChecked() is True

    def test_manual_range_disabled_when_autorange_on(self, qapp):
        dlg = self._make({"autorange": True})
        assert dlg.cmbRange.isEnabled() is False

    def test_manual_range_enabled_when_autorange_off(self, qapp):
        dlg = self._make({"autorange": False, "current_range_A": 0.01,
                           "compliance_A": 0.005})
        assert dlg.cmbRange.isEnabled() is True

    def test_get_options_roundtrip_fixed_range(self, qapp):
        dlg = self._make({"autorange": False, "current_range_A": 0.10,
                           "compliance_A": 0.020})
        opts = dlg.get_options()
        assert opts["autorange"] is False
        assert opts["current_range_A"] == pytest.approx(0.10)
        assert opts["compliance_A"] == pytest.approx(0.020)

    def test_get_options_roundtrip_autorange(self, qapp):
        dlg = self._make({"autorange": True, "compliance_A": 0.030})
        opts = dlg.get_options()
        assert opts["autorange"] is True
        assert opts["current_range_A"] is None
        assert opts["compliance_A"] == pytest.approx(0.030)

    def test_invalid_compliance_disables_ok(self, qapp):
        dlg = self._make({"autorange": False, "current_range_A": 1e-6,
                           "compliance_A": 0.5})  # 0.5 A > 1 µA range
        assert dlg._btn_ok.isEnabled() is False
        assert "exceeds" in dlg.lblWarn.text()


# ---------------------------------------------------------------------------
# Single-source-of-truth: spnCompl <-> _instrument_opts compliance_A
# ---------------------------------------------------------------------------
class TestCompliancePropagation:
    def test_open_dialog_mirrors_spnCompl(self, qapp, monkeypatch):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        win.spnCompl.setValue(25.0)  # 25 mA
        captured = {}

        from dlp_instrument_dialog import InstrumentOptionsDialog

        def fake_init(self, opts=None, parent=None):
            captured["opts"] = dict(opts or {})
            # bypass UI construction – just emulate accepted dialog
            from PySide6.QtWidgets import QDialog
            QDialog.__init__(self, parent)

        def fake_exec(self):
            return InstrumentOptionsDialog.DialogCode.Rejected

        monkeypatch.setattr(InstrumentOptionsDialog, "__init__", fake_init)
        monkeypatch.setattr(InstrumentOptionsDialog, "exec", fake_exec)

        win._open_instrument_dialog()
        assert captured["opts"]["compliance_A"] == pytest.approx(0.025)

    def test_accept_writes_back_to_spnCompl(self, qapp, monkeypatch):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        win.spnCompl.setValue(10.0)  # 10 mA

        from dlp_instrument_dialog import InstrumentOptionsDialog

        def fake_init(self, opts=None, parent=None):
            from PySide6.QtWidgets import QDialog
            QDialog.__init__(self, parent)

        def fake_exec(self):
            return InstrumentOptionsDialog.DialogCode.Accepted

        def fake_get_options(self):
            return normalize_options({
                "speed_preset": "Fast (0.1)",
                "output_protection": True,
                "autorange": True,
                "compliance_A": 0.040,  # 40 mA
            })

        monkeypatch.setattr(InstrumentOptionsDialog, "__init__", fake_init)
        monkeypatch.setattr(InstrumentOptionsDialog, "exec", fake_exec)
        monkeypatch.setattr(InstrumentOptionsDialog, "get_options",
                            fake_get_options)

        win._open_instrument_dialog()
        assert win.spnCompl.value() == pytest.approx(40.0)
        assert win._instrument_opts["compliance_A"] == pytest.approx(0.040)
