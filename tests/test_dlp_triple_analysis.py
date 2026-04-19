"""Pure-math tests for the triple-probe analysis module."""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dlp_triple_analysis import (
    DEFAULT_AREA_M2,
    DEFAULT_SPECIES,
    E_CHARGE,
    EV_TO_K,
    K_B,
    LN2,
    SPECIES_AMU,
    U_AMU,
    analyze_sample,
    compute_ne_m3,
    compute_te_ev,
    mean_mass_kg,
    mi_from_species,
    mgs_to_sccm,
    sccm_to_mgs,
    te_eq10,
    te_eq11,
    triple_probe_valid,
)


# ===========================================================================
# Eq-11 (closed form)
# ===========================================================================
class TestEq11:
    def test_closed_form(self):
        assert te_eq11(1.0) == pytest.approx(1.0 / LN2)
        assert te_eq11(3.0) == pytest.approx(3.0 / LN2)

    def test_zero_returns_zero(self):
        assert te_eq11(0.0) == 0.0

    def test_negative_returns_nan(self):
        assert math.isnan(te_eq11(-0.1))

    def test_nan_input_returns_nan(self):
        assert math.isnan(te_eq11(float("nan")))


# ===========================================================================
# Eq-10 (numerical)
# ===========================================================================
class TestEq10:
    def test_inside_valid_band_finite_and_positive(self):
        # V_d12 must be > 2·V_d13 for the model to be valid.
        te = te_eq10(v_d12=10.0, v_d13=2.0)
        assert math.isfinite(te) and te > 0

    def test_root_satisfies_implicit_equation(self):
        v12, v13 = 12.0, 3.0
        te = te_eq10(v12, v13)
        residual = (2.0 * math.exp(-v13 / te)
                    - (1.0 + math.exp(-v12 / te)))
        assert abs(residual) < 1e-6

    def test_below_validity_returns_nan(self):
        # V_d12 == 2·V_d13 → invalid; just below also invalid.
        assert math.isnan(te_eq10(v_d12=4.0, v_d13=2.0))
        assert math.isnan(te_eq10(v_d12=3.9, v_d13=2.0))

    def test_negative_or_nonfinite_returns_nan(self):
        assert math.isnan(te_eq10(-1.0, 0.5))
        assert math.isnan(te_eq10(5.0, -0.1))
        assert math.isnan(te_eq10(float("inf"), 1.0))

    def test_zero_v_d13_returns_zero(self):
        assert te_eq10(v_d12=10.0, v_d13=0.0) == 0.0

    def test_eq10_approaches_eq11_when_v_d12_dominates(self):
        # With V_d12 >> V_d13, exp(-V_d12/Te) → 0 and Eq-10 reduces to
        # 2·exp(-V_d13/Te) = 1, i.e. Te = V_d13/ln(2) = Eq-11.
        v13 = 1.5
        te_a = te_eq10(v_d12=1000.0, v_d13=v13)
        te_b = te_eq11(v13)
        assert te_a == pytest.approx(te_b, rel=1e-3)


# ===========================================================================
# Validity guard
# ===========================================================================
class TestValidity:
    def test_valid_band(self):
        assert triple_probe_valid(10.0, 1.0)
        assert triple_probe_valid(10.0, 4.99)

    def test_invalid_when_v_d12_too_low(self):
        assert not triple_probe_valid(2.0, 2.0)
        assert not triple_probe_valid(3.9, 2.0)

    def test_invalid_when_v_d13_negative(self):
        assert not triple_probe_valid(10.0, -0.1)

    def test_invalid_when_nonfinite(self):
        assert not triple_probe_valid(float("nan"), 1.0)
        assert not triple_probe_valid(10.0, float("inf"))


# ===========================================================================
# compute_te_ev (Eq-10 with Eq-11 fallback)
# ===========================================================================
class TestComputeTe:
    def test_prefer_eq10_uses_exact_solution(self):
        te_exact = te_eq10(10.0, 2.0)
        assert compute_te_ev(10.0, 2.0, prefer_eq10=True) == pytest.approx(te_exact)

    def test_prefer_eq11_returns_closed_form(self):
        te = compute_te_ev(10.0, 2.0, prefer_eq10=False)
        assert te == pytest.approx(2.0 / LN2)

    def test_invalid_input_returns_nan(self):
        assert math.isnan(compute_te_ev(2.0, 2.0))

    def test_eq11_fallback_when_eq10_unsolvable(self):
        # Edge: V_d13 just under V_d12/2 — Eq-10 may converge slowly,
        # Eq-11 always gives a finite result.  We don't force Eq-10 to
        # fail (that would require monkeypatching), but we verify the
        # wrapper never returns NaN inside the valid band.
        te = compute_te_ev(10.0, 4.99)
        assert math.isfinite(te) and te > 0


# ===========================================================================
# n_e
# ===========================================================================
class TestComputeNe:
    def test_positive_density_for_negative_current(self):
        # current into the probe is negative by convention; result > 0.
        ne = compute_ne_m3(
            i_a=-1e-3, te_ev=2.0, v_d13=2.0,
            area_m2=DEFAULT_AREA_M2,
            mi_kg=mi_from_species("Argon (Ar)"))
        assert ne > 0

    def test_zero_or_pathological_inputs_return_zero(self):
        assert compute_ne_m3(0.0, 2.0, 2.0,
                              DEFAULT_AREA_M2, U_AMU * 40) == 0.0
        # Te = 0 → 0
        assert compute_ne_m3(-1e-3, 0.0, 2.0,
                              DEFAULT_AREA_M2, U_AMU * 40) == 0.0
        # Area = 0 → 0
        assert compute_ne_m3(-1e-3, 2.0, 2.0,
                              0.0, U_AMU * 40) == 0.0
        # mi = 0 → 0
        assert compute_ne_m3(-1e-3, 2.0, 2.0,
                              DEFAULT_AREA_M2, 0.0) == 0.0

    def test_density_scales_with_current(self):
        kw = dict(te_ev=2.0, v_d13=2.0,
                  area_m2=DEFAULT_AREA_M2,
                  mi_kg=mi_from_species("Argon (Ar)"))
        ne1 = compute_ne_m3(i_a=-1e-3, **kw)
        ne2 = compute_ne_m3(i_a=-2e-3, **kw)
        assert ne2 == pytest.approx(2 * ne1, rel=1e-9)

    def test_density_formula_matches_bohm_definition(self):
        te = 2.0
        v13 = 2.0
        area = DEFAULT_AREA_M2
        mi = mi_from_species("Argon (Ar)")
        i = -1e-3
        v_bohm = math.sqrt(K_B * te * EV_TO_K / mi)
        x = v13 / te
        ex = math.exp(-x)
        expected = (-i) * (ex / (1 - ex)) / (0.61 * area * E_CHARGE * v_bohm)
        got = compute_ne_m3(i, te, v13, area, mi)
        assert got == pytest.approx(expected, rel=1e-12)


# ===========================================================================
# Gas / mass helpers
# ===========================================================================
class TestGasHelpers:
    def test_mi_from_known_species(self):
        assert mi_from_species("Argon (Ar)") == pytest.approx(
            SPECIES_AMU["Argon (Ar)"] * U_AMU)
        assert mi_from_species("Xenon (Xe)") == pytest.approx(
            SPECIES_AMU["Xenon (Xe)"] * U_AMU)

    def test_mi_from_unknown_species_falls_back(self):
        assert mi_from_species("Unobtainium") == pytest.approx(
            mi_from_species(DEFAULT_SPECIES))

    def test_sccm_mgs_round_trip(self):
        for name in ("Argon (Ar)", "Krypton (Kr)", "Xenon (Xe)"):
            for sccm in (1.0, 5.0, 42.0):
                back = mgs_to_sccm(sccm_to_mgs(sccm, name), name)
                assert back == pytest.approx(sccm, rel=1e-9)

    def test_mean_mass_empty_returns_default(self):
        assert mean_mass_kg([]) == pytest.approx(
            mi_from_species(DEFAULT_SPECIES))

    def test_mean_mass_single_species(self):
        assert mean_mass_kg([("Xenon (Xe)", 0.0)]) == pytest.approx(
            mi_from_species("Xenon (Xe)"))

    def test_mean_mass_flow_weighted(self):
        # 1 sccm Ar + 1 sccm Xe → arithmetic mean of the two AMUs.
        m = mean_mass_kg([("Argon (Ar)", 1.0), ("Xenon (Xe)", 1.0)])
        expected = ((SPECIES_AMU["Argon (Ar)"]
                     + SPECIES_AMU["Xenon (Xe)"]) / 2) * U_AMU
        assert m == pytest.approx(expected, rel=1e-12)

    def test_mean_mass_skips_zero_flow_when_others_have_flow(self):
        m = mean_mass_kg([("Argon (Ar)", 1.0), ("Xenon (Xe)", 0.0)])
        assert m == pytest.approx(mi_from_species("Argon (Ar)"))

    def test_mean_mass_all_zero_uses_equal_weight(self):
        m = mean_mass_kg([("Argon (Ar)", 0.0), ("Xenon (Xe)", 0.0)])
        expected = ((SPECIES_AMU["Argon (Ar)"]
                     + SPECIES_AMU["Xenon (Xe)"]) / 2) * U_AMU
        assert m == pytest.approx(expected, rel=1e-12)


# ===========================================================================
# Convenience wrapper
# ===========================================================================
class TestAnalyzeSample:
    def test_round_trip_yields_finite_te_and_ne(self):
        out = analyze_sample(
            v_d12=10.0, v_d13=2.0, i_measure_a=-1e-3,
            species_name="Argon (Ar)")
        assert math.isfinite(out["Te_eV"]) and out["Te_eV"] > 0
        assert out["n_e_m3"] > 0

    def test_invalid_inputs_propagate(self):
        out = analyze_sample(
            v_d12=2.0, v_d13=2.0, i_measure_a=-1e-3,
            species_name="Argon (Ar)")
        assert math.isnan(out["Te_eV"])
        assert out["n_e_m3"] == 0.0

    def test_mi_kg_override_takes_precedence(self):
        out_a = analyze_sample(
            v_d12=10.0, v_d13=2.0, i_measure_a=-1e-3,
            species_name="Argon (Ar)")
        out_b = analyze_sample(
            v_d12=10.0, v_d13=2.0, i_measure_a=-1e-3,
            species_name="Argon (Ar)",
            mi_kg=mi_from_species("Xenon (Xe)"))
        # Heavier ion → lower Bohm velocity → higher n_e.
        assert out_b["n_e_m3"] > out_a["n_e_m3"]
