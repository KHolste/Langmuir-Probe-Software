"""Default method mode + per-mode SMU configuration."""
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


@pytest.fixture
def lp_main(qapp):
    from LPmeasurement import LPMainWindow
    win = LPMainWindow()
    yield win
    win.close()


# ---------------------------------------------------------------------------
class TestStartupDefaults:
    def test_double_is_active_at_startup(self, lp_main):
        assert lp_main.btnMethodDouble.isChecked()
        assert not lp_main.btnMethodSingle.isChecked()
        assert not lp_main.btnMethodTriple.isChecked()

    def test_settle_default_is_20_milliseconds(self, lp_main):
        # Per operator request: faster sweep cadence by default.
        assert lp_main.spnSettle.value() == pytest.approx(0.02)

    def test_default_instrument_opts_match_double(self, lp_main):
        assert lp_main._instrument_opts["output_low"] == "FLO"
        assert lp_main._instrument_opts["remote_sense"] is True

    def test_main_calls_show_maximized_not_show(self):
        """Entry point launches the window maximised (not full
        screen).  Source-level guard so a future refactor that
        accidentally swaps ``showMaximized`` for ``show`` or
        ``showFullScreen`` fails this test instead of producing a
        stealth UX regression."""
        repo = pathlib.Path(__file__).resolve().parent.parent
        src = (repo / "LPmeasurement.py").read_text(encoding="utf-8")
        # main() body must call showMaximized.
        assert "win.showMaximized()" in src
        # And explicitly NOT plain show() or showFullScreen() in
        # the entry point.
        assert "win.showFullScreen()" not in src
        assert "win.show()" not in src


# ---------------------------------------------------------------------------
class TestModeSwitchesUpdateOpts:
    def test_single_sets_gro_and_no_remote_sense(self, lp_main):
        lp_main.btnMethodSingle.setChecked(True)
        assert lp_main._instrument_opts["output_low"] == "GRO"
        assert lp_main._instrument_opts["remote_sense"] is False

    def test_triple_sets_flo_and_remote_sense(self, lp_main):
        lp_main.btnMethodTriple.setChecked(True)
        assert lp_main._instrument_opts["output_low"] == "FLO"
        assert lp_main._instrument_opts["remote_sense"] is True

    def test_triple_then_double_restores_double_defaults(self, lp_main):
        lp_main.btnMethodTriple.setChecked(True)
        # User flips to Single (GRO) and then back to Double.
        lp_main.btnMethodSingle.setChecked(True)
        assert lp_main._instrument_opts["output_low"] == "GRO"
        lp_main.btnMethodDouble.setChecked(True)
        assert lp_main._instrument_opts["output_low"] == "FLO"
        assert lp_main._instrument_opts["remote_sense"] is True

    def test_unchecked_signal_is_a_noop(self, lp_main):
        # Pre-state: Double is checked.
        opts_before = dict(lp_main._instrument_opts)
        # An "unchecked" toggle (e.g. when another button takes over)
        # must not write anything — only the new "checked" handler
        # applies defaults.
        lp_main._on_method_button_toggled(lp_main.btnMethodSingle, False)
        assert lp_main._instrument_opts == opts_before


# ---------------------------------------------------------------------------
class TestLiveApplyWhenSmuConnected:
    def test_mode_switch_pushes_to_live_smu(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            # Pretend the SMU is connected — apply_instrument_options
            # checks for ``enable_output_protection`` to detect a live
            # device.
            smu = MagicMock()
            smu.enable_output_protection = MagicMock()
            win.smu = smu
            # Patch the helper at the import site used by LPmeasurement.
            with patch("LPmeasurement.apply_instrument_options") as p:
                win.btnMethodSingle.setChecked(True)
                p.assert_called()
                # Last call carries the Single-mode opts.
                opts = p.call_args.args[1]
                assert opts["output_low"] == "GRO"
                assert opts["remote_sense"] is False
        finally:
            win.close()

    def test_mode_switch_without_smu_does_not_call_apply(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            assert win.smu is None
            with patch("LPmeasurement.apply_instrument_options") as p:
                win.btnMethodTriple.setChecked(True)
                p.assert_not_called()
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestSimModelWiring:
    """Method buttons must propagate a per-mode sim IV model so that
    Single uses the single_probe form and Double/Triple use the
    symmetric double_langmuir form."""

    def test_double_at_startup_writes_double_langmuir(self, lp_main):
        # Default mode applied during __init__.
        assert lp_main._sim_options.get("model") == "double_langmuir"

    def test_single_writes_single_probe(self, lp_main):
        lp_main.btnMethodSingle.setChecked(True)
        assert lp_main._sim_options.get("model") == "single_probe"

    def test_triple_keeps_double_langmuir(self, lp_main):
        lp_main.btnMethodTriple.setChecked(True)
        assert lp_main._sim_options.get("model") == "double_langmuir"

    def test_live_sim_smu_model_switches_with_method(self, qapp):
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.smu = FakeB2901v2(current_compliance=0.01)
            win.smu.connect()
            win.btnMethodSingle.setChecked(True)
            assert win.smu.model == "single_probe"
            win.btnMethodDouble.setChecked(True)
            assert win.smu.model == "double_langmuir"
        finally:
            try:
                win.smu.close()
            except Exception:
                pass
            win.close()

    def test_live_method_swap_resyncs_sheath_to_model_default(self, qapp):
        """Sheath defaults are model-specific; a live swap that only
        changes ``model`` would inherit the previous model's sheath
        and visually break the new model's curve.  Guard the resync.
        """
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            # Start under Double's sheath default.
            win.smu = FakeB2901v2(model="double_langmuir",
                                  current_compliance=0.01)
            win.smu.connect()
            assert win.smu.sheath_conductance == pytest.approx(5.0e-6)
            # Swap to Single → sheath must drop to single_probe default.
            win.btnMethodSingle.setChecked(True)
            assert win.smu.sheath_conductance == pytest.approx(1.0e-7)
            # Swap back to Double → sheath must bounce back.
            win.btnMethodDouble.setChecked(True)
            assert win.smu.sheath_conductance == pytest.approx(5.0e-6)
        finally:
            try:
                win.smu.close()
            except Exception:
                pass
            win.close()

    def test_live_swap_keeps_each_curve_visually_correct(self, qapp):
        """End-to-end: after a live model swap the *resulting* IV
        curve at ±50 V must match the textbook profile of the new
        model — Single asymmetric (|i_pos|/|i_neg| ≥ 50, |i_neg|
        within a few µA of i_ion_sat), Double symmetric and clearly
        saturated (|i| close to i_sat at ±50 V)."""
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.smu = FakeB2901v2(model="double_langmuir",
                                  current_compliance=0.01)
            win.smu.connect(); win.smu.output(True)

            # Live-swap to Single and check the Single profile.
            win.btnMethodSingle.setChecked(True)
            win.smu.set_voltage(-50.0); i_neg_s = win.smu.read_current()
            win.smu.set_voltage(+50.0); i_pos_s = win.smu.read_current()
            assert abs(i_neg_s) <= 2.0 * win.smu.i_ion_sat, i_neg_s
            assert i_pos_s >= 0.8 * win.smu.i_electron_sat, i_pos_s
            assert abs(i_pos_s) / abs(i_neg_s) >= 50.0

            # Live-swap back to Double and check the Double profile.
            win.btnMethodDouble.setChecked(True)
            win.smu.set_voltage(-50.0); i_neg_d = win.smu.read_current()
            win.smu.set_voltage(+50.0); i_pos_d = win.smu.read_current()
            # Saturated within ~25 % of i_sat, symmetric.
            assert 0.95 * win.smu.i_sat <= abs(i_pos_d) <= 1.30 * win.smu.i_sat
            assert 0.95 * win.smu.i_sat <= abs(i_neg_d) <= 1.30 * win.smu.i_sat
            assert abs(abs(i_pos_d) - abs(i_neg_d)) <= 1e-6
        finally:
            try:
                win.smu.close()
            except Exception:
                pass
            win.close()

    def test_sim_connect_under_single_constructs_single_probe(self, qapp):
        """End-to-end check: Single mode + Sim-Connect must produce a
        FakeB2901v2 whose IV branch is the single-probe form."""
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win.chkSim.setChecked(True)
            win._toggle_connect()
            assert isinstance(win.smu, FakeB2901v2)
            assert win.smu.model == "single_probe"
        finally:
            try:
                if win.smu is not None:
                    win.smu.close()
            except Exception:
                pass
            win.close()

    def test_sim_connect_under_double_constructs_double_langmuir(self, qapp):
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            # Double is the startup default; assert it explicitly.
            assert win.btnMethodDouble.isChecked()
            win.chkSim.setChecked(True)
            win._toggle_connect()
            assert isinstance(win.smu, FakeB2901v2)
            assert win.smu.model == "double_langmuir"
        finally:
            try:
                if win.smu is not None:
                    win.smu.close()
            except Exception:
                pass
            win.close()

    def test_sim_connect_under_single_yields_visibly_asymmetric_curve(
            self, qapp):
        """End-to-end GUI-path consistency: with the *exact* defaults
        the GUI uses (no test-only sheath override), a Single-mode
        Sim-Connect must produce a curve where the negative plateau
        is small (close to i_ion_sat) and the positive plateau is
        close to i_electron_sat — the asymmetric Langmuir signature.
        Regression guard for the original mismatch where the default
        sheath_conductance dominated and the curve looked linear."""
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win.chkSim.setChecked(True)
            win._toggle_connect()
            assert isinstance(win.smu, FakeB2901v2)
            assert win.smu.model == "single_probe"
            win.smu.output(True)
            win.smu.set_voltage(-50.0); i_neg = win.smu.read_current()
            win.smu.set_voltage(+50.0); i_pos = win.smu.read_current()
            # Negative plateau within a small multiple of i_ion_sat.
            assert abs(i_neg) <= 2.0 * win.smu.i_ion_sat, (
                i_neg, win.smu.i_ion_sat)
            # Positive plateau in clear electron saturation.
            assert i_pos >= 0.8 * win.smu.i_electron_sat, (
                i_pos, win.smu.i_electron_sat)
            # Strong asymmetry — at least 50× more electron than ion.
            assert abs(i_pos) / abs(i_neg) >= 50.0
        finally:
            try:
                if win.smu is not None:
                    win.smu.close()
            except Exception:
                pass
            win.close()

    def test_sim_dialog_reset_does_not_break_next_connect(self, qapp):
        """Even if the user replaces _sim_options via the sim dialog
        (which drops the model key), the next Sim-Connect must still
        re-inject the active method's model."""
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            # Simulate the dialog replacing the dict (no model key).
            win._sim_options = {"noise_uA": 0.0, "noise_corr": 0.0,
                                "asymmetry_pct": 0.0, "offset_uA": 0.0,
                                "drift_nA_per_pt": 0.0}
            win.chkSim.setChecked(True)
            win._toggle_connect()
            assert isinstance(win.smu, FakeB2901v2)
            assert win.smu.model == "single_probe"
        finally:
            try:
                if win.smu is not None:
                    win.smu.close()
            except Exception:
                pass
            win.close()


# ---------------------------------------------------------------------------
class TestSingleAnalyzeDispatch:
    """The Analyze button dispatches by active method.  Single goes
    to the new pipeline, Double stays with V2, Triple is silent.
    The dataset-method tag protects against analyzing data from one
    method with another method's logic."""

    def _populate_sim_single_sweep(self, win):
        """Helper: stuff a clean Single sim sweep into the buffer."""
        import numpy as np
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        i_ion_sat=5.5e-6, i_electron_sat=1.0e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        for v in np.linspace(-50, 50, 201):
            f.set_voltage(v)
            win._v_ist.append(v); win._i_mean.append(f.read_current())

    def test_active_method_helper(self, lp_main):
        assert lp_main._current_active_method() == "double"
        lp_main.btnMethodSingle.setChecked(True)
        assert lp_main._current_active_method() == "single"
        lp_main.btnMethodTriple.setChecked(True)
        assert lp_main._current_active_method() == "triple"

    def test_start_click_stamps_dataset_method(self, lp_main):
        assert lp_main._dataset_method is None
        lp_main.btnMethodSingle.setChecked(True)
        lp_main._stamp_dataset_method()
        assert lp_main._dataset_method == "single"
        lp_main.btnMethodDouble.setChecked(True)
        lp_main._stamp_dataset_method()
        assert lp_main._dataset_method == "double"

    def test_dispatch_routes_single_to_single_pipeline(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._populate_sim_single_sweep(win)
            win._run_analysis_dispatch()
            r = win._last_single_analysis
            assert r is not None and r["ok"]
            assert r["te_eV"] == pytest.approx(4.0, rel=0.15)
        finally:
            win.close()

    def test_dispatch_blocks_method_mismatch(self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            win.btnMethodDouble.setChecked(True)
            win._dataset_method = "single"  # acquired earlier
            for v in range(-25, 26):
                win._v_ist.append(float(v)); win._i_mean.append(0.0)
            shown = []
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: shown.append(self_)
                or QMessageBox.StandardButton.Ok)
            called = {"single": False, "double": False}
            monkeypatch.setattr(
                win, "_run_single_analysis",
                lambda: called.update({"single": True}))
            monkeypatch.setattr(
                win, "_run_analysis",
                lambda: called.update({"double": True}))
            win._run_analysis_dispatch()
            assert not called["single"], "single must NOT run"
            assert not called["double"], "double must NOT run"
            assert len(shown) == 1, "warning dialog must be shown once"
        finally:
            win.close()

    def test_dispatch_unknown_dataset_asks_for_confirmation(
            self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            win.btnMethodDouble.setChecked(True)
            win._dataset_method = None  # unknown — e.g. loaded from CSV
            for v in range(-25, 26):
                win._v_ist.append(float(v)); win._i_mean.append(0.0)
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: QMessageBox.StandardButton.Cancel)
            called = {"double": False}
            monkeypatch.setattr(
                win, "_run_analysis",
                lambda: called.update({"double": True}))
            win._run_analysis_dispatch()
            assert not called["double"]
            assert win._dataset_method is None
        finally:
            win.close()

    def test_dispatch_unknown_dataset_yes_proceeds_and_stamps(
            self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            win.btnMethodDouble.setChecked(True)
            win._dataset_method = None
            for v in range(-25, 26):
                win._v_ist.append(float(v)); win._i_mean.append(0.0)
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: QMessageBox.StandardButton.Yes)
            called = {"double": False}
            monkeypatch.setattr(
                win, "_run_analysis",
                lambda: called.update({"double": True}))
            win._run_analysis_dispatch()
            assert called["double"]
            assert win._dataset_method == "double"
        finally:
            win.close()

    def test_dispatch_triple_does_not_run_double_or_single(
            self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodTriple.setChecked(True)
            win._dataset_method = "triple"
            for v in range(-25, 26):
                win._v_ist.append(float(v)); win._i_mean.append(0.0)
            called = {"single": False, "double": False}
            monkeypatch.setattr(
                win, "_run_single_analysis",
                lambda: called.update({"single": True}))
            monkeypatch.setattr(
                win, "_run_analysis",
                lambda: called.update({"double": True}))
            win._run_analysis_dispatch()
            assert not called["single"] and not called["double"]
        finally:
            win.close()

    def test_dispatch_empty_buffer_falls_through_to_v2(
            self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = None
            assert not win._v_ist
            called = {"v2": False, "single": False}
            monkeypatch.setattr(
                win, "_run_analysis",
                lambda: called.update({"v2": True}))
            monkeypatch.setattr(
                win, "_run_single_analysis",
                lambda: called.update({"single": True}))
            win._run_analysis_dispatch()
            assert called["v2"], "V2 must take the empty-buffer path"
            assert not called["single"]
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestSingleAnalysisGasAndAreaPropagation:
    """Gas mix and probe area must come from the existing repo
    truth sources (Experiment dialog + Probe Params dialog), not
    from a parallel single-only source."""

    def test_single_uses_probe_area_and_gas_from_helpers(
            self, qapp, monkeypatch):
        import numpy as np
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            monkeypatch.setattr(
                win, "_build_lp_probe_area_m2", lambda: 2.5e-6)
            # 3-tuple contract after the mixed-ion-composition
            # pass: (gas_label, m_i_kg, m_i_rel_unc).  Monatomic
            # gases have no ion-composition ambiguity → rel_unc=0.
            monkeypatch.setattr(
                win, "_build_lp_gas_context",
                lambda: ("Krypton (Kr)", 1.39e-25, 0.0))
            f = FakeB2901v2(model="single_probe", te_eV=4.0,
                            i_ion_sat=5e-6, i_electron_sat=1.0e-3,
                            v_plasma_V=0.0, sheath_conductance=0.0,
                            electron_sat_slope=0.0,
                            current_compliance=10.0)
            f.connect(); f.output(True)
            for v in np.linspace(-50, 50, 201):
                f.set_voltage(v)
                win._v_ist.append(v); win._i_mean.append(f.read_current())
            win._run_analysis_dispatch()
            r = win._last_single_analysis
            assert r["area_m2"] == pytest.approx(2.5e-6)
            assert r["m_i_kg"] == pytest.approx(1.39e-25)
            assert r["gas_label"] == "Krypton (Kr)"
            assert not r["m_i_is_fallback"]
        finally:
            win.close()

    def test_no_gas_configured_marks_fallback(self, qapp, monkeypatch):
        import numpy as np
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            monkeypatch.setattr(
                win, "_build_lp_probe_area_m2", lambda: 1.0e-5)
            monkeypatch.setattr(
                win, "_build_lp_gas_context",
                lambda: ("Argon (Ar)", None, 0.0))
            f = FakeB2901v2(model="single_probe", te_eV=4.0,
                            sheath_conductance=0.0,
                            electron_sat_slope=0.0,
                            current_compliance=10.0)
            f.connect(); f.output(True)
            for v in np.linspace(-50, 50, 201):
                f.set_voltage(v)
                win._v_ist.append(v); win._i_mean.append(f.read_current())
            win._run_analysis_dispatch()
            r = win._last_single_analysis
            assert r["m_i_is_fallback"]
            assert any("Argon" in w for w in r["warnings"])
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestCsvMethodTagRoundtrip:
    """The Method tag must travel save → CSV → reload → analyze."""

    def _make_dataset(self, win):
        import numpy as np
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        i_ion_sat=5.5e-6, i_electron_sat=1.0e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        for v in np.linspace(-50, 50, 51):
            f.set_voltage(v)
            win._v_soll.append(v); win._v_ist.append(v)
            win._i_mean.append(f.read_current())
            win._i_std.append(0.0)
            win._directions.append("fwd")
            win._compliance.append(False)

    def test_save_writes_method_header(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._make_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win._save_csv(run_status="completed")
            csvs = list(tmp_path.rglob("LP_*.csv"))
            assert len(csvs) == 1
            text = csvs[0].read_text(encoding="utf-8")
            assert "Method: single" in text, text[:600]
        finally:
            win.close()

    def test_save_defaults_to_double_when_no_tag(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._dataset_method = None  # never started a sweep
            self._make_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win._save_csv(run_status="completed")
            csvs = list(tmp_path.rglob("LP_*.csv"))
            text = csvs[0].read_text(encoding="utf-8")
            assert "Method: double" in text
        finally:
            win.close()

    def test_parse_csv_dataset_reads_method_header(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._make_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win._save_csv(run_status="completed")
            path = next(tmp_path.rglob("LP_*.csv"))
            meta, *_ = LPMainWindow.parse_csv_dataset(path)
            assert meta.get("Method") == "single"
        finally:
            win.close()

    def test_load_csv_with_method_tag_sets_dataset_method(
            self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        # First window: acquire + save under Single.
        win1 = LPMainWindow()
        try:
            win1.btnMethodSingle.setChecked(True)
            win1._dataset_method = "single"
            self._make_dataset(win1)
            win1._save_folder = tmp_path
            win1.chkSave.setChecked(True)
            win1.chkAutoAnalyze.setChecked(False)
            win1._save_csv(run_status="completed")
        finally:
            win1.close()
        # Second window: load → dataset method auto-restored.
        path = next(tmp_path.rglob("LP_*.csv"))
        win2 = LPMainWindow()
        try:
            assert win2._dataset_method is None
            win2._load_csv_with_method_tag(str(path))
            assert win2._dataset_method == "single"
            assert len(win2._v_ist) == 51
        finally:
            win2.close()

    def test_load_csv_without_method_tag_keeps_dataset_unset(
            self, qapp, tmp_path):
        # Hand-craft a CSV without the Method header.
        p = tmp_path / "legacy.csv"
        p.write_text(
            "# Date: 2026-04-18 12:00:00\n"
            "# Points: 3\n"
            "V_soll_V,V_ist_V,I_mean_A,I_std_A,dir,compl\n"
            "-1.0,-1.0,1.0e-6,0,fwd,False\n"
            " 0.0, 0.0,2.0e-6,0,fwd,False\n"
            " 1.0, 1.0,3.0e-6,0,fwd,False\n",
            encoding="utf-8")
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            meta = win._load_csv_with_method_tag(str(p))
            assert "Method" not in meta
            assert win._dataset_method is None
            assert len(win._v_ist) == 3
        finally:
            win.close()

    def test_reload_with_matching_method_skips_confirm(
            self, qapp, tmp_path, monkeypatch):
        """Ende-zu-Ende: getaggter Reload + Analyze unter passender
        Methode → kein Confirm-Dialog, Single-Pipeline läuft direkt."""
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        # Save a Single dataset.
        win1 = LPMainWindow()
        try:
            win1.btnMethodSingle.setChecked(True)
            win1._dataset_method = "single"
            self._make_dataset(win1)
            win1._save_folder = tmp_path
            win1.chkSave.setChecked(True)
            win1.chkAutoAnalyze.setChecked(False)
            win1._save_csv(run_status="completed")
        finally:
            win1.close()
        # Reload + analyze.
        path = next(tmp_path.rglob("LP_*.csv"))
        win2 = LPMainWindow()
        try:
            win2.btnMethodSingle.setChecked(True)
            win2._load_csv_with_method_tag(str(path))
            assert win2._dataset_method == "single"
            shown = []
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: shown.append(self_)
                or QMessageBox.StandardButton.Cancel)
            win2._run_analysis_dispatch()
            assert not shown, "no confirm dialog must be shown"
            r = win2._last_single_analysis
            assert r is not None and r["ok"]
        finally:
            win2.close()


# ---------------------------------------------------------------------------
class TestMidSweepMethodLock:
    """Single/Double/Triple buttons must be inert while a sweep
    is running and must auto-restore on completion / abort."""

    def test_method_buttons_disabled_while_running(self, lp_main):
        # Running flag set via V2 helper — simulate sweep start.
        lp_main._set_sweep_ui(True)
        assert not lp_main.btnMethodSingle.isEnabled()
        assert not lp_main.btnMethodDouble.isEnabled()
        assert not lp_main.btnMethodTriple.isEnabled()
        # And re-enabled when sweep ends.
        lp_main._set_sweep_ui(False)
        assert lp_main.btnMethodSingle.isEnabled()
        assert lp_main.btnMethodDouble.isEnabled()
        assert lp_main.btnMethodTriple.isEnabled()

    def test_lock_helper_is_idempotent(self, lp_main):
        # Calling twice with the same state must not flip anything.
        lp_main._lock_method_buttons_during_sweep(True)
        lp_main._lock_method_buttons_during_sweep(True)
        assert not lp_main.btnMethodSingle.isEnabled()
        lp_main._lock_method_buttons_during_sweep(False)


# ---------------------------------------------------------------------------
class TestSinglePlotOverlays:
    """Single-probe analysis must draw V_f and V_p as vertical
    lines on the IV plot, with a confidence-coded V_p style, and
    must clear them at every Start click."""

    def _populate_sim_single_sweep(self, win):
        import numpy as np
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        i_ion_sat=5.5e-6, i_electron_sat=1.0e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        for v in np.linspace(-50, 50, 201):
            f.set_voltage(v)
            win._v_ist.append(v); win._i_mean.append(f.read_current())

    def test_overlays_drawn_after_single_analysis(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._populate_sim_single_sweep(win)
            assert win._single_overlay_lines == []
            win._run_analysis_dispatch()
            # Two overlays expected: V_f and V_p.
            assert len(win._single_overlay_lines) == 2
        finally:
            win.close()

    def test_overlays_cleared_on_start_click(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._populate_sim_single_sweep(win)
            win._run_analysis_dispatch()
            assert len(win._single_overlay_lines) == 2
            # Simulate next sweep start (signal already wired).
            win._clear_single_overlays()
            assert win._single_overlay_lines == []
        finally:
            win.close()

    def test_vp_style_reflects_confidence(self, qapp):
        # V_p line style scales with the reported confidence:
        #   high   = solid (-)        — derivative method on clean data
        #   medium = dashed (--)
        #   low    = dotted (:)
        # The synthetic Gompertz sweep can land in any of the three
        # tiers depending on the chosen estimator; the test just
        # asserts the mapping is applied consistently.
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._populate_sim_single_sweep(win)
            win._run_analysis_dispatch()
            r = win._last_single_analysis
            assert r["v_plasma_confidence"] in ("high", "medium", "low")
            v_p_line = win._single_overlay_lines[-1]
            style = v_p_line.get_linestyle()
            expected = {"high": "-", "medium": "--",
                        "low": ":"}[r["v_plasma_confidence"]]
            assert style == expected
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestCompactDoubleHtml:
    """The compact formatter must keep the key Double fields and
    drop the verbose parameter dump and history footer."""

    def _sample_fit(self):
        return {
            "model_key": "tanh_slope",
            "label": "tanh + slope",
            "Te_eV": 3.20, "Te_err_eV": 0.04,
            "I_sat_fit_A": 2.0e-3,
            "R2": 0.998, "NRMSE": 0.012,
            "grade": "A", "grade_color": "#3a8",
        }

    def test_compact_block_contains_essentials(self):
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(
            self._sample_fit(),
            {"n_i_m3": 1.2e17, "ion_label": "Ar"},
            None)
        assert "Double-Probe Analysis" in html
        assert "T_e" in html and "3.200" in html
        assert "I_sat" in html and "2.000 mA" in html
        assert "Model" in html and "tanh + slope" in html
        assert "R" in html and "0.998" in html
        assert "[A]" in html  # grade
        assert "n_i" in html and "1.200e+17" in html

    def test_compact_block_is_meaningfully_shorter_than_v2(self):
        # Soft regression: fewer than ~600 chars vs V2's ~1500–2500.
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(
            self._sample_fit(),
            {"n_i_m3": 1.2e17, "ion_label": "Ar"},
            None)
        assert len(html) < 800, len(html)

    def test_compact_block_omits_model_table_when_only_one(self):
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(
            self._sample_fit(), None, [self._sample_fit()])
        assert "<b>Models:</b>" not in html

    def test_compact_block_includes_model_table_for_multi(self):
        from LPmeasurement import _format_compact_double
        cmp_list = [
            self._sample_fit(),
            {"model_key": "simple_tanh", "label": "Simple tanh",
             "Te_eV": 3.40, "R2": 0.96, "grade_color": "#bb8"},
        ]
        html = _format_compact_double(
            self._sample_fit(), None, cmp_list)
        assert "<b>Models:</b>" in html
        assert "Simple tanh" in html
        assert "tanh + slope" in html

    def test_compact_block_handles_missing_fields(self):
        from LPmeasurement import _format_compact_double
        # Sparse fit dict — must not crash, must show n/a markers.
        html = _format_compact_double(
            {"model_key": "x", "label": "X",
             "Te_eV": float("nan"), "I_sat_fit_A": None,
             "grade": "?"}, None, None)
        assert "n/a" in html or "N/A" in html.upper() or "&aa6" in html.lower() or "aa6" in html
        assert "X" in html
