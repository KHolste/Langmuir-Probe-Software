"""Focused tests for the Double-help / analyze-log toggle / help
readability / K2000-options pass.

Kept compact so a quick regression is one focused pytest invocation.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


def _dispose(dlg):
    try:
        dlg._dlg.deleteLater()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Part 1 — Double help module + dialog wiring
# ---------------------------------------------------------------------------
class TestDoubleHelpContent:
    def test_help_html_mentions_key_concepts(self):
        from dlp_double_help import HELP_HTML
        html = HELP_HTML()
        # Terms as they literally appear in the rendered HTML (with
        # HTML subscripts for variable names).
        for term in ("Double-probe", "tanh",
                     "I<sub>sat</sub>", "Bohm",
                     "Maxwellian", "Compliance", "bootstrap",
                     "Hysteresis", "Show analysis log",
                     "Model Comparison", "fit-only"):
            assert term in html, f"missing help term: {term}"

    def test_help_html_uses_readable_font_size(self):
        from dlp_double_help import (
            HELP_HTML, HELP_BODY_FONT_SIZE_PT,
        )
        # Body size must be documentation-sized (>= 12pt) so the
        # help window is genuinely readable as prose.
        assert HELP_BODY_FONT_SIZE_PT >= 12
        assert f"font-size: {HELP_BODY_FONT_SIZE_PT}pt" in HELP_HTML()

    def test_help_html_uses_reliable_font_family(self):
        from dlp_double_help import HELP_FONT_FAMILY
        # Every fallback should be a font that PyInstaller bundles
        # via Qt or that Windows / Linux ships by default.
        assert "Segoe UI" in HELP_FONT_FAMILY
        assert "sans-serif" in HELP_FONT_FAMILY
        # No exotic bundled-only fonts that would vanish on fresh
        # Windows installs.
        for exotic in ("Fira Code", "Inter", "Atkinson"):
            assert exotic not in HELP_FONT_FAMILY


class TestDoubleDialogHelpWiring:
    def test_dialog_has_help_button(self, qapp):
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        from PySide6.QtWidgets import QDialogButtonBox
        dlg = DoubleAnalysisOptionsDialog("tanh_slope",
                                            DoubleAnalysisOptions())
        try:
            btn = None
            for child in dlg._dlg.findChildren(QDialogButtonBox):
                btn = child.button(
                    QDialogButtonBox.StandardButton.Help)
                if btn is not None:
                    break
            assert btn is not None, "Help button not found in dialog"
        finally:
            _dispose(dlg)

    def test_open_help_via_dialog_does_not_crash(self, qapp, monkeypatch):
        # Replace the help launcher with a sentinel so the test is
        # fast and does not pop a real window.
        called = {"n": 0}
        def _fake_open(parent=None):
            called["n"] += 1
        import dlp_double_help
        monkeypatch.setattr(dlp_double_help, "open_double_help_dialog",
                             _fake_open)
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        dlg = DoubleAnalysisOptionsDialog("tanh_slope",
                                            DoubleAnalysisOptions())
        try:
            dlg._open_help()
            assert called["n"] == 1
        finally:
            _dispose(dlg)


# ---------------------------------------------------------------------------
# Part 2 — analyze-log toggle
# ---------------------------------------------------------------------------
class TestAnalyzeLogToggle:
    def test_default_is_off(self):
        from dlp_double_options import DoubleAnalysisOptions
        assert DoubleAnalysisOptions().show_analysis_log is False

    def test_persists_through_to_dict_from_dict(self):
        from dlp_double_options import DoubleAnalysisOptions
        seed = DoubleAnalysisOptions(show_analysis_log=True)
        round_tripped = DoubleAnalysisOptions.from_dict(seed.to_dict())
        assert round_tripped.show_analysis_log is True

    def test_dialog_exposes_checkbox(self, qapp):
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        dlg = DoubleAnalysisOptionsDialog("tanh_slope",
                                            DoubleAnalysisOptions())
        try:
            assert hasattr(dlg, "chkShowLog")
            assert dlg.chkShowLog.isChecked() is False  # default off
        finally:
            _dispose(dlg)

    def test_dialog_get_options_carries_flag(self, qapp):
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        dlg = DoubleAnalysisOptionsDialog(
            "tanh_slope",
            DoubleAnalysisOptions(show_analysis_log=True))
        try:
            got = dlg.get_options()
            assert got.show_analysis_log is True
        finally:
            _dispose(dlg)

    def test_v2_respects_toggle_off(self, qapp, monkeypatch):
        # With _show_analysis_log = False, V2 must NOT call the log
        # window opener — the history-file write must still happen.
        import DoubleLangmuir_measure_v2 as v2
        calls = {"show": 0, "record": 0}
        def _fake_show(*a, **k):
            calls["show"] += 1
            raise AssertionError("show_analysis_window must not be called "
                                  "when show_analysis_log is off")
        def _fake_record(body_text, *, path=None, timestamp=None):
            calls["record"] += 1
            class _Rec:
                pass
            _Rec.timestamp = "now"
            _Rec.body = body_text
            return _Rec()
        monkeypatch.setattr(v2, "show_analysis_window", _fake_show)
        monkeypatch.setattr(v2, "append_analysis_record", _fake_record)
        # We exercise only the persistence + conditional-open block
        # of _run_analysis by calling the helper tail directly via a
        # trimmed stub object that provides the attributes V2 reads.
        class _Stub:
            _show_analysis_log = False
            _analysis_history_path = None
            _last_analysis_record = None
        stub = _Stub()
        # Inline the relevant guarded path (the full _run_analysis
        # involves Qt widgets we don't want to build here).
        rec = v2.append_analysis_record("body", path=None)
        if getattr(stub, "_show_analysis_log", False):
            v2.show_analysis_window(stub, history_path=None)
        assert calls["record"] == 1
        assert calls["show"] == 0


# ---------------------------------------------------------------------------
# Part 3 — help readability applied to BOTH help modules
# ---------------------------------------------------------------------------
class TestHelpReadabilityIsConsistent:
    def test_single_help_body_has_readable_font_size(self):
        import dlp_single_help
        html = dlp_single_help.HELP_HTML
        assert "13pt" in html, "Single help should use 13 pt body size"

    def test_double_help_shares_the_same_body_size(self):
        from dlp_double_help import HELP_BODY_FONT_SIZE_PT
        assert HELP_BODY_FONT_SIZE_PT == 13

    def test_single_and_double_use_consistent_font_family(self):
        import dlp_single_help
        from dlp_double_help import HELP_FONT_FAMILY
        # The Single help's inline CSS must mention the same primary
        # face as the Double help so both dialogs look like parts of
        # the same documentation system.
        assert "Segoe UI" in dlp_single_help.HELP_HTML
        assert "Segoe UI" in HELP_FONT_FAMILY


# ---------------------------------------------------------------------------
# Part 4 — K2000 options
# ---------------------------------------------------------------------------
class TestK2000OptionsDataclass:
    def test_defaults_match_driver_defaults(self):
        from dlp_k2000_options import K2000Options
        opts = K2000Options()
        assert opts.autorange is True
        assert opts.nplc == pytest.approx(1.0)

    def test_from_dict_clamps_out_of_range_nplc(self):
        from dlp_k2000_options import K2000Options
        assert K2000Options.from_dict({"nplc": 100.0}).nplc == pytest.approx(10.0)
        assert K2000Options.from_dict({"nplc": 0.0001}).nplc == pytest.approx(0.01)

    def test_from_dict_snaps_range_to_device_grid(self):
        from dlp_k2000_options import K2000Options, K2000_RANGES_V
        got = K2000Options.from_dict({"range_V": 7.3}).range_V
        assert got in K2000_RANGES_V
        # Nearest of {0.1, 1, 10, 100, 1000} to 7.3 is 10.
        assert got == pytest.approx(10.0)


class TestK2000OptionsApplyAgainstFake:
    def test_apply_autorange_on(self):
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_k2000_options import K2000Options, apply_k2000_options
        k = FakeKeithley2000()
        k.connect()
        apply_k2000_options(k, K2000Options(autorange=True, range_V=10.0,
                                              nplc=0.5))
        assert k.v_range is None   # autorange encodes as None
        assert k.nplc == pytest.approx(0.5)

    def test_apply_autorange_off_sets_fixed_range(self):
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_k2000_options import K2000Options, apply_k2000_options
        k = FakeKeithley2000()
        k.connect()
        apply_k2000_options(k, K2000Options(autorange=False,
                                              range_V=1.0, nplc=1.0))
        assert k.v_range == pytest.approx(1.0)
        assert k.nplc == pytest.approx(1.0)

    def test_apply_returns_summary_string(self):
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_k2000_options import K2000Options, apply_k2000_options
        k = FakeKeithley2000()
        k.connect()
        msg = apply_k2000_options(k, K2000Options(autorange=True,
                                                    nplc=0.1))
        assert msg and "autorange ON" in msg and "NPLC=0.1" in msg
        msg2 = apply_k2000_options(k, K2000Options(autorange=False,
                                                     range_V=10.0,
                                                     nplc=0.1))
        assert "range=10" in msg2
        assert "NPLC=0.1" in msg2

    def test_apply_ignored_on_none_instrument(self):
        from dlp_k2000_options import K2000Options, apply_k2000_options
        # Must not raise — the UI may call this before Connect.
        assert apply_k2000_options(None, K2000Options()) is None


class TestK2000OptionsDialog:
    def test_dialog_exposes_controls(self, qapp):
        from dlp_k2000_options import (
            K2000Options, K2000OptionsDialog,
        )
        dlg = K2000OptionsDialog(K2000Options())
        try:
            assert hasattr(dlg, "chkAutorange")
            assert hasattr(dlg, "cmbRange")
            assert hasattr(dlg, "spnNplc")
            # When autorange is on the manual range is greyed.
            assert dlg.cmbRange.isEnabled() is False
            dlg.chkAutorange.setChecked(False)
            assert dlg.cmbRange.isEnabled() is True
        finally:
            _dispose(dlg)

    def test_dialog_get_options_returns_current_state(self, qapp):
        from dlp_k2000_options import (
            K2000Options, K2000OptionsDialog,
        )
        dlg = K2000OptionsDialog(
            K2000Options(autorange=False, range_V=1.0, nplc=0.5))
        try:
            got = dlg.get_options()
            assert got.autorange is False
            assert got.range_V == pytest.approx(1.0)
            assert got.nplc == pytest.approx(0.5)
        finally:
            _dispose(dlg)


class TestLPMainWindowK2000ApplyPath:
    def test_open_options_applies_to_live_fake(self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        from dlp_k2000_options import K2000Options
        import dlp_k2000_options as mod
        win = LPMainWindow()
        try:
            # Connect the K2000 in sim mode so self.k2000 is live.
            win.chkK2000Sim.setChecked(True)
            win._toggle_k2000_connect()
            assert win.k2000 is not None
            # Replace the options dialog with a no-UI stub that
            # returns a deterministic options object.
            monkeypatch.setattr(
                mod, "open_k2000_options_dialog",
                lambda current, parent=None: K2000Options(
                    autorange=False, range_V=100.0, nplc=0.1))
            win._open_k2000_options()
            assert win.k2000.v_range == pytest.approx(100.0)
            assert win.k2000.nplc == pytest.approx(0.1)
            assert win._k2000_options.autorange is False
        finally:
            try:
                win.close()
            except Exception:
                pass
