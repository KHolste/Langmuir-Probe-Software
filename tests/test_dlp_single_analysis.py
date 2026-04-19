"""Tests for the pure single-probe analysis pipeline.

Covers each stage in isolation plus the orchestrator on:
  * a clean Gompertz-shaped Argon curve (the model the GUI's sim
    backend produces);
  * underdetermined / pathological inputs (must NOT raise, must
    surface warnings);
  * end-to-end against the real ``FakeB2901v2`` sim, mirroring what
    the GUI receives.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dlp_single_analysis import (
    analyze_single_iv,
    compute_n_e,
    estimate_v_plasma,
    find_v_float,
    fit_electron_saturation,
    fit_ion_saturation,
    fit_te_semilog,
    format_single_result_html,
    initial_te_estimate,
    M_AR_KG,
)


def _make_iv(v_min=-50.0, v_max=50.0, n=201,
             i_ion=5.5e-6, i_e=1.0e-3, te=4.0, v_p=0.0):
    """Generate an ideal Gompertz single-probe IV curve."""
    V = np.linspace(v_min, v_max, n)
    arg = (V - v_p) / te
    arg = np.clip(arg, -50.0, 50.0)
    e_factor = 1.0 - np.exp(-np.exp(arg))
    I = -i_ion + i_e * e_factor
    return V, I


# ---------------------------------------------------------------------------
class TestFindVFloat:
    def test_zero_crossing_recovered(self):
        V, I = _make_iv(te=4.0, i_ion=5.5e-6, i_e=1.0e-3)
        v_f, st = find_v_float(V, I)
        assert st == "ok"
        # Theoretical: V_f = V_p − T_e · ln(I_e/I_i) ≈ −20.8 V.
        v_theory = 0 - 4.0 * math.log(1e-3 / 5.5e-6)
        assert v_f == pytest.approx(v_theory, abs=0.5)

    def test_no_crossing_returns_none(self):
        V = np.linspace(-50, 50, 101)
        I = np.full_like(V, -1e-5)
        v_f, st = find_v_float(V, I)
        assert v_f is None
        assert "no zero" in st


class TestInitialTeEstimate:
    def test_close_to_truth_for_clean_data(self):
        V, I = _make_iv(te=4.0)
        v_f, _ = find_v_float(V, I)
        te_init = initial_te_estimate(V, I, v_f)
        # Coarse: within a factor of 2.
        assert te_init is not None
        assert 2.0 < te_init < 8.0


class TestIonSaturation:
    def test_recovers_magnitude(self):
        V, I = _make_iv(i_ion=5.5e-6, te=4.0)
        v_f, _ = find_v_float(V, I)
        te_init = initial_te_estimate(V, I, v_f)
        i_ion, _, st, _ = fit_ion_saturation(V, I, v_f, te_init)
        assert st == "ok"
        assert i_ion == pytest.approx(5.5e-6, rel=0.30)

    def test_too_few_points_returns_none(self):
        V = np.array([0.0, 1.0])
        I = np.array([0.0, 1.0])
        i_ion, _, _, _ = fit_ion_saturation(V, I, 0.0, 1.0)
        assert i_ion is None


class TestTeSemilog:
    def test_recovers_te_within_10_pct(self):
        V, I = _make_iv(te=4.0)
        v_f, _ = find_v_float(V, I)
        te_init = initial_te_estimate(V, I, v_f)
        i_ion, _, _, _ = fit_ion_saturation(V, I, v_f, te_init)
        te, _, r2, _, _, _, st = fit_te_semilog(V, I, v_f, i_ion, te_init)
        assert st == "ok"
        assert te == pytest.approx(4.0, rel=0.10)
        assert r2 > 0.99


class TestEstimateVPlasma:
    def test_v_p_recovered_close_to_truth(self):
        V, I = _make_iv(te=4.0, v_p=0.0)
        v_f, _ = find_v_float(V, I)
        te_init = initial_te_estimate(V, I, v_f)
        i_ion, _, _, _ = fit_ion_saturation(V, I, v_f, te_init)
        te, *_ = fit_te_semilog(V, I, v_f, i_ion, te_init)
        v_p, conf, st = estimate_v_plasma(V, I, te, v_f, i_ion)
        assert v_p is not None
        # The Gompertz knee is near V_p but soft; bracketed bisection
        # lands within a few T_e.
        assert abs(v_p) < 8.0
        assert conf in ("medium", "low")


class TestElectronSaturation:
    def test_value_close_to_i_electron_sat(self):
        V, I = _make_iv(i_e=1.0e-3, te=4.0)
        v_f, _ = find_v_float(V, I)
        te_init = initial_te_estimate(V, I, v_f)
        i_ion, _, _, _ = fit_ion_saturation(V, I, v_f, te_init)
        te, *_ = fit_te_semilog(V, I, v_f, i_ion, te_init)
        v_p, *_ = estimate_v_plasma(V, I, te, v_f, i_ion)
        i_e_sat, _, _, st = fit_electron_saturation(V, I, v_p, te)
        assert i_e_sat is not None
        # Linear extrapolation of V > V_p+2T_e back to V_p lands within ±30%.
        assert i_e_sat == pytest.approx(1.0e-3, rel=0.30)


class TestComputeNe:
    def test_typical_argon_density(self):
        n_e = compute_n_e(5.5e-6, 4.0, 1e-5, M_AR_KG)
        assert n_e is not None
        assert 1e14 < n_e < 1e18

    def test_invalid_inputs_return_none(self):
        assert compute_n_e(None, 4.0, 1e-5, M_AR_KG) is None
        assert compute_n_e(5.5e-6, 0, 1e-5, M_AR_KG) is None
        assert compute_n_e(5.5e-6, 4.0, 0, M_AR_KG) is None
        assert compute_n_e(5.5e-6, 4.0, 1e-5, 0) is None


# ---------------------------------------------------------------------------
class TestAnalyzeOrchestrator:
    def test_clean_argon_curve_yields_credible_results(self):
        V, I = _make_iv(te=4.0, i_ion=5.5e-6, i_e=1.0e-3, v_p=0.0)
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG,
                              gas_label="Argon (Ar)")
        assert r["ok"]
        assert r["v_float_V"] == pytest.approx(-20.8, abs=1.0)
        assert r["te_eV"] == pytest.approx(4.0, rel=0.10)
        assert r["i_ion_sat_A"] == pytest.approx(5.5e-6, rel=0.30)
        assert r["i_electron_sat_A"] == pytest.approx(1.0e-3, rel=0.30)
        assert r["n_e_m3"] is not None and r["n_e_m3"] > 0
        assert r["v_plasma_V"] is not None
        assert r["fit_R2_te"] > 0.95
        # After the V_p improvement, clean synthetic data may now
        # legitimately reach "high" confidence via the derivative
        # method.  The legacy intersection-only path used to top
        # out at "medium" — accept either.
        assert r["v_plasma_confidence"] in ("high", "medium", "low")

    def test_no_gas_uses_argon_fallback_and_warns(self):
        V, I = _make_iv()
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=None)
        assert r["m_i_is_fallback"]
        assert any("Argon" in w for w in r["warnings"])

    def test_too_few_points_short_circuits(self):
        V = np.linspace(-1, 1, 5)
        I = np.linspace(-1e-6, 1e-6, 5)
        r = analyze_single_iv(V, I, area_m2=1e-5)
        assert not r["ok"]
        assert any("less than 10" in w for w in r["warnings"])

    def test_no_zero_crossing_fails_gracefully(self):
        V = np.linspace(-50, 50, 101)
        I = np.full_like(V, -1e-5)
        r = analyze_single_iv(V, I, area_m2=1e-5)
        assert not r["ok"]
        assert r["v_float_V"] is None
        assert any("V_f" in w for w in r["warnings"])

    def test_html_format_contains_key_fields(self):
        V, I = _make_iv()
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        html = format_single_result_html(r)
        assert "Single-Probe Analysis" in html
        assert "V_f" in html
        assert "T_e" in html
        assert "I_i,sat" in html
        assert "n_e" in html

    def test_html_marks_low_confidence_and_warnings(self):
        # Force a degraded curve: only ion-side data, no electron side.
        V = np.linspace(-50, -10, 50)
        I = np.full_like(V, -5e-6)
        r = analyze_single_iv(V, I, area_m2=1e-5)
        html = format_single_result_html(r)
        # Either Warnings block or n/a markers must be present.
        assert "Warnings" in html or "n/a" in html


# ---------------------------------------------------------------------------
class TestEndToEndWithSimBackend:
    """Pipeline against the real ``FakeB2901v2`` single_probe model
    — exactly what the GUI feeds into the analysis."""

    def test_argon_sim_yields_consistent_results(self):
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        i_ion_sat=5.5e-6, i_electron_sat=1.0e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        V = np.linspace(-50, 50, 201)
        I = np.array([(f.set_voltage(v) or f.read_current()) for v in V])
        r = analyze_single_iv(V, I, area_m2=1e-5, m_i_kg=M_AR_KG)
        assert r["ok"]
        assert r["te_eV"] == pytest.approx(4.0, rel=0.15)
        assert abs(r["v_float_V"] - (-20.8)) < 2.0
