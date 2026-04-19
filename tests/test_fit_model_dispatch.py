"""Tests for the mode-aware Fit Model… dispatch and the new
Single-probe analysis options dialog/dataclass."""
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


# ---------------------------------------------------------------------------
class TestSingleAnalysisOptionsDataclass:
    def test_defaults_match_shipping_behavior(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions()
        assert o.te_window_factor == 3.0
        assert o.robust_te_fit is True
        assert o.compliance_mode == "exclude_clipped"
        assert o.hysteresis_threshold_pct == 5.0
        assert o.bootstrap_enabled is False

    def test_to_dict_round_trip(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions(te_window_factor=2.0,
                                    robust_te_fit=False,
                                    compliance_mode="include_all",
                                    hysteresis_threshold_pct=10.0,
                                    bootstrap_enabled=True,
                                    bootstrap_n_iters=500)
        d = o.to_dict()
        o2 = SingleAnalysisOptions.from_dict(d)
        assert o2 == o

    def test_from_dict_validates_invalid_values(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions.from_dict({
            "te_window_factor": 99.0,            # invalid → 3.0
            "compliance_mode": "garbage",         # invalid → exclude_clipped
            "hysteresis_threshold_pct": "abc",   # invalid → 5.0
            "bootstrap_n_iters": "xyz",          # invalid → 200
        })
        assert o.te_window_factor == 3.0
        assert o.compliance_mode == "exclude_clipped"
        assert o.hysteresis_threshold_pct == 5.0
        assert o.bootstrap_n_iters == 200

    def test_from_none_returns_defaults(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions.from_dict(None)
        assert o == SingleAnalysisOptions()


# ---------------------------------------------------------------------------
class TestFitModelDispatch:
    """After the convergence pass, Double routes to the combined
    Double-options dialog (model selector + Double-only knobs in one
    place); Single routes to the Single-options dialog; Triple shows
    an info-only message box."""

    def test_double_mode_opens_double_options_dialog(self, qapp,
                                                      monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            assert win.btnMethodDouble.isChecked()  # default
            called = {"double": False, "single": False}
            # Patch the symbol on the module the dispatcher imports
            # from — local-import inside the slot, so monkeypatching
            # the source module is the right hook.
            import dlp_double_options as _ddo
            monkeypatch.setattr(
                _ddo, "open_double_options_dialog",
                lambda *a, **k: called.update({"double": True}) or None)
            monkeypatch.setattr(
                win, "_open_single_analysis_options_dialog",
                lambda: called.update({"single": True}))
            win._open_fit_model_dispatch()
            assert called["double"] and not called["single"]
        finally:
            win.close()

    def test_single_mode_opens_single_options_dialog(self, qapp,
                                                       monkeypatch):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            called = {"double": False, "single": False}
            import dlp_double_options as _ddo
            monkeypatch.setattr(
                _ddo, "open_double_options_dialog",
                lambda *a, **k: called.update({"double": True}) or None)
            monkeypatch.setattr(
                win, "_open_single_analysis_options_dialog",
                lambda: called.update({"single": True}))
            win._open_fit_model_dispatch()
            assert called["single"] and not called["double"]
        finally:
            win.close()

    def test_triple_mode_shows_info_dialog(self, qapp, monkeypatch):
        from LPmeasurement import LPMainWindow
        from PySide6.QtWidgets import QMessageBox
        win = LPMainWindow()
        try:
            win.btnMethodTriple.setChecked(True)
            shown_texts: list[str] = []
            monkeypatch.setattr(
                QMessageBox, "exec",
                lambda self_: shown_texts.append(self_.text())
                or QMessageBox.StandardButton.Ok)
            called = {"double": False, "single": False}
            import dlp_double_options as _ddo
            monkeypatch.setattr(
                _ddo, "open_double_options_dialog",
                lambda *a, **k: called.update({"double": True}) or None)
            monkeypatch.setattr(
                win, "_open_single_analysis_options_dialog",
                lambda: called.update({"single": True}))
            win._open_fit_model_dispatch()
            assert not called["double"] and not called["single"]
            assert any("closed-form" in t.lower() or
                       "no swept fit" in t.lower()
                       for t in shown_texts)
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestComplianceModeGate:
    """Operator's choice of "include_all" must DISABLE the
    compliance-filter buffer swap on the Double path AND the
    compliance-kwarg pass-through on the Single path."""

    def test_double_include_all_disables_buffer_swap(self, qapp):
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        import numpy as _np
        win = LPMainWindow()
        try:
            f = FakeB2901v2(model="double_langmuir",
                            current_compliance=10.0)
            f.connect(); f.output(True)
            for v in _np.linspace(-50, 50, 80):
                f.set_voltage(v)
                win._v_soll.append(v); win._v_ist.append(v)
                win._i_mean.append(f.read_current())
                win._i_std.append(0.0)
                win._directions.append("fwd")
                win._compliance.append(False)
            for i in range(70, 80):
                win._compliance[i] = True
            # Operator opts out of filtering — Double-path knob now
            # owns its own copy after the per-method ownership split,
            # so this no longer touches Single's options.
            from dlp_double_options import DoubleAnalysisOptions
            win._double_analysis_options = DoubleAnalysisOptions(
                compliance_mode="include_all")
            win._dataset_method = "double"
            n_before = len(win._v_ist)
            win.txtLog.clear()
            win._run_analysis()
            assert len(win._v_ist) == n_before
            log_text = win.txtLog.toPlainText()
            # Must NOT log the exclusion message.
            assert "excluded" not in log_text.lower()
        finally:
            win.close()

    def test_single_include_all_passes_no_compliance(self, qapp,
                                                       monkeypatch):
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        import numpy as _np
        win = LPMainWindow()
        try:
            f = FakeB2901v2(model="single_probe",
                            current_compliance=10.0,
                            sheath_conductance=0.0,
                            electron_sat_slope=0.0)
            f.connect(); f.output(True)
            for v in _np.linspace(-50, 50, 100):
                f.set_voltage(v)
                win._v_ist.append(v)
                win._i_mean.append(f.read_current())
                win._compliance.append(False)
                win._directions.append("fwd")
            win._compliance[80] = True  # synthetic clipped flag
            from dlp_single_options import SingleAnalysisOptions
            win._single_analysis_options = SingleAnalysisOptions(
                compliance_mode="include_all")
            win._dataset_method = "single"
            captured_kwargs = {}
            from dlp_single_analysis import analyze_single_iv as _orig
            def _spy(*a, **k):
                captured_kwargs.update(k)
                return _orig(*a, **k)
            monkeypatch.setattr(
                "dlp_single_analysis.analyze_single_iv", _spy)
            win._run_single_analysis()
            assert captured_kwargs.get("compliance") is None
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestOptionsAffectAnalysis:
    def test_te_window_factor_changes_fit_window(self, qapp):
        # Different window factors must produce different reported
        # fit windows (not necessarily different Te values, but the
        # internal window must scale).
        from dlp_single_analysis import (
            analyze_single_iv, M_AR_KG)
        import numpy as _np
        V = _np.linspace(-50, 50, 201)
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        I = _np.array([(f.set_voltage(v) or f.read_current())
                        for v in V])
        r2 = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                                te_window_factor=2.0)
        r5 = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                                te_window_factor=5.0)
        win2 = r2["fit_window_te_V"]
        win5 = r5["fit_window_te_V"]
        # Wider factor → wider window.
        assert (win5[1] - win5[0]) > (win2[1] - win2[0])

    def test_hysteresis_threshold_drives_warning(self):
        from dlp_single_analysis import detect_hysteresis
        import numpy as _np
        V = _np.linspace(-10, 10, 50)
        I_fwd = _np.linspace(-1e-3, 1e-3, 50)
        I_rev = I_fwd + 5e-5  # ~3% of |I|_max
        V_all = _np.concatenate([V, V])
        I_all = _np.concatenate([I_fwd, I_rev])
        dirs = ["fwd"] * 50 + ["rev"] * 50
        # Lax threshold: not flagged.
        h_lax = detect_hysteresis(V_all, I_all, dirs,
                                   threshold_pct=10.0)
        assert not h_lax["flagged"]
        # Strict threshold: flagged.
        h_strict = detect_hysteresis(V_all, I_all, dirs,
                                      threshold_pct=1.0)
        assert h_strict["flagged"]


# ---------------------------------------------------------------------------
class TestPersistenceRoundTrip:
    def test_options_serialised_in_get_config(self, qapp):
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        win = LPMainWindow()
        try:
            win._single_analysis_options = SingleAnalysisOptions(
                te_window_factor=5.0, robust_te_fit=False,
                compliance_mode="include_all",
                hysteresis_threshold_pct=7.5,
                bootstrap_enabled=True, bootstrap_n_iters=300)
            cfg = win.get_config()
            assert "single_analysis_options" in cfg
            assert cfg["single_analysis_options"]["te_window_factor"] == 5.0
            assert cfg["single_analysis_options"]["compliance_mode"] == "include_all"
        finally:
            win.close()

    def test_options_restored_via_apply_config(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.apply_config({"single_analysis_options": {
                "te_window_factor": 2.0,
                "robust_te_fit": False,
                "compliance_mode": "include_all",
                "hysteresis_threshold_pct": 8.0,
                "bootstrap_enabled": True,
                "bootstrap_n_iters": 400}})
            o = win._single_analysis_options
            assert o.te_window_factor == 2.0
            assert o.robust_te_fit is False
            assert o.compliance_mode == "include_all"
            assert o.bootstrap_n_iters == 400
        finally:
            win.close()
