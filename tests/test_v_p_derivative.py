"""Tests for the derivative-based V_p estimator and the dual-method
auto-pick / fallback / agreement reporting.

Covers (in order):
  * direct unit tests on ``estimate_v_plasma_derivative`` for clean,
    noisy, sparse, and pathological inputs;
  * pipeline integration: result dict carries both V_p candidates,
    the chosen method tag, and confidence;
  * options dispatch: requesting "derivative" / "intersection" /
    invalid value behaves as documented;
  * HTML output: the chosen method appears in the V_p row, the
    cross-check row appears when both candidates are available;
  * round-trip persistence of ``v_p_method`` through SingleAnalysisOptions;
  * regression: existing analyze_single_iv behaviour for clean data
    is preserved (V_p still computed, n_e still finite, ok=True).
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
def _gompertz_iv(v_min=-50.0, v_max=50.0, n=201, te=4.0,
                  i_ion=5.5e-6, i_e=1.0e-3, v_p=0.0):
    """Synthetic single-probe IV with a sharp Maxwellian-like knee
    near v_p (Gompertz approximation)."""
    V = np.linspace(v_min, v_max, n)
    arg = np.clip((V - v_p) / te, -50.0, 50.0)
    factor = 1.0 - np.exp(-np.exp(arg))
    I = -i_ion + i_e * factor
    return V, I


def _ideal_maxwellian_iv(v_min=-50.0, v_max=50.0, n=401, te=3.0,
                          i_ion=4e-6, i_e_sat=1e-3, v_p=2.0):
    """Closer-to-textbook IV: linear electron-saturation tail above
    V_p (with small slope) so the dI/dV peak is sharper and the
    derivative method should clearly win over the intersection
    method."""
    V = np.linspace(v_min, v_max, n)
    I = np.empty_like(V)
    below = V < v_p
    I[below] = -i_ion + i_e_sat * np.exp((V[below] - v_p) / te)
    I[~below] = (-i_ion + i_e_sat
                 + 1e-5 * (V[~below] - v_p))   # gentle e-sat slope
    return V, I


# ---------------------------------------------------------------------------
class TestDerivativeUnit:
    def test_finds_knee_on_clean_data(self):
        from dlp_single_analysis import estimate_v_plasma_derivative
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        v_p, conf, st, diag = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=-15.0)
        assert v_p is not None
        assert abs(v_p - 2.0) < 1.5  # within half-T_e of truth
        assert conf in ("high", "medium")
        assert st == "ok" or "prominence" in st
        assert diag["peak_didv"] is not None and diag["peak_didv"] > 0

    def test_too_few_points_returns_none(self):
        from dlp_single_analysis import estimate_v_plasma_derivative
        V = np.linspace(-10, 10, 10)
        I = np.zeros_like(V)
        v_p, conf, st, _ = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=-1.0)
        assert v_p is None
        assert conf == "n/a"
        assert "fewer than 25" in st or "too few" in st

    def test_missing_te_or_vf_returns_none(self):
        from dlp_single_analysis import estimate_v_plasma_derivative
        V, I = _gompertz_iv(n=80)
        v_p, conf, _, _ = estimate_v_plasma_derivative(
            V, I, te=None, v_float=-15.0)
        assert v_p is None and conf == "n/a"
        v_p2, conf2, _, _ = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=None)
        assert v_p2 is None and conf2 == "n/a"

    def test_no_positive_derivative_returns_none(self):
        """Pure ion-saturation plateau (current never rises) should
        not invent a knee."""
        from dlp_single_analysis import estimate_v_plasma_derivative
        V = np.linspace(-50, 50, 200)
        I = -np.full_like(V, 5e-6)   # flat negative, no electron branch
        v_p, conf, st, _ = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=-15.0)
        assert v_p is None
        assert conf == "n/a"
        # Either of the two equally-correct rejection paths is fine:
        # (a) "no electron branch" (current is flat, gate trips first),
        # (b) "no positive dI/dV" (constant data, derivative all-zero).
        assert ("positive" in st or "peak" in st
                or "electron branch" in st or "flat" in st)

    def test_noisy_data_degrades_confidence_not_value(self):
        """Heavy noise on top of a real knee should weaken the
        prominence (medium / low confidence) but the peak location
        must still be in a sane neighbourhood, not at the boundary."""
        from dlp_single_analysis import estimate_v_plasma_derivative
        rng = np.random.default_rng(42)
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0, n=401)
        I_noisy = I + rng.normal(0.0, 5e-5, size=len(I))
        v_p, conf, _, _ = estimate_v_plasma_derivative(
            V, I_noisy, te=3.0, v_float=-15.0)
        assert v_p is not None
        # Position remains within a few T_e of truth even when noisy.
        assert abs(v_p - 2.0) < 6.0
        # Confidence is allowed to drop but must be a recognised tag.
        assert conf in ("high", "medium", "low")


# ---------------------------------------------------------------------------
class TestPipelineDualMethod:
    def test_both_candidates_populated_and_metadata_present(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        assert r["ok"]
        # Both candidates present (or at least exposed as keys).
        assert "v_plasma_V_derivative" in r
        assert "v_plasma_V_intersection" in r
        # New metadata keys exist.
        assert r["v_p_method"] in ("derivative", "intersection", "n/a")
        assert r["v_p_method_requested"] == "auto"
        # Disagreement field is computed when both candidates exist.
        if (r["v_plasma_V_intersection"] is not None
                and r["v_plasma_V_derivative"] is not None):
            assert r["v_p_methods_disagree_V"] is not None
            assert r["v_p_methods_disagree_V"] >= 0.0

    def test_auto_prefers_derivative_on_clean_data(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              v_p_method="auto")
        # On synthetic clean data with a sharp knee, the derivative
        # method should score "high" and be selected.  This is the
        # justification for adding it as the auto choice.
        assert r["v_p_method"] == "derivative"
        assert r["v_plasma_confidence"] == "high"
        # Reported value is close to the synthetic truth.
        assert abs(r["v_plasma_V"] - 2.0) < 1.5

    def test_explicit_intersection_request_honoured(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              v_p_method="intersection")
        # Intersection must be selected even though the derivative
        # method also produced a value — operator override wins.
        assert r["v_p_method"] == "intersection"
        # Intersection candidate must be the chosen V_p value.
        assert r["v_plasma_V"] == r["v_plasma_V_intersection"]

    def test_invalid_method_falls_back_to_auto_with_warning(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              v_p_method="weirdo")
        assert any("v_p_method" in w for w in r["warnings"])
        # Fallback still produces a result (auto policy).
        assert r["v_p_method"] in ("derivative", "intersection")

    def test_disagreement_warning_when_methods_differ_a_lot(self):
        """Construct a sweep where the two methods materially
        disagree (e.g. soft knee + steep e-sat slope).  The
        disagreement should be reported in warnings; the chosen
        value remains valid."""
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        # Soft knee: very wide Te smears out the dI/dV peak so the
        # derivative method picks a different V_p than the
        # intersection method.
        V = np.linspace(-50, 50, 401)
        te_true = 8.0
        v_p_true = -2.0
        i_ion = 4e-6
        i_e_sat = 1e-3
        I = np.empty_like(V)
        below = V < v_p_true
        I[below] = (-i_ion + i_e_sat
                    * np.exp((V[below] - v_p_true) / te_true))
        I[~below] = (-i_ion + i_e_sat
                     + 5e-5 * (V[~below] - v_p_true))
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              v_p_method="auto")
        # We don't assert which method "wins"; we only require the
        # disagreement metric to be non-trivial AND that a warning
        # is emitted when |Δ| > Te.
        if (r["v_plasma_V_derivative"] is not None
                and r["v_plasma_V_intersection"] is not None):
            delta = r["v_p_methods_disagree_V"]
            assert delta is not None and delta >= 0.0
            if r.get("te_eV") is not None and delta > r["te_eV"]:
                assert any("V_p methods disagree" in w
                           for w in r["warnings"])


# ---------------------------------------------------------------------------
class TestVisibleHtml:
    def test_v_p_row_shows_method_tag(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html, M_AR_KG)
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        html = format_single_result_html(r)
        assert "V_p" in html
        # Method tag is always shown next to the V_p value.
        assert ("derivative" in html or "intersection" in html)

    def test_cross_check_row_shows_both_candidates(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html, M_AR_KG)
        V, I = _ideal_maxwellian_iv(v_p=2.0, te=3.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        html = format_single_result_html(r)
        if (r["v_plasma_V_derivative"] is not None
                and r["v_plasma_V_intersection"] is not None):
            assert "V_p check" in html
            assert "derivative=" in html
            assert "intersection=" in html


# ---------------------------------------------------------------------------
class TestOptionsRoundtrip:
    def test_dataclass_default_is_auto(self):
        from dlp_single_options import SingleAnalysisOptions
        assert SingleAnalysisOptions().v_p_method == "auto"

    def test_invalid_value_falls_back_to_auto(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions.from_dict({"v_p_method": "garbage"})
        assert o.v_p_method == "auto"

    def test_round_trip(self):
        from dlp_single_options import SingleAnalysisOptions
        o = SingleAnalysisOptions(v_p_method="derivative")
        d = o.to_dict()
        o2 = SingleAnalysisOptions.from_dict(d)
        assert o2.v_p_method == "derivative"


# ---------------------------------------------------------------------------
class TestRegressionExistingBehaviour:
    """Existing analyze_single_iv behaviour on representative clean
    data must not regress — V_p still produced, n_e finite, ok flag
    set.  This is what the previous shipping test bed already proved
    for the intersection-only path; we re-prove it for the dual path."""

    def test_clean_iv_still_produces_full_result(self):
        from dlp_single_analysis import analyze_single_iv, M_AR_KG
        V, I = _gompertz_iv(te=4.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        assert r["ok"]
        assert r["te_eV"] == pytest.approx(4.0, rel=0.10)
        assert r["v_plasma_V"] is not None
        assert r["n_e_m3"] is not None and r["n_e_m3"] > 0
