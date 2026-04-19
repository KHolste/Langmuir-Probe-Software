"""End-to-end tests for the Single-probe T_e bootstrap CI feature.

Covers the wiring chain:

    SingleAnalysisOptions.bootstrap_enabled
        → LPMainWindow._run_single_analysis
            → analyze_single_iv (kwargs)
                → bootstrap_te_ci (math)
                    → result["te_ci_eV"] / ["te_ci_method"]
                        → format_single_result_html (visible row)
                        → LP append_log (visible CI text)

These are intentionally focused on the new feature only.  The
underlying bootstrap math is already covered by
``tests/test_single_analysis_hardening.py::TestBootstrapCi``.
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _gompertz_iv(v_min=-50.0, v_max=50.0, n=201, te=4.0,
                  i_ion=5.5e-6, i_e=1.0e-3, v_p=0.0):
    V = np.linspace(v_min, v_max, n)
    arg = np.clip((V - v_p) / te, -50.0, 50.0)
    factor = 1.0 - np.exp(-np.exp(arg))
    I = -i_ion + i_e * factor
    return V, I


# ---------------------------------------------------------------------------
class TestPipelineIntegration:
    def test_disabled_bootstrap_keeps_legacy_result_shape(self):
        """Default path must remain bit-for-bit close: te_ci_eV is
        None, te_ci_method is the explicit string ``"disabled"``."""
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _gompertz_iv()
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        assert r["ok"]
        assert r["te_ci_eV"] is None
        assert r["te_ci_method"] == "disabled"
        assert r["te_ci_n_iters"] == 0
        # Legacy te_eV not perturbed by the new code path.
        assert r["te_eV"] == pytest.approx(4.0, rel=0.10)

    def test_enabled_bootstrap_computes_ci_bracketing_te(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _gompertz_iv(te=4.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              bootstrap_enabled=True,
                              bootstrap_n_iters=200,
                              bootstrap_seed=7)
        assert r["ok"]
        assert r["te_ci_method"] == "bootstrap"
        assert r["te_ci_n_iters"] == 200
        ci = r["te_ci_eV"]
        assert ci is not None and len(ci) == 2
        lo, hi = ci
        assert lo < hi
        # CI must bracket the point estimate (this is the
        # well-defined invariant; the absolute value of T_e_point
        # depends on the Gompertz model's small semilog bias).
        assert lo <= r["te_eV"] <= hi
        # Plausibility band around the textbook 4 eV.
        assert 3.0 <= lo and hi <= 5.0

    def test_insufficient_data_degrades_gracefully(self):
        """When bootstrap is requested but the underlying helper
        returns ``(None, None)`` (e.g. too few points), surface the
        attempt explicitly via ``te_ci_method='unavailable'`` rather
        than silently dropping the CI."""
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        # 12 points just enough to clear analyze_single_iv's len < 10
        # gate but well below the bootstrap helper's >= 6 valid-points
        # threshold inside its own retarding window.
        V = np.linspace(-50, 50, 12)
        I = -5e-6 + 1e-3 * (1 - np.exp(-np.exp(V / 4.0)))
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              bootstrap_enabled=True,
                              bootstrap_n_iters=50)
        # The pipeline may or may not converge on Te with 12 points;
        # what matters is that the bootstrap field is honest:
        # either "bootstrap" with a real CI, or "unavailable" with
        # the corresponding warning — never a silent "disabled".
        assert r["te_ci_method"] in ("bootstrap", "unavailable")
        if r["te_ci_method"] == "unavailable":
            assert r["te_ci_eV"] is None
            assert any("bootstrap" in w.lower() for w in r["warnings"])

    def test_enabled_with_te_fit_failure_is_unavailable(self):
        """Te fit fails → bootstrap is structurally impossible →
        method must be 'unavailable', not 'disabled'."""
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        # Flat noise has no V_f / no Te slope → te_eV stays None.
        V = np.linspace(-50, 50, 50)
        I = np.zeros_like(V) + 1e-9
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              bootstrap_enabled=True)
        assert r["te_eV"] is None
        assert r["te_ci_method"] == "unavailable"
        assert r["te_ci_eV"] is None


# ---------------------------------------------------------------------------
class TestVisibleHtmlBlock:
    """The CI must be visible in the user-facing HTML when computed,
    and must not appear at all on the disabled-default path."""

    def test_html_contains_ci_row_when_bootstrap_succeeds(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html, M_AR_KG)
        V, I = _gompertz_iv(te=4.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              bootstrap_enabled=True,
                              bootstrap_n_iters=150)
        html = format_single_result_html(r)
        assert "T_e CI" in html
        assert "95% bootstrap" in html
        # Bracket numbers must show up in the row.
        lo, hi = r["te_ci_eV"]
        assert f"{lo:.3f}" in html and f"{hi:.3f}" in html

    def test_html_omits_ci_row_when_disabled(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html, M_AR_KG)
        V, I = _gompertz_iv()
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        html = format_single_result_html(r)
        # No CI row at all on the default path — avoids surfacing a
        # confusing "n/a" line for operators who never asked for one.
        assert "T_e CI" not in html

    def test_html_shows_unavailable_label_when_degraded(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html, M_AR_KG)
        V = np.linspace(-50, 50, 50)
        I = np.zeros_like(V) + 1e-9
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              bootstrap_enabled=True)
        html = format_single_result_html(r)
        assert "T_e CI" in html
        assert "n/a" in html
        assert "bootstrap" in html.lower()


# ---------------------------------------------------------------------------
class TestLPWiring:
    """LP._run_single_analysis must forward bootstrap kwargs and
    surface the CI in the operator log line."""

    def test_lp_passes_bootstrap_kwargs_to_pipeline(self, qapp,
                                                     monkeypatch):
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            f = FakeB2901v2(model="single_probe",
                            current_compliance=10.0,
                            sheath_conductance=0.0,
                            electron_sat_slope=0.0)
            f.connect(); f.output(True)
            for v in np.linspace(-50, 50, 100):
                f.set_voltage(v)
                win._v_ist.append(v)
                win._i_mean.append(f.read_current())
                win._compliance.append(False)
                win._directions.append("fwd")
            win._single_analysis_options = SingleAnalysisOptions(
                bootstrap_enabled=True,
                bootstrap_n_iters=123)
            win._dataset_method = "single"

            captured = {}
            from dlp_single_analysis import analyze_single_iv as _orig

            def _spy(*a, **k):
                captured.update(k)
                return _orig(*a, **k)

            monkeypatch.setattr(
                "dlp_single_analysis.analyze_single_iv", _spy)
            win._run_single_analysis()
            assert captured.get("bootstrap_enabled") is True
            assert captured.get("bootstrap_n_iters") == 123
        finally:
            win.close()

    def test_lp_log_line_contains_ci_when_enabled(self, qapp):
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        win = LPMainWindow()
        try:
            V, I = _gompertz_iv(te=4.0)
            win._v_ist = list(V); win._i_mean = list(I)
            win._compliance = [False] * len(V)
            win._directions = ["fwd"] * len(V)
            win._single_analysis_options = SingleAnalysisOptions(
                bootstrap_enabled=True, bootstrap_n_iters=200)
            win._dataset_method = "single"
            win.txtLog.clear()
            win._run_single_analysis()
            log_text = win.txtLog.toPlainText()
            assert "95%CI" in log_text or "T_e CI" in log_text
        finally:
            win.close()

    def test_lp_log_line_has_no_ci_when_disabled(self, qapp):
        """Default (bootstrap off) → log line stays in the legacy
        shape, no CI clause appended."""
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        win = LPMainWindow()
        try:
            V, I = _gompertz_iv(te=4.0)
            win._v_ist = list(V); win._i_mean = list(I)
            win._compliance = [False] * len(V)
            win._directions = ["fwd"] * len(V)
            win._single_analysis_options = SingleAnalysisOptions(
                bootstrap_enabled=False)
            win._dataset_method = "single"
            win.txtLog.clear()
            win._run_single_analysis()
            log_text = win.txtLog.toPlainText()
            assert "95%CI" not in log_text
            assert "CI=n/a" not in log_text
        finally:
            win.close()
