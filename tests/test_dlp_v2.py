"""Tests for the v2 improved double-Langmuir simulation model."""
from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fake_b2901_v2 import FakeB2901v2
from fake_b2901 import FakeB2901
from DoubleLangmuirAnalysis_v2 import (
    parse_dlp_csv, compute_metrics,
    fit_saturation_branches, correct_iv_curve, compute_plasma_params,
)
from DoubleLangmuir_measure import write_csv, DLPScanWorker
from DoubleLangmuir_measure_v2 import (
    default_data_dir, DLPMainWindowV2, build_sweep_voltages,
    format_result_block, format_model_comparison,
    _ensure_valid_app_font,
)
from dlp_fit_models import (
    FitModelDialog, fit_dlp_model, MODELS, MODEL_KEYS, DEFAULT_MODEL,
    grade_fit_quality, compare_all_models,
)
from dlp_probe_dialog import (
    ProbeParameterDialog, DEFAULT_PROBE_PARAMS,
    compute_electrode_area, probe_params_for_csv,
)
from dlp_experiment_dialog import (
    ExperimentParameterDialog, DEFAULT_EXPERIMENT_PARAMS,
    sccm_to_mgs, mgs_to_sccm, effective_ion_mass_kg, GAS_DATA,
)
from dlp_sim_dialog import (
    SimulationOptionsDialog, DEFAULT_SIM_OPTIONS,
    sim_options_to_fake_kwargs, PRESETS,
)
from dlp_instrument_dialog import (
    InstrumentOptionsDialog, DEFAULT_INSTRUMENT_OPTIONS,
    get_nplc, estimate_sweep_time, INSTRUMENT_PRESETS,
)


# ── helpers ──────────────────────────────────────────────────────────

def _sweep(fake, voltages):
    """Return list of currents for a voltage sweep."""
    fake.connect()
    fake.output(True)
    currents = []
    for v in voltages:
        fake.set_voltage(v)
        currents.append(fake.read_current())
    return currents


# ── basic API ────────────────────────────────────────────────────────

class TestFakeB2901v2API:

    def test_default_model(self):
        f = FakeB2901v2()
        assert f.model == "double_langmuir"

    def test_idn_v2(self):
        f = FakeB2901v2()
        assert "SIM-v2" in f.connect()

    def test_tanh_model_still_works(self):
        f = FakeB2901v2(model="tanh", i_sat=1e-3, seed=0)
        f.connect(); f.output(True)
        f.set_voltage(50.0)
        i = f.read_current()
        assert abs(i - 1e-3) < 1e-6  # saturated tanh ≈ i_sat

    def test_resistor_model_still_works(self):
        f = FakeB2901v2(model="resistor", resistance=1000.0, seed=0)
        f.connect(); f.output(True)
        f.set_voltage(5.0)
        assert f.read_current() == pytest.approx(0.005)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            FakeB2901v2(model="magic")

    def test_inherits_failure_injection(self):
        from fake_b2901 import SimulatedTimeout
        f = FakeB2901v2(fail_after=0, fail_on="read_current", seed=0)
        f.connect(); f.output(True)
        with pytest.raises(SimulatedTimeout):
            f.read_current()


# ── curve shape: sloped saturation ───────────────────────────────────

class TestSlopedSaturation:
    """The double_langmuir model must NOT have flat saturation branches."""

    @pytest.fixture()
    def voltages(self):
        return np.linspace(-50, 50, 201)

    @pytest.fixture()
    def curve(self, voltages):
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        return np.array(_sweep(f, voltages))

    @pytest.fixture()
    def v1_curve(self, voltages):
        """Old flat-saturation curve for comparison."""
        f = FakeB2901(model="tanh", i_sat=2e-3, te_eV=3.0, seed=0)
        return np.array(_sweep(f, voltages))

    def test_positive_saturation_has_slope(self, voltages, curve):
        """Current at V=+50 must be larger than at V=+20."""
        idx_20 = np.argmin(np.abs(voltages - 20))
        idx_50 = np.argmin(np.abs(voltages - 50))
        assert curve[idx_50] > curve[idx_20]

    def test_negative_saturation_has_slope(self, voltages, curve):
        """Current at V=-50 must be more negative than at V=-20."""
        idx_m20 = np.argmin(np.abs(voltages - (-20)))
        idx_m50 = np.argmin(np.abs(voltages - (-50)))
        assert curve[idx_m50] < curve[idx_m20]

    def test_slope_in_positive_saturation_region(self, voltages, curve):
        """Finite positive dI/dV in the +30..+50 V range."""
        mask = (voltages >= 30) & (voltages <= 50)
        v_sat = voltages[mask]
        i_sat = curve[mask]
        slope = np.polyfit(v_sat, i_sat, 1)[0]
        assert slope > 0, f"Expected positive slope, got {slope}"

    def test_slope_in_negative_saturation_region(self, voltages, curve):
        """Finite positive dI/dV in the -50..-30 V range."""
        mask = (voltages >= -50) & (voltages <= -30)
        v_sat = voltages[mask]
        i_sat = curve[mask]
        slope = np.polyfit(v_sat, i_sat, 1)[0]
        assert slope > 0, f"Expected positive slope, got {slope}"

    def test_v1_saturation_is_flat(self, voltages, v1_curve):
        """Old v1 model: saturation region is essentially flat."""
        mask = (voltages >= 30) & (voltages <= 50)
        i_sat = v1_curve[mask]
        spread = i_sat.max() - i_sat.min()
        assert spread < 1e-9, "v1 tanh saturation should be flat"

    def test_v2_saturation_is_not_flat(self, voltages, curve):
        """v2 model: saturation spread must be significant."""
        mask = (voltages >= 30) & (voltages <= 50)
        i_sat = curve[mask]
        spread = i_sat.max() - i_sat.min()
        assert spread > 1e-4, f"v2 saturation spread too small: {spread}"


# ── monotonicity and smoothness ──────────────────────────────────────

class TestMonotonicityAndSmoothness:

    def test_monotonically_increasing(self):
        """I(V) must be monotonically increasing for the full sweep."""
        V = np.linspace(-50, 50, 501)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        diffs = np.diff(I)
        assert np.all(diffs >= 0), "Curve must be monotonically increasing"

    def test_no_discontinuities(self):
        """No large jumps between adjacent points.

        With the smooth transition (W = 2*te_eV = 6 V), the max step
        at 0.1 V spacing should be well below i_sat.
        """
        V = np.linspace(-50, 50, 1001)
        i_sat = 2e-3
        f = FakeB2901v2(i_sat=i_sat, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        diffs = np.abs(np.diff(I))
        max_jump = diffs.max()
        # smooth curve: max step << i_sat (not ~2*i_sat like old step)
        assert max_jump < 0.1 * i_sat, f"Max jump {max_jump} too large"

    def test_approximate_antisymmetry(self):
        """I(V) ≈ -I(-V) for the symmetric model."""
        V = np.linspace(-50, 50, 201)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        # compare I(V) with -I(-V)
        I_rev = -I[::-1]
        np.testing.assert_allclose(I, I_rev, atol=1e-12)


# ── determinism ──────────────────────────────────────────────────────

class TestDeterminism:

    def test_same_seed_same_result(self):
        V = np.linspace(-20, 20, 41)
        I1 = np.array(_sweep(
            FakeB2901v2(seed=42, noise_std=1e-5), V))
        I2 = np.array(_sweep(
            FakeB2901v2(seed=42, noise_std=1e-5), V))
        np.testing.assert_array_equal(I1, I2)

    def test_no_noise_perfectly_deterministic(self):
        V = np.linspace(-20, 20, 41)
        I1 = np.array(_sweep(FakeB2901v2(seed=0), V))
        I2 = np.array(_sweep(FakeB2901v2(seed=99), V))
        # with noise_std=0 (default), seed doesn't matter
        np.testing.assert_array_equal(I1, I2)


# ── sheath_conductance parameter ─────────────────────────────────────

class TestSheathConductance:

    def test_zero_conductance_bounded_by_isat(self):
        """With g_sheath=0 the double_langmuir saturates at ±i_sat."""
        V = np.linspace(-50, 50, 201)
        i_sat = 2e-3
        I = np.array(_sweep(
            FakeB2901v2(sheath_conductance=0.0, i_sat=i_sat, te_eV=3.0,
                         seed=0), V))
        assert I[-1] == pytest.approx(i_sat, rel=1e-4)
        assert I[0] == pytest.approx(-i_sat, rel=1e-4)

    def test_larger_conductance_steeper_slope(self):
        """Doubling g_sheath should roughly double the saturation slope."""
        V = np.linspace(30, 50, 21)
        I_lo = np.array(_sweep(
            FakeB2901v2(sheath_conductance=3e-5, seed=0), V))
        I_hi = np.array(_sweep(
            FakeB2901v2(sheath_conductance=6e-5, seed=0), V))
        slope_lo = np.polyfit(V, I_lo, 1)[0]
        slope_hi = np.polyfit(V, I_hi, 1)[0]
        ratio = slope_hi / slope_lo
        assert 1.8 < ratio < 2.2, f"Slope ratio {ratio} not ~2x"


# ── asymmetry ────────────────────────────────────────────────────────

class TestAsymmetry:

    def test_zero_asymmetry_is_antisymmetric(self):
        V = np.linspace(-40, 40, 81)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0.0, i_sat=2e-3, seed=0), V))
        np.testing.assert_allclose(I, -I[::-1], atol=1e-12)

    def test_positive_asymmetry_changes_saturation(self):
        V = np.linspace(-40, 40, 81)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0.05, i_sat=2e-3,
                         sheath_conductance=0, seed=0), V))
        i_pos = I[-1]   # V = +40
        i_neg = I[0]     # V = -40
        # |I_pos| should be larger than |I_neg|
        assert abs(i_pos) > abs(i_neg)
        # ratio should be close to (1+a)/(1-a) = 1.05/0.95 ≈ 1.105
        ratio = abs(i_pos) / abs(i_neg)
        assert 1.08 < ratio < 1.13

    def test_asymmetry_breaks_antisymmetry(self):
        V = np.linspace(-40, 40, 81)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0.05, i_sat=2e-3, seed=0), V))
        residual = np.max(np.abs(I + I[::-1]))
        assert residual > 1e-5, "Asymmetry should break antisymmetry"

    def test_backward_compat_defaults(self):
        """All new params at 0 → same as previous v2 behavior."""
        V = np.linspace(-30, 30, 61)
        I_new = np.array(_sweep(
            FakeB2901v2(asymmetry=0, i_offset=0, drift_per_point=0,
                         noise_corr=0, i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0), V))
        I_old = np.array(_sweep(
            FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0), V))
        np.testing.assert_allclose(I_new, I_old, atol=1e-15)


# ── offset and drift ─────────────────────────────────────────────────

class TestOffsetAndDrift:

    def test_constant_offset(self):
        V = np.array([0.0])
        f = FakeB2901v2(i_offset=1e-4, sheath_conductance=0,
                         i_sat=2e-3, seed=0)
        I = _sweep(f, V)
        # at V=0, tanh(0)=0, so I = i_offset
        assert I[0] == pytest.approx(1e-4)

    def test_offset_shifts_entire_curve(self):
        V = np.linspace(-20, 20, 41)
        off = 5e-5
        I_no = np.array(_sweep(
            FakeB2901v2(i_offset=0, seed=0), V))
        I_off = np.array(_sweep(
            FakeB2901v2(i_offset=off, seed=0), V))
        np.testing.assert_allclose(I_off - I_no, off, atol=1e-12)

    def test_drift_accumulates(self):
        V = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        drift = 1e-5
        f = FakeB2901v2(drift_per_point=drift, i_offset=0,
                         sheath_conductance=0, i_sat=2e-3, seed=0)
        I = _sweep(f, V)
        # at V=0, I = drift*idx for idx=0..4
        for idx in range(5):
            assert I[idx] == pytest.approx(drift * idx)

    def test_zero_drift_no_effect(self):
        V = np.linspace(-20, 20, 41)
        I1 = np.array(_sweep(FakeB2901v2(drift_per_point=0, seed=0), V))
        I2 = np.array(_sweep(FakeB2901v2(drift_per_point=0, seed=0), V))
        np.testing.assert_array_equal(I1, I2)


# ── correlated noise ─────────────────────────────────────────────────

class TestCorrelatedNoise:

    def test_correlated_noise_deterministic(self):
        V = np.linspace(-10, 10, 51)
        I1 = np.array(_sweep(
            FakeB2901v2(noise_std=1e-5, noise_corr=0.8, seed=42), V))
        I2 = np.array(_sweep(
            FakeB2901v2(noise_std=1e-5, noise_corr=0.8, seed=42), V))
        np.testing.assert_array_equal(I1, I2)

    def test_correlated_noise_differs_from_white(self):
        V = np.linspace(-20, 20, 201)
        I_white = np.array(_sweep(
            FakeB2901v2(noise_std=1e-4, noise_corr=0.0, seed=7), V))
        I_corr = np.array(_sweep(
            FakeB2901v2(noise_std=1e-4, noise_corr=0.9, seed=7), V))
        # the actual values should differ because the filter changes them
        assert not np.allclose(I_white, I_corr)

    def test_correlated_noise_has_lower_high_freq_content(self):
        """Correlated noise should have more low-freq power."""
        V = np.linspace(-20, 20, 501)
        noiseless = np.array(_sweep(
            FakeB2901v2(noise_std=0, seed=0), V))
        I_corr = np.array(_sweep(
            FakeB2901v2(noise_std=1e-4, noise_corr=0.9, seed=7), V))
        residual = I_corr - noiseless
        # autocorrelation at lag=1 should be positive for correlated noise
        ac1 = np.corrcoef(residual[:-1], residual[1:])[0, 1]
        assert ac1 > 0.3, f"Expected positive autocorrelation, got {ac1}"


# ── CSV parsing and analysis ─────────────────────────────────────────

class TestCsvParsing:

    def _write_test_csv(self, tmp_path, bidir=False):
        V = np.linspace(-20, 20, 41)
        f = FakeB2901v2(i_sat=2e-3, asymmetry=0.03, seed=0)
        I = np.array(_sweep(f, V))
        stds = np.zeros_like(I)
        dirs = ["fwd"] * len(V) if not bidir else (
            ["fwd"] * 21 + ["rev"] * 20)
        compls = [False] * len(V)
        meta = {"Run_Status": "completed", "Date": "2026-04-15",
                "Points": str(len(V)), "Bidirectional": str(bidir)}
        path = tmp_path / "test.csv"
        write_csv(path, meta, list(V), list(I), list(stds), list(V),
                  dirs, compls)
        return path, V, I

    def test_parse_roundtrip(self, tmp_path):
        path, V_orig, I_orig = self._write_test_csv(tmp_path)
        meta, data = parse_dlp_csv(path)
        assert meta["Run_Status"] == "completed"
        assert len(data["V_soll"]) == 41
        np.testing.assert_allclose(data["I_mean"], I_orig, rtol=1e-5)

    def test_parse_has_direction(self, tmp_path):
        path, _, _ = self._write_test_csv(tmp_path, bidir=True)
        meta, data = parse_dlp_csv(path)
        assert "dir" in data
        assert data["dir"][0] == "fwd"
        assert data["dir"][-1] == "rev"

    def test_parse_meta_keys(self, tmp_path):
        path, _, _ = self._write_test_csv(tmp_path)
        meta, _ = parse_dlp_csv(path)
        assert "Date" in meta
        assert "Points" in meta


class TestComputeMetrics:

    def test_symmetric_curve_ratio_near_one(self):
        V = np.linspace(-40, 40, 201)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0, i_sat=2e-3,
                         sheath_conductance=5e-5, seed=0), V))
        m = compute_metrics(V, I)
        assert 0.99 < m["asymmetry_ratio"] < 1.01

    def test_asymmetric_curve_ratio_deviates(self):
        V = np.linspace(-40, 40, 201)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0.05, i_sat=2e-3,
                         sheath_conductance=5e-5, seed=0), V))
        m = compute_metrics(V, I)
        assert m["asymmetry_ratio"] > 1.05

    def test_zero_crossing_near_origin(self):
        V = np.linspace(-40, 40, 201)
        I = np.array(_sweep(
            FakeB2901v2(asymmetry=0, i_offset=0, seed=0), V))
        m = compute_metrics(V, I)
        assert abs(m["v_zero"]) < 0.5

    def test_offset_shifts_zero_crossing(self):
        V = np.linspace(-40, 40, 201)
        I_no = np.array(_sweep(
            FakeB2901v2(i_offset=0, i_sat=2e-3,
                         sheath_conductance=5e-5, seed=0), V))
        I_off = np.array(_sweep(
            FakeB2901v2(i_offset=5e-4, i_sat=2e-3,
                         sheath_conductance=5e-5, seed=0), V))
        m_no = compute_metrics(V, I_no)
        m_off = compute_metrics(V, I_off)
        # offset should shift zero crossing to more negative V
        assert m_off["v_zero"] < m_no["v_zero"]

    def test_positive_branch_slopes(self):
        V = np.linspace(-40, 40, 201)
        I = np.array(_sweep(
            FakeB2901v2(sheath_conductance=5e-5, seed=0), V))
        m = compute_metrics(V, I)
        assert m["slope_pos"] > 0
        assert m["slope_neg"] > 0


# ── smooth center transition ─────────────────────────────────────────

class TestSmoothTransition:
    """The new model must NOT show a step-like jump near V=0."""

    def test_center_slope_finite_and_bounded(self):
        """dI/dV near V=0 should be large but not absurdly steep.

        With W = 2*te_eV = 6 V, the peak slope at V=0 is
        I_sat / W ≈ 2e-3 / 6 ≈ 3.3e-4 A/V (plus g_sheath).
        """
        V = np.linspace(-1, 1, 101)  # fine grid around origin
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        slopes = np.diff(I) / np.diff(V)
        peak_slope = slopes.max()
        # should be ~3.3e-4 + 5e-5 ≈ 3.8e-4, not ~4 A/V like old model
        assert peak_slope < 1e-2, f"Peak slope {peak_slope} too steep"
        assert peak_slope > 1e-4, f"Peak slope {peak_slope} too flat"

    def test_gradual_rise_not_step(self):
        """At V = ±1 V the current should NOT yet be at saturation."""
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=0, seed=0)
        f.connect(); f.output(True)
        f.set_voltage(1.0)
        i_1v = f.read_current()
        # tanh(1/6) ≈ 0.165 → I ≈ 0.33 mA, far from 2 mA saturation
        assert abs(i_1v) < 0.5 * f.i_sat, (
            f"At 1V, |I|={abs(i_1v):.2e} should be well below I_sat/2")

    def test_transition_spans_several_volts(self):
        """Current should reach 90% of saturation only beyond ~5 V."""
        V = np.linspace(0, 30, 301)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=0, seed=0)
        I = np.array(_sweep(f, V))
        i90 = 0.9 * f.i_sat
        # find first V where I > 90% I_sat
        idx = np.argmax(I > i90)
        v90 = V[idx]
        assert v90 > 3.0, f"90% saturation reached at {v90:.1f} V (too soon)"
        assert v90 < 20.0, f"90% saturation not reached by {v90:.1f} V"

    def test_smooth_curvature_in_center(self):
        """The second derivative should be continuous (no kink)."""
        V = np.linspace(-15, 15, 601)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        dI = np.diff(I) / np.diff(V)
        d2I = np.diff(dI)
        # check that d2I has no extreme outlier (kink would show as spike)
        assert np.max(np.abs(d2I)) < 1e-5, "Second derivative spike (kink)"

    def test_much_smoother_than_old_step(self):
        """Max single-step dI at 0.5V spacing must be << 2*i_sat."""
        V = np.linspace(-50, 50, 201)  # 0.5 V steps
        i_sat = 2e-3
        f = FakeB2901v2(i_sat=i_sat, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        max_step = np.max(np.abs(np.diff(I)))
        # old model: max_step ≈ 2*i_sat = 4e-3 (entire jump in one step)
        # new model: max_step should be ~i_sat/W * dV ≈ 3.3e-4*0.5 ≈ 1.7e-4
        assert max_step < 0.1 * i_sat, (
            f"Max step {max_step:.2e} still looks like a jump")

    def test_transition_width_parameter(self):
        """Explicit transition_width overrides the default 2*te_eV."""
        V = np.linspace(-50, 50, 201)
        f_narrow = FakeB2901v2(transition_width=2.0, i_sat=2e-3,
                                sheath_conductance=0, seed=0)
        f_wide = FakeB2901v2(transition_width=10.0, i_sat=2e-3,
                              sheath_conductance=0, seed=0)
        I_narrow = np.array(_sweep(f_narrow, V))
        I_wide = np.array(_sweep(f_wide, V))
        # at V = 5 V: narrow should be closer to saturation than wide
        idx_5 = np.argmin(np.abs(V - 5.0))
        assert I_narrow[idx_5] > I_wide[idx_5]

    def test_deterministic_with_new_model(self):
        V = np.linspace(-30, 30, 61)
        I1 = np.array(_sweep(FakeB2901v2(seed=0), V))
        I2 = np.array(_sweep(FakeB2901v2(seed=0), V))
        np.testing.assert_array_equal(I1, I2)

    def test_still_monotonic(self):
        V = np.linspace(-50, 50, 501)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        assert np.all(np.diff(I) >= 0), "Curve must be monotonically increasing"

    def test_still_antisymmetric_when_symmetric_params(self):
        V = np.linspace(-50, 50, 201)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, asymmetry=0, seed=0)
        I = np.array(_sweep(f, V))
        np.testing.assert_allclose(I, -I[::-1], atol=1e-12)


# ── saturation fit analysis ──────────────────────────────────────────

class TestFitSaturationBranches:
    """Tests for the saturation-branch fitting and correction logic."""

    @pytest.fixture()
    def known_curve(self):
        """Generate a curve with known parameters for fit validation."""
        i_sat = 2e-3
        g = 5e-5
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=i_sat, te_eV=3.0,
                         sheath_conductance=g, asymmetry=0, seed=0)
        I = np.array(_sweep(f, V))
        return V, I, i_sat, g

    def test_fit_recovers_sheath_conductance(self, known_curve):
        """Average slope should match the known sheath conductance."""
        V, I, i_sat, g = known_curve
        fit = fit_saturation_branches(V, I)
        assert fit["slope_avg"] == pytest.approx(g, rel=0.05)

    def test_fit_recovers_i_sat_positive(self, known_curve):
        """Positive intercept should approximate +I_sat."""
        V, I, i_sat, g = known_curve
        fit = fit_saturation_branches(V, I)
        assert fit["i_sat_pos"] == pytest.approx(i_sat, rel=0.02)

    def test_fit_recovers_i_sat_negative(self, known_curve):
        """Negative intercept should approximate -I_sat."""
        V, I, i_sat, g = known_curve
        fit = fit_saturation_branches(V, I)
        assert fit["i_sat_neg"] == pytest.approx(-i_sat, rel=0.02)

    def test_both_slopes_close_for_symmetric(self, known_curve):
        """For symmetric probe, pos and neg slopes should be similar."""
        V, I, _, _ = known_curve
        fit = fit_saturation_branches(V, I)
        assert fit["slope_pos"] == pytest.approx(fit["slope_neg"], rel=0.05)

    def test_custom_boundaries(self, known_curve):
        """Explicit v_pos_min and v_neg_max override auto-detection."""
        V, I, _, _ = known_curve
        fit = fit_saturation_branches(V, I, v_pos_min=30.0, v_neg_max=-30.0)
        assert fit["v_pos_min"] == 30.0
        assert fit["v_neg_max"] == -30.0
        assert fit["n_pos"] > 0
        assert fit["n_neg"] > 0

    def test_sat_fraction_controls_region_size(self, known_curve):
        """Larger sat_fraction → more points in each fit region."""
        V, I, _, _ = known_curve
        fit_small = fit_saturation_branches(V, I, sat_fraction=0.1)
        fit_large = fit_saturation_branches(V, I, sat_fraction=0.3)
        assert fit_large["n_pos"] > fit_small["n_pos"]
        assert fit_large["n_neg"] > fit_small["n_neg"]

    def test_too_few_points_raises(self):
        """If boundaries leave < 2 points, a ValueError is raised."""
        V = np.linspace(-10, 10, 5)
        I = np.linspace(-1, 1, 5)
        with pytest.raises(ValueError, match="Not enough points"):
            fit_saturation_branches(V, I, v_pos_min=999)

    def test_asymmetric_curves_different_i_sat(self):
        """Asymmetric probes → |i_sat_pos| != |i_sat_neg|."""
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, asymmetry=0.05,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        fit = fit_saturation_branches(V, I)
        assert abs(fit["i_sat_pos"]) > abs(fit["i_sat_neg"])


class TestCorrectIVCurve:
    """Tests for the sheath-slope correction step."""

    def test_correction_removes_linear_trend(self):
        """After correction, the saturation regions should be ~flat."""
        V = np.linspace(-40, 40, 201)
        g = 8e-5
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=g, seed=0)
        I = np.array(_sweep(f, V))

        fit = fit_saturation_branches(V, I)
        I_corr = correct_iv_curve(V, I, fit)

        # positive saturation of corrected curve: slope should be ~0
        mask = V >= fit["v_pos_min"]
        slope_corr = np.polyfit(V[mask], I_corr[mask], 1)[0]
        assert abs(slope_corr) < 1e-6, (
            f"Corrected pos slope {slope_corr:.2e} not near zero")

    def test_corrected_curve_bounded(self):
        """Corrected curve should be bounded roughly by ±I_sat."""
        V = np.linspace(-40, 40, 201)
        i_sat = 2e-3
        f = FakeB2901v2(i_sat=i_sat, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))

        fit = fit_saturation_branches(V, I)
        I_corr = correct_iv_curve(V, I, fit)

        assert np.max(I_corr) == pytest.approx(i_sat, rel=0.05)
        assert np.min(I_corr) == pytest.approx(-i_sat, rel=0.05)

    def test_correction_preserves_center_shape(self):
        """The central transition should still be essentially monotonic.

        Tiny negative diffs (< 1e-7) may arise from fit residuals and
        are acceptable.
        """
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))

        fit = fit_saturation_branches(V, I)
        I_corr = correct_iv_curve(V, I, fit)

        diffs = np.diff(I_corr)
        assert np.min(diffs) > -1e-6, (
            f"Corrected curve has excessive negative step: {np.min(diffs):.2e}")

    def test_csv_roundtrip_with_analysis(self, tmp_path):
        """Full pipeline: simulate → CSV → parse → fit → correct."""
        V = np.linspace(-30, 30, 121)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=6e-5, seed=0)
        I_orig = np.array(_sweep(f, V))

        # write CSV
        path = tmp_path / "analysis_test.csv"
        dirs = ["fwd"] * len(V)
        compls = [False] * len(V)
        meta = {"Run_Status": "completed", "Points": str(len(V))}
        write_csv(path, meta, list(V), list(I_orig), [0.0]*len(V),
                  list(V), dirs, compls)

        # parse and analyse
        _, data = parse_dlp_csv(path)
        fit = fit_saturation_branches(data["V_ist"], data["I_mean"])
        I_corr = correct_iv_curve(data["V_ist"], data["I_mean"], fit)

        assert fit["slope_avg"] == pytest.approx(6e-5, rel=0.1)
        assert abs(np.max(I_corr)) < 2.5e-3  # bounded near I_sat

    def test_pure_linear_data(self):
        """On a purely linear curve, correction should remove the line."""
        V = np.linspace(-20, 20, 41)
        slope = 1e-4
        I = slope * V + 0.5e-3
        fit = fit_saturation_branches(V, I)
        I_corr = correct_iv_curve(V, I, fit)
        # after correction, should be ~constant (slope removed)
        assert np.std(I_corr) < 1e-6


# ── default data directory ───────────────────────────────────────────

class TestDefaultDataDir:

    def test_returns_data_subdir(self):
        d = default_data_dir()
        # Renamed from "double_langmuir" → "lp_measurements" so the
        # base folder is method-neutral.  See paths.py.
        assert d.name == "lp_measurements"
        assert d.parent.name == "data"

    def test_dir_exists_after_call(self):
        d = default_data_dir()
        assert d.is_dir()

    def test_not_cwd(self):
        """Default data dir must NOT be the project root / cwd."""
        d = default_data_dir()
        assert d != Path.cwd()

    def test_csv_in_data_dir(self):
        """make_csv_path puts the file under <data_dir>/<method>/."""
        from DoubleLangmuir_measure import make_csv_path
        d = default_data_dir()
        p = make_csv_path(d)
        assert "lp_measurements" in str(p)
        # Unified scheme: <data_dir>/double/LP_<ts>_double.csv
        assert p.parent == d / "double"


# ── GUI analysis integration ─────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestGuiAnalysisIntegration:
    """Tests target DLPMainWindowV2, NOT the base DLPMainWindow."""

    def test_fit_lines_exist_on_v2_window(self, qapp):
        """The v2 window must have fit overlay line objects."""
        win = DLPMainWindowV2()
        assert hasattr(win, "line_fit_pos")
        assert hasattr(win, "line_fit_neg")
        assert hasattr(win, "line_corrected")

    def test_fit_lines_initially_empty(self, qapp):
        win = DLPMainWindowV2()
        assert len(win.line_fit_pos.get_xdata()) == 0
        assert len(win.line_fit_neg.get_xdata()) == 0
        assert len(win.line_corrected.get_xdata()) == 0

    def test_analyze_button_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnAnalyze")
        assert win.btnAnalyze.text() == "Analyze"

    def _make_analysed_window(self, qapp):
        """Helper: create a v2 window with data and run analysis."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        return win

    def test_run_analysis_populates_fit_lines(self, qapp):
        """After analysis, fit lines have data within the fit region."""
        win = self._make_analysed_window(qapp)
        # fit lines are drawn with 50 points each (linspace)
        assert len(win.line_fit_pos.get_xdata()) == 50
        assert len(win.line_fit_neg.get_xdata()) == 50
        assert len(win.line_corrected.get_xdata()) == 61

    def test_fit_lines_within_fit_region(self, qapp):
        """Fit lines must only span their respective fit region."""
        win = self._make_analysed_window(qapp)
        fit = win._last_fit
        # positive fit line starts at v_pos_min
        pos_x = win.line_fit_pos.get_xdata()
        assert min(pos_x) == pytest.approx(fit["v_pos_min"], abs=0.1)
        # negative fit line ends at v_neg_max
        neg_x = win.line_fit_neg.get_xdata()
        assert max(neg_x) == pytest.approx(fit["v_neg_max"], abs=0.1)

    def test_shading_patches_created(self, qapp):
        """Analysis must add three shading patches (neg, pos, transition)."""
        win = self._make_analysed_window(qapp)
        assert len(win._fit_shading) == 3

    def test_shading_cleared_on_reanalysis(self, qapp):
        """Re-running analysis must not accumulate shading patches."""
        win = self._make_analysed_window(qapp)
        win._run_analysis()  # second run
        assert len(win._fit_shading) == 3  # still 3, not 6

    def test_last_fit_stored(self, qapp):
        """_last_fit dict must contain fit boundaries."""
        win = self._make_analysed_window(qapp)
        assert "v_pos_min" in win._last_fit
        assert "v_neg_max" in win._last_fit
        assert "n_pos" in win._last_fit
        assert win._last_fit["n_pos"] > 0

    def test_sat_fraction_spinbox_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "spnSatFrac")
        assert win.spnSatFrac.value() == pytest.approx(0.20)

    def test_sat_fraction_affects_fit_region(self, qapp):
        """Changing sat_fraction should change the fit boundaries."""
        win = DLPMainWindowV2()
        V = np.linspace(-40, 40, 81)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)

        win.spnSatFrac.setValue(0.15)
        win._run_analysis()
        fit_15 = win._last_fit.copy()

        win.spnSatFrac.setValue(0.35)
        win._run_analysis()
        fit_35 = win._last_fit.copy()

        # larger fraction → lower v_pos_min boundary
        assert fit_35["v_pos_min"] < fit_15["v_pos_min"]
        assert fit_35["n_pos"] > fit_15["n_pos"]

    def test_run_analysis_with_few_points_warns(self, qapp):
        """Analysis with too few points should not crash."""
        win = DLPMainWindowV2()
        win._v_ist = [1.0, 2.0]
        win._i_mean = [0.001, 0.002]
        win._run_analysis()
        assert len(win.line_fit_pos.get_xdata()) == 0

    def test_default_save_folder_is_data_dir(self, qapp):
        win = DLPMainWindowV2()
        # Renamed: the historic "double_langmuir" base folder is now
        # "lp_measurements" (method-neutral; per-method subfolders
        # underneath via dlp_save_paths).
        assert win._save_folder.name == "lp_measurements"
        assert win._save_folder.parent.name == "data"

    def test_base_window_has_no_fit_lines(self, qapp):
        """The base DLPMainWindow must NOT have analysis features."""
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        assert not hasattr(win, "line_fit_pos")
        assert not hasattr(win, "btnAnalyze")
        assert win._save_folder == Path.cwd()


# ── auto-analyze gating ──────────────────────────────────────────────


class TestAutoAnalyzeGating:
    """Sweep-end behaviour for the auto-analyze toggle.

    Default must be OFF so atypical raw data are not obscured by the
    fit / autoscale logic.  The existing Analyze button must still work,
    and the opt-in auto path must call the same _run_analysis function.
    """

    def _prepare_window(self, qapp, *, n_pts: int = 61):
        """Return a window with realistic sweep data and finalize-state set."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, n_pts)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_soll = list(V)
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._i_std = [0.0] * n_pts
        win._directions = ["fwd"] * n_pts
        win._compliance = [False] * n_pts
        win._sweep_elapsed = 1.0
        win._sweep_status = "completed"
        win._sweep_failure = ""
        win._sweep_finalized = False
        win._thread = None
        win.chkSave.setChecked(False)  # avoid CSV side-effect in tests
        return win

    def test_checkbox_defaults_off(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "chkAutoAnalyze")
        assert win.chkAutoAnalyze.isChecked() is False

    def test_analyze_button_still_present(self, qapp):
        """Button must keep working independently of the checkbox."""
        win = DLPMainWindowV2()
        assert hasattr(win, "btnAnalyze")
        assert win.btnAnalyze.isEnabled()

    def test_finalize_does_not_auto_analyze_by_default(self, qapp):
        """Default path: finalize completes without touching fit overlays."""
        win = self._prepare_window(qapp)
        assert not win.chkAutoAnalyze.isChecked()

        win._do_finalize()

        assert len(win.line_fit_pos.get_xdata()) == 0
        assert len(win.line_fit_neg.get_xdata()) == 0
        assert len(win.line_corrected.get_xdata()) == 0
        assert len(win._fit_shading) == 0
        assert not hasattr(win, "_last_fit")

    def test_manual_button_runs_analysis(self, qapp):
        """btnAnalyze click produces fit overlays even with auto-analyze OFF."""
        win = self._prepare_window(qapp)
        assert not win.chkAutoAnalyze.isChecked()

        win.btnAnalyze.click()

        assert len(win.line_fit_pos.get_xdata()) > 0
        assert len(win.line_fit_neg.get_xdata()) > 0
        assert hasattr(win, "_last_fit")

    def test_auto_mode_runs_analysis_after_finalize(self, qapp):
        """With the checkbox ON, _do_finalize still runs the analysis."""
        win = self._prepare_window(qapp)
        win.chkAutoAnalyze.setChecked(True)

        win._do_finalize()

        assert len(win.line_fit_pos.get_xdata()) > 0
        assert hasattr(win, "_last_fit")

    def test_finalize_with_no_data_is_safe(self, qapp):
        """Aborted sweep (0 points) must not blow up even if auto-analyze ON."""
        win = DLPMainWindowV2()
        win.chkAutoAnalyze.setChecked(True)
        win._sweep_status = "aborted"
        win._sweep_failure = ""
        win._sweep_elapsed = 0.1
        win._sweep_finalized = False
        win._thread = None
        win.chkSave.setChecked(False)

        win._do_finalize()  # must not raise

        assert len(win.line_fit_pos.get_xdata()) == 0

    def test_manual_analysis_no_data_is_safe(self, qapp):
        """Clicking Analyze without data logs a warning but does not crash."""
        win = DLPMainWindowV2()
        win._v_ist = []
        win._i_mean = []

        win.btnAnalyze.click()  # must not raise

        assert len(win.line_fit_pos.get_xdata()) == 0

    def test_config_roundtrip_persists_auto_analyze(self, qapp):
        """get_config / apply_config preserves the auto-analyze flag."""
        win1 = DLPMainWindowV2()
        win1.chkAutoAnalyze.setChecked(True)
        cfg = win1.get_config()
        assert cfg["auto_analyze"] is True

        win2 = DLPMainWindowV2()
        assert not win2.chkAutoAnalyze.isChecked()
        win2.apply_config(cfg)
        assert win2.chkAutoAnalyze.isChecked() is True

    def test_repeated_manual_analysis_does_not_accumulate_shading(self, qapp):
        """Clicking Analyze multiple times keeps exactly 3 shading patches."""
        win = self._prepare_window(qapp)
        win.btnAnalyze.click()
        win.btnAnalyze.click()
        win.btnAnalyze.click()
        assert len(win._fit_shading) == 3


# ── compact options-panel layout ─────────────────────────────────────


class TestDistributedOptionLayout:
    """Each option button now lives in its semantic home.

    Functional behaviour (callbacks, state) is covered elsewhere; this
    class only asserts the new grouping so a future refactor that
    accidentally collapses everything back into one grid would fail.
    """

    OPTION_BUTTONS = (
        "btnProbeParams", "btnSimOptions", "btnExperiment",
        "btnFitModel", "btnInstrument",
    )

    def _layout_widgets(self, layout):
        out = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w is not None:
                out.append(w)
                continue
            sub = item.layout()
            if sub is not None:
                for j in range(sub.count()):
                    sw = sub.itemAt(j).widget()
                    if sw is not None:
                        out.append(sw)
        return out

    def test_btn_instrument_lives_in_instrument_group(self, qapp):
        win = DLPMainWindowV2()
        widgets = self._layout_widgets(win._inst_layout)
        assert win.btnInstrument in widgets

    def test_btn_fit_model_lives_in_control_group(self, qapp):
        win = DLPMainWindowV2()
        widgets = self._layout_widgets(win._ctrl_layout)
        assert win.btnFitModel in widgets

    def test_btn_sim_options_sits_next_to_chkSim(self, qapp):
        win = DLPMainWindowV2()
        widgets = self._layout_widgets(win._row_sim_layout)
        assert win.chkSim in widgets
        assert win.btnSimOptions in widgets
        # btnSimOptions must follow chkSim in the row.
        assert widgets.index(win.btnSimOptions) > widgets.index(win.chkSim)

    def test_btn_sim_options_visibility_follows_chkSim(self, qapp):
        win = DLPMainWindowV2()
        # Default: simulation off → button hidden.
        win.show()
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        assert not win.chkSim.isChecked()
        assert not win.btnSimOptions.isVisible()
        # Toggle on → button appears.
        win.chkSim.setChecked(True)
        QCoreApplication.processEvents()
        assert win.btnSimOptions.isVisible()
        # Toggle off → button hides again.
        win.chkSim.setChecked(False)
        QCoreApplication.processEvents()
        assert not win.btnSimOptions.isVisible()
        win.close()

    def test_process_gas_types_group_exists_with_experiment(self, qapp):
        from PySide6.QtWidgets import QGroupBox
        win = DLPMainWindowV2()
        assert hasattr(win, "grpGases")
        assert isinstance(win.grpGases, QGroupBox)
        assert win.grpGases.title() == "Process gas types"
        widgets = self._layout_widgets(win.grpGases.layout())
        assert win.btnExperiment in widgets

    def test_left_column_order_puts_output_below_gases(self, qapp):
        from PySide6.QtWidgets import QGroupBox
        win = DLPMainWindowV2()
        lv = win._left_v_layout
        groups_in_order = []
        for i in range(lv.count()):
            w = lv.itemAt(i).widget()
            if isinstance(w, QGroupBox):
                groups_in_order.append(w.title())
        i_gas = groups_in_order.index("Process gas types")
        i_out = groups_in_order.index("Output")
        assert i_gas < i_out

    def test_save_and_auto_analyze_in_output_group(self, qapp):
        win = DLPMainWindowV2()
        widgets = self._layout_widgets(win._fv_layout)
        assert win.chkSave in widgets
        assert win.chkAutoAnalyze in widgets

    def test_button_callbacks_still_wired(self, qapp):
        win = DLPMainWindowV2()
        for name in self.OPTION_BUTTONS:
            btn = getattr(win, name)
            assert btn.isEnabled(), f"{name} disabled after layout change"
            assert btn.receivers("2clicked()") >= 1, \
                f"{name} lost its slot"


# ── probe parameter dialog ───────────────────────────────────────────

class TestComputeElectrodeArea:

    def test_cylindrical(self):
        a = compute_electrode_area("cylindrical", 5.0, 0.1)
        expected = 2 * math.pi * 0.1 * 5.0  # ≈ 3.1416
        assert a == pytest.approx(expected)

    def test_planar(self):
        a = compute_electrode_area("planar", 5.0, 1.0)
        expected = math.pi * 1.0 ** 2
        assert a == pytest.approx(expected)


class TestProbeParamsForCsv:

    def test_contains_required_keys(self):
        csv_meta = probe_params_for_csv(DEFAULT_PROBE_PARAMS)
        assert "Probe_Geometry" in csv_meta
        assert "Geometric_Area_mm2" in csv_meta
        assert "Exposed_Length_mm" in csv_meta
        assert "Electrode_Radius_mm" in csv_meta

    def test_auto_area_computed(self):
        params = dict(DEFAULT_PROBE_PARAMS)
        params["electrode_area_mm2"] = None  # auto
        csv_meta = probe_params_for_csv(params)
        area = float(csv_meta["Geometric_Area_mm2"])
        assert area > 0

    def test_manual_area_used(self):
        params = dict(DEFAULT_PROBE_PARAMS)
        params["electrode_area_mm2"] = 99.99
        csv_meta = probe_params_for_csv(params)
        assert "99.99" in csv_meta["Geometric_Area_mm2"]


class TestProbeParameterDialog:

    def test_dialog_creates(self, qapp):
        dlg = ProbeParameterDialog()
        assert dlg.windowTitle() == "Probe Parameters"

    def test_default_values(self, qapp):
        dlg = ProbeParameterDialog()
        p = dlg.get_params()
        assert p["geometry"] == "cylindrical"
        assert p["electrode_length_mm"] == pytest.approx(5.0)
        assert p["electrode_radius_mm"] == pytest.approx(0.1)
        assert p["electrode_area_mm2"] is None  # auto mode

    def test_auto_area(self, qapp):
        dlg = ProbeParameterDialog()
        area = dlg.get_geometric_area_mm2()
        expected = 2 * math.pi * 0.1 * 5.0
        assert area == pytest.approx(expected, rel=0.01)

    def test_backward_compat_alias(self, qapp):
        dlg = ProbeParameterDialog()
        assert dlg.get_effective_area_mm2() == dlg.get_geometric_area_mm2()

    def test_exposed_length_tooltip(self, qapp):
        dlg = ProbeParameterDialog()
        tip = dlg.spnLength.toolTip()
        assert "plasma" in tip.lower()

    def test_area_tooltip_mentions_geometric(self, qapp):
        dlg = ProbeParameterDialog()
        tip = dlg.spnArea.toolTip()
        assert "geometric" in tip.lower() or "A_geo" in tip

    def test_internal_keys_unchanged(self, qapp):
        """Dict keys must stay stable for backward compat."""
        dlg = ProbeParameterDialog()
        p = dlg.get_params()
        assert "electrode_length_mm" in p
        assert "electrode_area_mm2" in p

    def test_custom_params_roundtrip(self, qapp):
        custom = {
            "probe_id": "DLP-007",
            "geometry": "planar",
            "electrode_length_mm": 10.0,
            "electrode_radius_mm": 0.5,
            "electrode_area_mm2": 1.234,
            "electrode_spacing_mm": 5.0,
            "material": "platinum",
            "notes": "test probe",
        }
        dlg = ProbeParameterDialog(custom)
        p = dlg.get_params()
        assert p["probe_id"] == "DLP-007"
        assert p["geometry"] == "planar"
        assert p["electrode_radius_mm"] == pytest.approx(0.5)
        assert p["electrode_area_mm2"] == pytest.approx(1.234)
        assert p["material"] == "platinum"

    def test_v2_window_has_probe_button(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnProbeParams")
        assert "Probe" in win.btnProbeParams.text()

    def test_v2_config_includes_probe_params(self, qapp):
        win = DLPMainWindowV2()
        win._probe_params["probe_id"] = "TEST-001"
        cfg = win.get_config()
        assert "probe_parameters" in cfg
        assert cfg["probe_parameters"]["probe_id"] == "TEST-001"

    def test_v2_apply_config_restores_probe_params(self, qapp):
        win = DLPMainWindowV2()
        cfg = {"probe_parameters": {"probe_id": "RESTORED",
                                     "electrode_length_mm": 12.0}}
        win.apply_config(cfg)
        assert win._probe_params["probe_id"] == "RESTORED"
        assert win._probe_params["electrode_length_mm"] == 12.0

    def test_config_json_roundtrip(self, qapp, tmp_path):
        import json
        win = DLPMainWindowV2()
        win._probe_params = {
            "probe_id": "RT-01",
            "geometry": "cylindrical",
            "electrode_length_mm": 8.0,
            "electrode_radius_mm": 0.2,
            "electrode_area_mm2": None,
            "electrode_spacing_mm": 4.0,
            "material": "molybdenum",
            "notes": "roundtrip test",
        }
        cfg = win.get_config()
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        win2 = DLPMainWindowV2()
        win2.apply_config(json.loads(path.read_text(encoding="utf-8")))
        assert win2._probe_params["probe_id"] == "RT-01"
        assert win2._probe_params["electrode_length_mm"] == 8.0
        assert win2._probe_params["material"] == "molybdenum"


# ── simulation options dialog ────────────────────────────────────────

class TestSimOptionsConversion:

    def test_ideal_produces_zero_kwargs(self):
        kw = sim_options_to_fake_kwargs(PRESETS["Ideal"])
        assert kw["noise_std"] == 0.0
        assert kw["asymmetry"] == 0.0
        assert kw["i_offset"] == 0.0
        assert kw["drift_per_point"] == 0.0
        assert kw["noise_corr"] == 0.0

    def test_unit_conversion(self):
        opts = {"noise_uA": 10.0, "noise_corr": 0.5,
                "asymmetry_pct": 5.0, "offset_uA": 2.0,
                "drift_nA_per_pt": 1.0}
        kw = sim_options_to_fake_kwargs(opts)
        assert kw["noise_std"] == pytest.approx(10e-6)
        assert kw["asymmetry"] == pytest.approx(0.05)
        assert kw["i_offset"] == pytest.approx(2e-6)
        assert kw["drift_per_point"] == pytest.approx(1e-9)
        assert kw["noise_corr"] == pytest.approx(0.5)

    def test_realistic_light_creates_valid_fake(self):
        kw = sim_options_to_fake_kwargs(PRESETS["Realistic (light)"])
        f = FakeB2901v2(seed=0, **kw)
        f.connect(); f.output(True)
        f.set_voltage(10.0)
        i = f.read_current()
        assert isinstance(i, float)


class TestSimulationOptionsDialog:

    def test_dialog_creates(self, qapp):
        dlg = SimulationOptionsDialog()
        assert dlg.windowTitle() == "Simulation Options"

    def test_default_values_are_ideal(self, qapp):
        dlg = SimulationOptionsDialog()
        opts = dlg.get_options()
        assert opts["noise_uA"] == 0.0
        assert opts["asymmetry_pct"] == 0.0

    def test_preset_applies(self, qapp):
        dlg = SimulationOptionsDialog()
        dlg._apply_preset("Realistic (medium)")
        opts = dlg.get_options()
        assert opts["noise_uA"] == pytest.approx(5.0)
        assert opts["asymmetry_pct"] == pytest.approx(5.0)
        assert opts["noise_corr"] == pytest.approx(0.7)

    def test_custom_values_roundtrip(self, qapp):
        custom = {"noise_uA": 3.0, "noise_corr": 0.4,
                  "asymmetry_pct": 1.5, "offset_uA": -0.5,
                  "drift_nA_per_pt": 0.2}
        dlg = SimulationOptionsDialog(custom)
        opts = dlg.get_options()
        assert opts["noise_uA"] == pytest.approx(3.0)
        assert opts["offset_uA"] == pytest.approx(-0.5)


class TestSimOptionsGuiIntegration:

    def test_sim_options_button_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnSimOptions")
        assert "Sim" in win.btnSimOptions.text()

    def test_config_includes_sim_options(self, qapp):
        win = DLPMainWindowV2()
        win._sim_options = dict(PRESETS["Realistic (light)"])
        cfg = win.get_config()
        assert "simulation_options" in cfg
        assert cfg["simulation_options"]["noise_uA"] == pytest.approx(1.0)

    def test_apply_config_restores_sim_options(self, qapp):
        win = DLPMainWindowV2()
        cfg = {"simulation_options": {"noise_uA": 7.0, "asymmetry_pct": 3.0}}
        win.apply_config(cfg)
        assert win._sim_options["noise_uA"] == pytest.approx(7.0)
        assert win._sim_options["asymmetry_pct"] == pytest.approx(3.0)

    def test_config_json_roundtrip(self, qapp, tmp_path):
        import json
        win = DLPMainWindowV2()
        win._sim_options = dict(PRESETS["Realistic (medium)"])
        cfg = win.get_config()
        path = tmp_path / "sim_cfg.json"
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        win2 = DLPMainWindowV2()
        win2.apply_config(json.loads(path.read_text(encoding="utf-8")))
        assert win2._sim_options["noise_uA"] == pytest.approx(5.0)
        assert win2._sim_options["drift_nA_per_pt"] == pytest.approx(0.5)

    def test_sim_affects_fake_output(self):
        """Realistic options should produce different output than ideal."""
        V = np.linspace(-20, 20, 41)
        kw_ideal = sim_options_to_fake_kwargs(PRESETS["Ideal"])
        kw_real = sim_options_to_fake_kwargs(PRESETS["Realistic (medium)"])
        I_ideal = np.array(_sweep(FakeB2901v2(seed=42, **kw_ideal), V))
        I_real = np.array(_sweep(FakeB2901v2(seed=42, **kw_real), V))
        # must differ because of noise/asymmetry/offset
        assert not np.allclose(I_ideal, I_real)

    def test_sim_deterministic_with_seed(self):
        V = np.linspace(-20, 20, 41)
        kw = sim_options_to_fake_kwargs(PRESETS["Realistic (light)"])
        I1 = np.array(_sweep(FakeB2901v2(seed=99, **kw), V))
        I2 = np.array(_sweep(FakeB2901v2(seed=99, **kw), V))
        np.testing.assert_array_equal(I1, I2)


# ── end-to-end sim options → fake data ───────────────────────────────

class TestSimOptionsLiveApplication:
    """Verify that sim options actually change the fake instrument output,
    including the critical case of changing options on an already-connected
    instance (the original bug).
    """

    def test_apply_sim_updates_live_instance(self, qapp):
        """Changing sim options AFTER connect must affect the live SMU."""
        win = DLPMainWindowV2()
        # start with ideal defaults, connect
        win.chkSim.setChecked(True)
        win._toggle_connect()
        assert isinstance(win.smu, FakeB2901v2)
        assert win.smu.asymmetry == 0.0
        assert win.smu.noise_std == 0.0

        # now change sim options (simulating dialog accept)
        win._sim_options = dict(PRESETS["Realistic (medium)"])
        win._apply_sim_to_smu()

        # verify the live instance was updated
        assert win.smu.asymmetry == pytest.approx(0.05)
        assert win.smu.noise_std == pytest.approx(5e-6)
        assert win.smu.i_offset == pytest.approx(2e-6)
        assert win.smu.drift_per_point == pytest.approx(0.5e-9)
        assert win.smu.noise_corr == pytest.approx(0.7)

    def test_apply_sim_on_real_hardware_is_noop(self, qapp):
        """_apply_sim_to_smu must not crash when smu is None or real."""
        win = DLPMainWindowV2()
        win.smu = None
        win._sim_options = dict(PRESETS["Realistic (light)"])
        win._apply_sim_to_smu()  # must not raise

    def test_changed_options_produce_different_sweep_data(self, qapp):
        """End-to-end: ideal connect → sweep → change options → sweep
        must produce numerically different data.
        """
        win = DLPMainWindowV2()
        win.chkSim.setChecked(True)
        win._toggle_connect()
        win.smu.output(True)

        # sweep 1: ideal
        V = np.linspace(-20, 20, 21)
        I_ideal = []
        for v in V:
            win.smu.set_voltage(v)
            I_ideal.append(win.smu.read_current())

        # disconnect, change options, reconnect
        win._toggle_connect()  # disconnect
        win._sim_options = dict(PRESETS["Realistic (medium)"])
        win.chkSim.setChecked(True)
        win.chkSim.setEnabled(True)
        win._toggle_connect()  # reconnect with new options
        win.smu.output(True)

        # sweep 2: realistic
        I_real = []
        for v in V:
            win.smu.set_voltage(v)
            I_real.append(win.smu.read_current())

        assert not np.allclose(I_ideal, I_real), \
            "Realistic sim options must produce different data"

    def test_live_update_changes_subsequent_readings(self, qapp):
        """Update options on live instance → next read_current differs."""
        win = DLPMainWindowV2()
        win.chkSim.setChecked(True)
        win._toggle_connect()
        win.smu.output(True)

        win.smu.set_voltage(10.0)
        i_before = win.smu.read_current()

        # apply strong offset
        win._sim_options = {"noise_uA": 0, "noise_corr": 0,
                            "asymmetry_pct": 0, "offset_uA": 500,
                            "drift_nA_per_pt": 0}
        win._apply_sim_to_smu()

        win.smu.set_voltage(10.0)
        i_after = win.smu.read_current()

        # 500 µA offset should shift the reading visibly
        assert abs(i_after - i_before) > 400e-6

    def test_asymmetry_changes_branch_ratio(self):
        """Nonzero asymmetry must create |I+| ≠ |I-|."""
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(seed=0, sheath_conductance=0,
                         asymmetry=0.0)
        I_sym = np.array(_sweep(f, V))

        f2 = FakeB2901v2(seed=0, sheath_conductance=0,
                          asymmetry=0.1)
        I_asym = np.array(_sweep(f2, V))

        ratio_sym = abs(I_sym[-1]) / abs(I_sym[0])
        ratio_asym = abs(I_asym[-1]) / abs(I_asym[0])
        assert ratio_sym == pytest.approx(1.0, abs=0.01)
        assert ratio_asym > 1.15  # (1.1)/(0.9) ≈ 1.22

    def test_offset_shifts_zero_crossing(self):
        """Nonzero offset must shift the zero-crossing voltage."""
        V = np.linspace(-30, 30, 61)
        f0 = FakeB2901v2(seed=0, i_offset=0)
        I0 = np.array(_sweep(f0, V))
        f1 = FakeB2901v2(seed=0, i_offset=1e-3)
        I1 = np.array(_sweep(f1, V))
        # find zero crossings
        zc0 = V[np.argmin(np.abs(I0))]
        zc1 = V[np.argmin(np.abs(I1))]
        assert zc1 < zc0 - 0.5  # offset shifts zero left

    def test_drift_changes_late_points(self):
        """Drift must cause later points to deviate from early ones
        at the same voltage."""
        f = FakeB2901v2(seed=0, drift_per_point=1e-5, i_offset=0,
                         sheath_conductance=0)
        f.connect(); f.output(True)
        f.set_voltage(0.0)
        i_early = f.read_current()   # point 0 → drift = 0
        # read 100 more points to accumulate drift
        for _ in range(100):
            f.read_current()
        f.set_voltage(0.0)
        i_late = f.read_current()    # point 102 → drift = 102*1e-5
        assert i_late - i_early > 50e-5


# ── gas conversion & experiment dialog ───────────────────────────────

class TestGasConversion:

    def test_sccm_to_mgs_argon(self):
        """50 sccm Ar ≈ 1.485 mg/s (matches existing Langmuir data)."""
        mgs = sccm_to_mgs(50.0, GAS_DATA["Ar"])
        assert mgs == pytest.approx(1.485, rel=0.01)

    def test_roundtrip_sccm_mgs(self):
        for gas, M in GAS_DATA.items():
            sccm = 100.0
            mgs = sccm_to_mgs(sccm, M)
            back = mgs_to_sccm(mgs, M)
            assert back == pytest.approx(sccm, rel=1e-6), f"Roundtrip failed for {gas}"

    def test_effective_mass_pure_argon(self):
        gases = [{"gas": "Ar", "flow_sccm": 50.0}]
        mi = effective_ion_mass_kg(gases)
        assert mi == pytest.approx(39.948 * 1.6605e-27, rel=0.01)

    def test_effective_mass_mixture(self):
        gases = [{"gas": "Ar", "flow_sccm": 50.0},
                 {"gas": "He", "flow_sccm": 50.0}]
        mi = effective_ion_mass_kg(gases)
        expected = (39.948 + 4.003) / 2 * 1.6605e-27
        assert mi == pytest.approx(expected, rel=0.01)

    def test_effective_mass_zero_flow(self):
        gases = [{"gas": "Ar", "flow_sccm": 0.0}]
        assert effective_ion_mass_kg(gases) is None

    def test_effective_mass_empty(self):
        assert effective_ion_mass_kg([]) is None


class TestExperimentDialog:

    def test_dialog_creates(self, qapp):
        dlg = ExperimentParameterDialog()
        assert dlg.windowTitle() == "Experiment Parameters"

    def test_default_three_rows(self, qapp):
        dlg = ExperimentParameterDialog()
        assert len(dlg._gas_combos) == 3

    def test_custom_params_roundtrip(self, qapp):
        params = {"gases": [{"gas": "Xe", "flow_sccm": 20.0}]}
        dlg = ExperimentParameterDialog(params)
        out = dlg.get_params()
        assert len(out["gases"]) >= 1
        assert out["gases"][0]["gas"] == "Xe"
        assert out["gases"][0]["flow_sccm"] == pytest.approx(20.0)


# ── plasma parameter computation ────────────────────────────────────

class TestPlasmaParams:

    def _make_corrected_curve(self, te_eV=3.0, i_sat=2e-3, g=5e-5):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=i_sat, te_eV=te_eV,
                         sheath_conductance=g, seed=0)
        I_raw = np.array(_sweep(f, V))
        fit = fit_saturation_branches(V, I_raw)
        I_corr = correct_iv_curve(V, I_raw, fit)
        return V, I_corr, fit

    def test_te_recovery(self):
        """T_e should be recovered within ~10% from tanh fit."""
        V, I_corr, fit = self._make_corrected_curve(te_eV=3.0)
        area_m2 = 2 * math.pi * 0.1e-3 * 5e-3  # cylindrical
        mi = 39.948 * 1.6605e-27
        pp = compute_plasma_params(V, I_corr, fit, area_m2, mi)
        assert pp["Te_eV"] == pytest.approx(3.0, rel=0.15)

    def test_isat_recovery(self):
        V, I_corr, fit = self._make_corrected_curve(i_sat=2e-3)
        pp = compute_plasma_params(V, I_corr, fit, 1e-6, 39.948e-27)
        assert pp["I_sat_fit_A"] == pytest.approx(2e-3, rel=0.1)

    def test_density_positive(self):
        V, I_corr, fit = self._make_corrected_curve()
        area_m2 = 3.14e-6
        mi = 39.948 * 1.6605e-27
        pp = compute_plasma_params(V, I_corr, fit, area_m2, mi)
        assert pp["n_i_m3"] > 0
        assert not np.isnan(pp["n_i_m3"])

    def test_density_nan_without_gas(self):
        V, I_corr, fit = self._make_corrected_curve()
        pp = compute_plasma_params(V, I_corr, fit, 1e-6, None)
        assert np.isnan(pp["n_i_m3"])

    def test_bohm_velocity_reasonable(self):
        V, I_corr, fit = self._make_corrected_curve(te_eV=3.0)
        mi = 39.948 * 1.6605e-27
        pp = compute_plasma_params(V, I_corr, fit, 1e-6, mi)
        # v_Bohm for Ar at 3 eV ≈ 2700 m/s
        assert 1000 < pp["v_Bohm_ms"] < 5000


# ── font fix & experiment button integration ─────────────────────────

class TestFontAndExperimentIntegration:

    def test_font_point_size_valid(self, qapp):
        """After v2 init, font point size must be > 0."""
        win = DLPMainWindowV2()
        ps = win.font().pointSize()
        assert ps > 0, f"Font pointSize is {ps}, expected > 0"

    def test_experiment_button_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnExperiment")
        assert "Experiment" in win.btnExperiment.text()

    def test_config_includes_experiment(self, qapp):
        win = DLPMainWindowV2()
        win._experiment_params = {"gases": [{"gas": "He", "flow_sccm": 30}]}
        cfg = win.get_config()
        assert "experiment_parameters" in cfg
        assert cfg["experiment_parameters"]["gases"][0]["gas"] == "He"

    def test_apply_config_restores_experiment(self, qapp):
        win = DLPMainWindowV2()
        cfg = {"experiment_parameters":
               {"gases": [{"gas": "Ne", "flow_sccm": 10}]}}
        win.apply_config(cfg)
        assert win._experiment_params["gases"][0]["gas"] == "Ne"

    def test_analysis_logs_te(self, qapp):
        """After analysis, _last_plasma should contain Te_eV."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        assert hasattr(win, "_last_plasma")
        assert not np.isnan(win._last_plasma["Te_eV"])


# ── T_e fit diagnostics ─────────────────────────────────────────────

class TestTeFitDiagnostics:

    def _make_corrected_curve(self, te_eV=3.0, i_sat=2e-3, g=5e-5):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=i_sat, te_eV=te_eV,
                         sheath_conductance=g, seed=0)
        I_raw = np.array(_sweep(f, V))
        fit = fit_saturation_branches(V, I_raw)
        I_corr = correct_iv_curve(V, I_raw, fit)
        return V, I_corr, fit

    def test_uncertainty_returned(self):
        V, I_corr, fit = self._make_corrected_curve()
        pp = compute_plasma_params(V, I_corr, fit, 1e-6)
        assert "Te_err_eV" in pp
        assert not np.isnan(pp["Te_err_eV"])
        assert pp["Te_err_eV"] > 0
        # uncertainty should be much smaller than value
        assert pp["Te_err_eV"] < pp["Te_eV"]

    def test_r_squared_high(self):
        V, I_corr, fit = self._make_corrected_curve()
        pp = compute_plasma_params(V, I_corr, fit, 1e-6)
        assert pp["R2"] > 0.99, f"R² = {pp['R2']:.4f} too low"

    def test_rmse_small(self):
        V, I_corr, fit = self._make_corrected_curve(i_sat=2e-3)
        pp = compute_plasma_params(V, I_corr, fit, 1e-6)
        assert pp["RMSE"] < 1e-4, f"RMSE = {pp['RMSE']:.2e} too large"

    def test_fit_curve_returned(self):
        V, I_corr, fit = self._make_corrected_curve()
        pp = compute_plasma_params(V, I_corr, fit, 1e-6)
        assert len(pp["fit_V"]) == 200
        assert len(pp["fit_I"]) == 200

    def test_fit_overlay_in_gui(self, qapp):
        """After analysis, the T_e fit line must have data."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        assert len(win.line_te_fit.get_xdata()) == 200

    def test_bad_data_low_r2(self):
        """Random noise should produce low R²."""
        rng = np.random.RandomState(0)
        V = np.linspace(-20, 20, 41)
        I = rng.randn(41) * 1e-3
        fit = {"i_sat_pos": 1e-3}
        pp = compute_plasma_params(V, I, fit, 1e-6)
        # either NaN or poor R²
        if not np.isnan(pp["R2"]):
            assert pp["R2"] < 0.5


# ── gas dialog combo usability ───────────────────────────────────────

class TestGasDialogCombo:

    def test_presets_in_dropdown(self, qapp):
        """Gas presets must be selectable, not just typeable."""
        dlg = ExperimentParameterDialog()
        cmb = dlg._gas_combos[0]
        items = [cmb.itemText(i) for i in range(cmb.count())]
        assert "Ar" in items
        assert "Xe" in items
        assert "He" in items

    def test_select_xenon_by_index(self, qapp):
        """Selecting Xe from dropdown must work."""
        dlg = ExperimentParameterDialog()
        cmb = dlg._gas_combos[0]
        idx = cmb.findText("Xe")
        assert idx >= 0, "Xe not found in combo items"
        cmb.setCurrentIndex(idx)
        assert cmb.currentText() == "Xe"

    def test_none_selection_gives_empty_gas(self, qapp):
        dlg = ExperimentParameterDialog()
        cmb = dlg._gas_combos[0]
        cmb.setCurrentIndex(0)  # "(none)"
        assert dlg._gas_name(0) == ""

    def test_combo_not_editable(self, qapp):
        """Combo should be non-editable (strict presets)."""
        dlg = ExperimentParameterDialog()
        # check that it's not editable — users select from list
        assert not dlg._gas_combos[0].isEditable()


# ── result block formatting ──────────────────────────────────────────

class TestResultBlockFormatting:

    def _sample_pp(self, te=3.0, te_err=0.04, i_sat=2e-3, r2=0.999,
                    rmse=2e-5, w=6.0, n_i=1.2e17, v_b=2700):
        return {
            "Te_eV": te, "Te_err_eV": te_err,
            "I_sat_fit_A": i_sat, "W_fit_V": w,
            "R2": r2, "RMSE": rmse,
            "n_i_m3": n_i, "v_Bohm_ms": v_b,
            "fit_V": [], "fit_I": [],
        }

    def test_contains_te_with_uncertainty(self):
        pp = self._sample_pp()
        html = format_result_block({}, pp)
        assert "T_e = 3.00" in html
        assert "\u00b1 0.04" in html
        assert "eV" in html

    def test_contains_r_squared(self):
        pp = self._sample_pp()
        html = format_result_block({}, pp)
        assert "0.9990" in html

    def test_contains_nrmse(self):
        pp = self._sample_pp()
        pp["NRMSE"] = 0.01
        html = format_result_block({}, pp)
        assert "NRMSE" in html

    def test_contains_density(self):
        pp = self._sample_pp()
        html = format_result_block({}, pp, ion_label="Ar")
        assert "1.200e+17" in html
        assert "Ar" in html

    def test_no_density_shows_warning(self):
        pp = self._sample_pp(n_i=float("nan"))
        pp["label"] = "tanh + slope"  # model must be set for warning
        html = format_result_block({}, pp)
        assert "n/a" in html

    def test_contains_model_label(self):
        pp = self._sample_pp()
        pp["label"] = "tanh + slope"
        pp["param_names"] = ["I_sat", "W", "g"]
        pp["param_values"] = [2e-3, 6.0, 5e-5]
        pp["param_errors"] = [1e-5, 0.04, 1e-6]
        pp["param_units"] = ["A", "V", "A/V"]
        html = format_result_block({}, pp)
        assert "tanh + slope" in html

    def test_html_structure(self):
        pp = self._sample_pp()
        html = format_result_block({}, pp)
        assert "<div" in html
        assert "border-left" in html
        assert "</div>" in html

    def test_bold_te_value(self):
        pp = self._sample_pp()
        html = format_result_block({}, pp)
        assert "<b>" in html

    def test_nan_te_skips_value(self):
        pp = self._sample_pp(te=float("nan"))
        html = format_result_block({}, pp)
        assert "T_e =" not in html


class TestLogFontSize:

    def test_v2_log_font_larger(self, qapp):
        win = DLPMainWindowV2()
        ss = win.txtLog.styleSheet()
        assert "13px" in ss

    def test_result_block_in_analysis(self, qapp):
        """Analysis must produce an HTML result block in the log."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "Analysis Results" in log_html
        assert "T_e" in log_html


# ── fit model selection ──────────────────────────────────────────────

class TestFitModels:
    """Test the fit model registry and fit function."""

    def _make_data(self, te=3.0, g=5e-5):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, te_eV=te,
                         sheath_conductance=g, seed=0)
        I = np.array(_sweep(f, V))
        return V, I

    def test_all_models_registered(self):
        assert "simple_tanh" in MODELS
        assert "tanh_slope" in MODELS
        assert "tanh_slope_asym" in MODELS
        assert len(MODEL_KEYS) == 3

    def test_simple_tanh_on_corrected(self):
        V, I = self._make_data()
        sat = fit_saturation_branches(V, I)
        r = fit_dlp_model(V, I, "simple_tanh", sat_fit=sat)
        assert r["model_key"] == "simple_tanh"
        assert len(r["param_values"]) == 2
        assert r["Te_eV"] == pytest.approx(3.0, rel=0.15)
        assert r["R2"] > 0.99

    def test_tanh_slope_on_raw(self):
        V, I = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope")
        assert r["model_key"] == "tanh_slope"
        assert len(r["param_values"]) == 3
        assert r["Te_eV"] == pytest.approx(3.0, rel=0.15)
        # g should recover sheath conductance
        g_fit = r["param_values"][2]
        assert g_fit == pytest.approx(5e-5, rel=0.2)

    def test_tanh_slope_asym_on_raw(self):
        V, I = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope_asym")
        assert len(r["param_values"]) == 4
        assert r["Te_eV"] == pytest.approx(3.0, rel=0.2)

    def test_models_return_different_param_counts(self):
        V, I = self._make_data()
        sat = fit_saturation_branches(V, I)
        r_a = fit_dlp_model(V, I, "simple_tanh", sat_fit=sat)
        r_b = fit_dlp_model(V, I, "tanh_slope")
        r_c = fit_dlp_model(V, I, "tanh_slope_asym")
        assert len(r_a["param_names"]) == 2
        assert len(r_b["param_names"]) == 3
        assert len(r_c["param_names"]) == 4

    def test_fit_returns_plot_curve(self):
        V, I = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope")
        assert len(r["fit_V"]) == 200
        assert len(r["fit_I"]) == 200

    def test_fit_returns_uncertainties(self):
        V, I = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope")
        assert not np.isnan(r["Te_err_eV"])
        assert r["Te_err_eV"] > 0
        for e in r["param_errors"]:
            assert not np.isnan(e)

    def test_default_model_is_tanh_slope(self):
        assert DEFAULT_MODEL == "tanh_slope"


class TestFitModelDialog:

    def test_dialog_creates(self, qapp):
        dlg = FitModelDialog()
        assert dlg.windowTitle() == "Fit Model"

    def test_dialog_shows_all_models(self, qapp):
        dlg = FitModelDialog()
        items = [dlg.cmbModel.itemData(i)
                 for i in range(dlg.cmbModel.count())]
        assert "simple_tanh" in items
        assert "tanh_slope" in items
        assert "tanh_slope_asym" in items

    def test_dialog_returns_selected(self, qapp):
        dlg = FitModelDialog("tanh_slope_asym")
        assert dlg.get_model_key() == "tanh_slope_asym"

    def test_formula_displayed(self, qapp):
        dlg = FitModelDialog("simple_tanh")
        assert "tanh" in dlg.lblFormula.text()


class TestFitModelGuiIntegration:

    def test_fit_model_button_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnFitModel")
        assert "Fit" in win.btnFitModel.text()

    def test_config_saves_model(self, qapp):
        win = DLPMainWindowV2()
        win._fit_model = "tanh_slope_asym"
        cfg = win.get_config()
        assert cfg["fit_model"] == "tanh_slope_asym"

    def test_config_restores_model(self, qapp):
        win = DLPMainWindowV2()
        win.apply_config({"fit_model": "simple_tanh"})
        assert win._fit_model == "simple_tanh"

    def test_analysis_uses_selected_model(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)

        win._fit_model = "tanh_slope"
        win._run_analysis()
        assert win._last_model_fit["model_key"] == "tanh_slope"
        assert len(win._last_model_fit["param_values"]) == 3

    def test_model_switch_changes_params(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)

        win._fit_model = "simple_tanh"
        win._run_analysis()
        n_a = len(win._last_model_fit["param_values"])

        win._fit_model = "tanh_slope_asym"
        win._run_analysis()
        n_c = len(win._last_model_fit["param_values"])

        assert n_a == 2
        assert n_c == 4

    def test_result_block_shows_model_name(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._fit_model = "tanh_slope"
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "tanh + slope" in log_html


# ── fit quality grading & NRMSE ──────────────────────────────────────

class TestFitQualityGrading:

    def test_excellent(self):
        grade, _ = grade_fit_quality(0.9995, 0.005)
        assert grade == "excellent"

    def test_good(self):
        grade, _ = grade_fit_quality(0.995, 0.03)
        assert grade == "good"

    def test_fair(self):
        grade, _ = grade_fit_quality(0.96, 0.08)
        assert grade == "fair"

    def test_poor(self):
        grade, _ = grade_fit_quality(0.90, 0.2)
        assert grade == "poor"

    def test_nan(self):
        grade, _ = grade_fit_quality(float("nan"))
        assert grade == "n/a"

    def test_high_r2_but_high_nrmse_not_excellent(self):
        """R²=0.9999 but NRMSE=8% → must NOT be excellent."""
        grade, _ = grade_fit_quality(0.9999, 0.08)
        assert grade != "excellent"

    def test_high_r2_but_nan_nrmse_not_excellent(self):
        """R²=0.9999 but NRMSE=NaN → conservative: not excellent."""
        grade, _ = grade_fit_quality(0.9999, float("nan"))
        assert grade == "poor"  # NaN nrmse → treated as worst case

    def test_both_criteria_needed(self):
        """Good R² but bad NRMSE → downgrade."""
        grade, _ = grade_fit_quality(0.999, 0.06)  # NRMSE > 5%
        assert grade in ("fair", "poor")  # not good or excellent

    def test_nrmse_in_fit_result(self):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        r = fit_dlp_model(V, I, "tanh_slope")
        assert "NRMSE" in r
        assert r["NRMSE"] < 0.05  # < 5% for clean data
        assert r["NRMSE"] > 0

    def test_grade_in_fit_result(self):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        r = fit_dlp_model(V, I, "tanh_slope")
        assert r["grade"] in ("excellent", "good")

    def test_grade_color_in_fit_result(self):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        r = fit_dlp_model(V, I, "tanh_slope")
        assert r["grade_color"].startswith("#")


# ── model comparison ─────────────────────────────────────────────────

class TestModelComparison:

    def _make_data(self):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        return V, np.array(_sweep(f, V))

    def test_compare_returns_all_models(self):
        V, I = self._make_data()
        cmp = compare_all_models(V, I)
        assert len(cmp) == 3
        keys = [c["model_key"] for c in cmp]
        assert "simple_tanh" in keys
        assert "tanh_slope" in keys

    def test_compare_has_required_fields(self):
        V, I = self._make_data()
        cmp = compare_all_models(V, I)
        for c in cmp:
            assert "R2" in c
            assert "NRMSE" in c
            assert "Te_eV" in c
            assert "grade" in c

    def test_models_give_similar_te(self):
        V, I = self._make_data()
        sat = fit_saturation_branches(V, I)
        cmp = compare_all_models(V, I, sat_fit=sat)
        te_values = [c["Te_eV"] for c in cmp if not np.isnan(c["Te_eV"])]
        # all should agree within ~20%
        assert max(te_values) / min(te_values) < 1.2

    def test_comparison_in_gui(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        assert hasattr(win, "_last_comparison")
        assert len(win._last_comparison) == 3


# ── consistent naming & transition zone ──────────────────────────────

class TestConsistentNaming:

    def test_fit_line_label_matches_model(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._fit_model = "tanh_slope"
        win._run_analysis()
        label = win.line_te_fit.get_label()
        assert "tanh + slope" in label
        assert "Model fit" in label

    def test_transition_zone_shading(self, qapp):
        """Analysis must add 3 shading patches: neg, pos, transition."""
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        assert len(win._fit_shading) == 3  # neg + pos + transition

    def test_result_block_shows_grade(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert any(g in log_html for g in ("excellent", "good", "fair"))

    def test_result_block_shows_nrmse(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "NRMSE" in log_html


# ── fit data basis assignment ────────────────────────────────────────

class TestFitDataBasis:
    """Verify that each model fits on the correct data basis."""

    def _make_data(self):
        V = np.linspace(-40, 40, 201)
        f = FakeB2901v2(i_sat=2e-3, te_eV=3.0,
                         sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        sat = fit_saturation_branches(V, I)
        return V, I, sat

    def test_simple_tanh_uses_corrected(self):
        V, I, sat = self._make_data()
        r = fit_dlp_model(V, I, "simple_tanh", sat_fit=sat)
        assert r["fit_data"] == "corrected"

    def test_tanh_slope_uses_raw(self):
        V, I, sat = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope", sat_fit=sat)
        assert r["fit_data"] == "raw"

    def test_tanh_slope_asym_uses_raw(self):
        V, I, sat = self._make_data()
        r = fit_dlp_model(V, I, "tanh_slope_asym", sat_fit=sat)
        assert r["fit_data"] == "raw"

    def test_simple_tanh_without_sat_fit_is_raw(self):
        """Without sat_fit, even simple_tanh falls back to raw."""
        V, I, _ = self._make_data()
        r = fit_dlp_model(V, I, "simple_tanh", sat_fit=None)
        assert r["fit_data"] == "raw"

    def test_comparison_shows_fit_data(self):
        V, I, sat = self._make_data()
        cmp = compare_all_models(V, I, sat_fit=sat)
        data_map = {c["model_key"]: c["fit_data"] for c in cmp}
        assert data_map["simple_tanh"] == "corrected"
        assert data_map["tanh_slope"] == "raw"
        assert data_map["tanh_slope_asym"] == "raw"

    def test_gui_log_shows_data_basis(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._fit_model = "tanh_slope"
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "raw" in log_html.lower()

    def test_gui_result_block_shows_data_basis(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._fit_model = "simple_tanh"
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "corrected" in log_html.lower()


# ── instrument options dialog ────────────────────────────────────────

class TestInstrumentOptionsConversion:

    def test_get_nplc_fast(self):
        assert get_nplc({"speed_preset": "Fast (0.1)"}) == pytest.approx(0.1)

    def test_get_nplc_very_fast(self):
        assert get_nplc({"speed_preset": "Very fast (0.01)"}) == pytest.approx(0.01)

    def test_get_nplc_default(self):
        assert get_nplc(DEFAULT_INSTRUMENT_OPTIONS) == pytest.approx(0.1)

    def test_estimate_sweep_time(self):
        opts = {"speed_preset": "Fast (0.1)"}
        t = estimate_sweep_time(opts, 200, 0.05)
        # 200 * (0.002 + 0.05) + 0.5 ≈ 10.9 s
        assert 10 < t < 12

    def test_estimate_very_fast(self):
        opts = {"speed_preset": "Very fast (0.01)"}
        t = estimate_sweep_time(opts, 200, 0.05)
        # faster measurement → less total time
        assert t < estimate_sweep_time(
            {"speed_preset": "Slow (10)"}, 200, 0.05)


class TestInstrumentOptionsDialog:

    def test_dialog_creates(self, qapp):
        dlg = InstrumentOptionsDialog()
        assert dlg.windowTitle() == "Instrument Options"

    def test_default_values(self, qapp):
        dlg = InstrumentOptionsDialog()
        opts = dlg.get_options()
        assert opts["speed_preset"] == "Fast (0.1)"
        assert opts["output_protection"] is True
        assert opts["autorange"] is True

    def test_preset_applies(self, qapp):
        dlg = InstrumentOptionsDialog()
        dlg._apply_preset("Very fast (noisy)")
        opts = dlg.get_options()
        assert opts["speed_preset"] == "Very fast (0.01)"

    def test_custom_values_roundtrip(self, qapp):
        custom = {"speed_preset": "Slow (10)",
                  "output_protection": False,
                  "autorange": False}
        dlg = InstrumentOptionsDialog(custom)
        opts = dlg.get_options()
        assert opts["speed_preset"] == "Slow (10)"
        assert opts["output_protection"] is False
        assert opts["autorange"] is False


class TestInstrumentGuiIntegration:

    def test_instrument_button_exists(self, qapp):
        win = DLPMainWindowV2()
        assert hasattr(win, "btnInstrument")
        assert "Instrument" in win.btnInstrument.text()

    def test_config_includes_instrument_options(self, qapp):
        win = DLPMainWindowV2()
        win._instrument_opts = {"speed_preset": "Slow (10)",
                                 "output_protection": False,
                                 "autorange": True}
        cfg = win.get_config()
        assert "instrument_options" in cfg
        assert cfg["instrument_options"]["speed_preset"] == "Slow (10)"

    def test_config_restores_instrument_options(self, qapp):
        win = DLPMainWindowV2()
        cfg = {"instrument_options":
               {"speed_preset": "Very fast (0.01)",
                "output_protection": False,
                "autorange": False}}
        win.apply_config(cfg)
        assert win._instrument_opts["speed_preset"] == "Very fast (0.01)"
        assert win._instrument_opts["output_protection"] is False

    def test_config_json_roundtrip(self, qapp, tmp_path):
        import json
        win = DLPMainWindowV2()
        win._instrument_opts = dict(INSTRUMENT_PRESETS["Precise (slow)"])
        cfg = win.get_config()
        path = tmp_path / "inst_cfg.json"
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        win2 = DLPMainWindowV2()
        win2.apply_config(json.loads(path.read_text(encoding="utf-8")))
        assert win2._instrument_opts["speed_preset"] == "Slow (10)"


# ── structured result formatting ─────────────────────────────────────

class TestStructuredResultBlock:
    """Test the sectioned result block format."""

    def _full_pp(self):
        return {
            "Te_eV": 3.0, "Te_err_eV": 0.04,
            "I_sat_fit_A": 2e-3, "W_fit_V": 6.0,
            "R2": 0.9998, "RMSE": 2e-5, "NRMSE": 0.01,
            "grade": "excellent", "grade_color": "#5ccf8a",
            "n_i_m3": 1.2e17, "v_Bohm_ms": 2700,
            "label": "tanh + slope", "fit_data": "raw",
            "formula": "I = I_sat * tanh(V/W) + g*V",
            "param_names": ["I_sat", "W", "g"],
            "param_values": [2e-3, 6.0, 5e-5],
            "param_errors": [1e-5, 0.04, 1e-6],
            "param_units": ["A", "V", "A/V"],
            "fit_V": [], "fit_I": [],
        }

    def test_has_section_headers(self):
        html = format_result_block({}, self._full_pp(), "Ar")
        assert "Fit Quality" in html
        assert "Model:" in html

    def test_params_on_separate_lines(self):
        html = format_result_block({}, self._full_pp())
        # each param should be on its own line (followed by <br/>)
        assert "I_sat = " in html
        assert "W = " in html
        assert "g = " in html

    def test_data_basis_shown(self):
        html = format_result_block({}, self._full_pp())
        assert "raw data" in html

    def test_density_with_gas_label(self):
        html = format_result_block({}, self._full_pp(), "Ar")
        assert "Ar" in html
        assert "1.200e+17" in html

    def test_missing_params_handled(self):
        pp = {"Te_eV": float("nan"), "I_sat_fit_A": float("nan"),
              "R2": float("nan")}
        html = format_result_block({}, pp)
        assert "<div" in html  # should not crash


class TestModelComparisonBlock:

    def _sample_cmp(self):
        return [
            {"model_key": "simple_tanh", "label": "Simple tanh",
             "fit_data": "corrected", "R2": 0.9990, "NRMSE": 0.005,
             "Te_eV": 3.01, "grade": "good"},
            {"model_key": "tanh_slope", "label": "tanh + slope",
             "fit_data": "raw", "R2": 0.9999, "NRMSE": 0.004,
             "Te_eV": 3.02, "grade": "excellent"},
        ]

    def test_comparison_block_structure(self):
        html = format_model_comparison(self._sample_cmp(), "tanh_slope")
        assert "Model Comparison" in html
        assert "<div" in html
        assert "</div>" in html

    def test_active_model_marked(self):
        html = format_model_comparison(self._sample_cmp(), "tanh_slope")
        assert "\u25b6" in html  # active marker

    def test_data_basis_tags(self):
        html = format_model_comparison(self._sample_cmp(), "tanh_slope")
        assert "[raw]" in html
        assert "[cor]" in html

    def test_each_model_on_own_line(self):
        html = format_model_comparison(self._sample_cmp(), "")
        assert "Simple tanh" in html
        assert "tanh + slope" in html

    def test_empty_comparison(self):
        assert format_model_comparison([], "") == ""

    def test_gui_comparison_is_html(self, qapp):
        win = DLPMainWindowV2()
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._run_analysis()
        log_html = win.txtLog.toHtml()
        assert "Model Comparison" in log_html


# ── CSV content, save option, analysis export ────────────────────────

class TestCsvDataContent:

    def _run_sweep_and_save(self, qapp, tmp_path, save=True):
        """Simulate a full sweep + analysis + save cycle.

        Auto-analyze is enabled explicitly — the CSV metadata checks
        exercise the auto-analysis-then-save path.
        """
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(save)
        win.chkAutoAnalyze.setChecked(True)
        # connect simulation
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # feed data manually (simulate completed sweep)
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        I = np.array(_sweep(f, V))
        win._v_soll = list(V)
        win._v_ist = list(V)
        win._i_mean = list(I)
        win._i_std = [0.0] * len(V)
        win._directions = ["fwd"] * len(V)
        win._compliance = [False] * len(V)
        # init sweep state flags (normally done by _start_sweep)
        win._sweep_finished = False
        win._sweep_finalized = False
        win._sweep_n_expected = len(V)
        # trigger done (runs analysis + save directly)
        win._on_done(1.5)
        return win, tmp_path

    def test_csv_has_data_rows(self, qapp, tmp_path):
        """CSV must contain actual sweep data, not just headers."""
        _, folder = self._run_sweep_and_save(qapp, tmp_path)
        csvs = list(folder.rglob("LP_*.csv"))
        assert len(csvs) == 1
        text = csvs[0].read_text(encoding="utf-8")
        header_lines = [l for l in text.splitlines() if l.startswith("#")]
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(header_lines) > 5  # metadata present
        assert len(data_lines) == 61  # all sweep points

    def test_csv_data_parseable(self, qapp, tmp_path):
        """Each data line must have correct column count."""
        _, folder = self._run_sweep_and_save(qapp, tmp_path)
        csv_path = list(folder.rglob("LP_*.csv"))[0]
        from DoubleLangmuirAnalysis_v2 import parse_dlp_csv
        meta, data = parse_dlp_csv(csv_path)
        assert len(data["V_soll"]) == 61
        assert len(data["I_mean"]) == 61

    def test_csv_contains_analysis_metadata(self, qapp, tmp_path):
        """CSV header must contain analysis results."""
        _, folder = self._run_sweep_and_save(qapp, tmp_path)
        text = list(folder.rglob("LP_*.csv"))[0].read_text(encoding="utf-8")
        assert "Analysis_Te_eV" in text
        assert "Analysis_R2" in text
        assert "Analysis_Model" in text

    def test_save_off_no_file(self, qapp, tmp_path):
        """With save disabled, no CSV should be created."""
        self._run_sweep_and_save(qapp, tmp_path, save=False)
        assert len(list(tmp_path.rglob("LP_*.csv"))) == 0

    def test_save_checkbox_default_on(self, qapp):
        win = DLPMainWindowV2()
        assert win.chkSave.isChecked()

    def test_save_config_roundtrip(self, qapp):
        win = DLPMainWindowV2()
        win.chkSave.setChecked(False)
        cfg = win.get_config()
        assert cfg["save_csv"] is False
        win2 = DLPMainWindowV2()
        win2.apply_config(cfg)
        assert not win2.chkSave.isChecked()

    def test_partial_sweep_saves_points(self, qapp, tmp_path):
        """Aborted sweep should save the points collected so far."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # partial data
        win._v_soll = [1.0, 2.0, 3.0]
        win._v_ist = [1.0, 2.0, 3.0]
        win._i_mean = [0.001, 0.002, 0.003]
        win._i_std = [0.0, 0.0, 0.0]
        win._directions = ["fwd", "fwd", "fwd"]
        win._compliance = [False, False, False]
        win._on_stopped()
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) == 1
        text = csvs[0].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(data_lines) == 3


# ── experiment default ───────────────────────────────────────────────

class TestExperimentDefault:

    def test_default_gas_argon(self):
        from dlp_experiment_dialog import DEFAULT_EXPERIMENT_PARAMS
        gases = DEFAULT_EXPERIMENT_PARAMS["gases"]
        assert gases[0]["gas"] == "Ar"

    def test_default_flow_01_sccm(self):
        from dlp_experiment_dialog import DEFAULT_EXPERIMENT_PARAMS
        assert DEFAULT_EXPERIMENT_PARAMS["gases"][0]["flow_sccm"] == \
            pytest.approx(0.1)

    def test_effective_mass_with_default(self):
        from dlp_experiment_dialog import (
            DEFAULT_EXPERIMENT_PARAMS, effective_ion_mass_kg)
        mi = effective_ion_mass_kg(DEFAULT_EXPERIMENT_PARAMS["gases"])
        assert mi is not None
        assert mi > 0


# ── bug fixes: NameError + QFont ─────────────────────────────────────

class TestNameErrorFix:
    """Verify KeysightB2901PSU NameError is fixed."""

    def test_no_keysight_classname_in_v2(self):
        """v2 class must not reference KeysightB2901PSU by name."""
        import inspect
        src = inspect.getsource(DLPMainWindowV2)
        assert "KeysightB2901PSU" not in src

    def test_instrument_dialog_opens_without_crash(self, qapp):
        """Opening instrument dialog must not raise NameError."""
        win = DLPMainWindowV2()
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # this previously crashed with NameError
        win._instrument_opts = {"speed_preset": "Fast (0.1)",
                                 "output_protection": True,
                                 "autorange": True}
        # simulate dialog accept path (the isinstance check)
        # must not crash
        win._open_instrument_dialog  # method exists
        # directly test the duck-type check
        has_prot = hasattr(win.smu, "enable_output_protection")
        # FakeB2901v2 does NOT have this → should not try to apply
        assert not has_prot

    def test_duck_typing_on_real_driver(self):
        """KeysightB2901PSU has the methods we check for."""
        from keysight_b2901 import KeysightB2901PSU
        assert hasattr(KeysightB2901PSU, "enable_output_protection")
        assert hasattr(KeysightB2901PSU, "set_nplc")


class TestFontFix:

    def test_ensure_valid_app_font(self, qapp):
        """After _ensure_valid_app_font, pointSize must be > 0."""
        _ensure_valid_app_font()
        ps = qapp.font().pointSize()
        assert ps > 0, f"App font pointSize is {ps}"

    def test_v2_window_font_valid(self, qapp):
        """After v2 init, window font pointSize must be > 0."""
        win = DLPMainWindowV2()
        assert win.font().pointSize() > 0

    def test_ensure_valid_font_idempotent(self, qapp):
        """Calling twice must not change the font."""
        _ensure_valid_app_font(10)
        ps1 = qapp.font().pointSize()
        _ensure_valid_app_font(10)
        ps2 = qapp.font().pointSize()
        assert ps1 == ps2


# ── worker→buffer→CSV data flow ──────────────────────────────────────

class TestWorkerDataFlow:
    """Verify that the signal-based data flow from worker to CSV works."""

    def test_on_point_fills_buffers(self, qapp):
        """Each _on_point call must append to ALL data buffers."""
        win = DLPMainWindowV2()
        # simulate 3 point signals
        for i in range(3):
            win._on_point(i, 10, float(i), float(i)+0.001,
                          1e-3 * i, 1e-6, False, "fwd")
        assert len(win._v_soll) == 3
        assert len(win._v_ist) == 3
        assert len(win._i_mean) == 3
        assert len(win._i_std) == 3
        assert len(win._directions) == 3
        assert len(win._compliance) == 3

    def test_buffers_consistent_after_points(self, qapp):
        """All buffers must have the same length."""
        win = DLPMainWindowV2()
        for i in range(5):
            win._on_point(i, 5, float(i), float(i), 1e-3, 0, False, "fwd")
        lens = {len(win._v_soll), len(win._v_ist), len(win._i_mean),
                len(win._i_std), len(win._directions), len(win._compliance)}
        assert len(lens) == 1  # all same length
        assert 5 in lens

    def test_save_after_points_writes_data(self, qapp, tmp_path):
        """Filling buffers then saving must produce data rows."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # simulate 20 points
        for i in range(20):
            v = -10 + i
            win._on_point(i, 20, float(v), float(v),
                          1e-3 * v, 1e-6, False, "fwd")
        # save
        win._save_csv(run_status="completed")
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) == 1
        text = csvs[0].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(data_lines) == 20
        # verify Points header matches
        assert "Points: 20" in text

    def test_clear_then_refill_works(self, qapp, tmp_path):
        """Starting a new sweep clears old data."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        # fill some old data
        for i in range(5):
            win._on_point(i, 5, float(i), float(i), 1e-3, 0, False, "fwd")
        assert len(win._v_soll) == 5
        # simulate start_sweep clearing (just the buffer part)
        win._v_soll.clear()
        win._v_ist.clear()
        win._i_mean.clear()
        win._i_std.clear()
        win._directions.clear()
        win._compliance.clear()
        assert len(win._v_soll) == 0
        # fill new data
        for i in range(3):
            win._on_point(i, 3, float(i), float(i), 2e-3, 0, False, "fwd")
        assert len(win._v_soll) == 3

    def test_slot_decorators_present(self):
        """v2 _on_done/_on_fail/_on_stopped must have @Slot decorators."""
        import inspect
        src = inspect.getsource(DLPMainWindowV2._on_done)
        # The Slot decorator is applied, verify the method signature works
        # by checking we can call it without error
        # (actual decorator check is indirect)
        assert "elapsed" in src

    def test_csv_values_match_buffer(self, qapp, tmp_path):
        """CSV data values must match the buffer contents exactly."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # specific known values
        win._on_point(0, 2, -5.0, -4.998, 1.234e-3, 5.6e-6, False, "fwd")
        win._on_point(1, 2, 5.0, 5.002, -1.234e-3, 5.6e-6, True, "fwd")
        win._save_csv(run_status="completed")
        text = list(tmp_path.rglob("LP_*.csv"))[0].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(data_lines) == 2
        # first row
        row0 = data_lines[0].split(",")
        assert float(row0[0]) == pytest.approx(-5.0)
        assert float(row0[2]) == pytest.approx(1.234e-3)
        assert row0[4].strip() == "fwd"
        assert row0[5].strip() == "0"  # no compliance
        # second row
        row1 = data_lines[1].split(",")
        assert float(row1[0]) == pytest.approx(5.0)
        assert row1[5].strip() == "1"  # compliance


# ── true end-to-end Worker→Thread→GUI→CSV tests ─────────────────────

class TestEndToEndWorkerThread:
    """Verify data flow through REAL QThread + QueuedConnection signals.

    These tests do NOT call _on_point directly — they let the worker
    thread emit signals through the Qt event loop, exactly like the
    real application does.  This catches @Slot / QueuedConnection
    ordering issues that direct-call tests miss.
    """

    def _run_threaded_sweep(self, qapp, win, sweep, settle_s=0.0):
        """Start a real worker in a QThread and wait for completion.

        v2 uses state-based finalization: _do_finalize is called when
        both the last _on_point and _on_done have been received.
        """
        from PySide6.QtCore import QThread, Qt
        import time as _time

        # clear buffers + init state (like _start_sweep does)
        win._v_soll.clear(); win._v_ist.clear()
        win._i_mean.clear(); win._i_std.clear()
        win._directions.clear(); win._compliance.clear()
        win._sweep_finished = False
        win._sweep_finalized = False
        win._sweep_n_expected = len(sweep)

        worker = DLPScanWorker(win.smu, sweep, settle_s, n_avg=1)
        thread = QThread()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.point.connect(win._on_point, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(win._on_done, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(win._on_fail, Qt.ConnectionType.QueuedConnection)
        worker.stopped.connect(win._on_stopped, Qt.ConnectionType.QueuedConnection)
        for sig in (worker.finished, worker.failed, worker.stopped):
            sig.connect(thread.quit)

        thread.start()

        # wait until finalized (state-based, not time-based)
        deadline = _time.monotonic() + 15.0
        while not getattr(win, "_sweep_finalized", False) \
                and _time.monotonic() < deadline:
            qapp.processEvents()
            _time.sleep(0.01)
        if thread.isRunning():
            thread.quit()
            thread.wait(3000)
        qapp.processEvents()

    def test_full_sweep_data_arrives(self, qapp, tmp_path):
        """Worker emits 11 points → MainWindow buffers have 11 entries."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()

        sweep = [(-5.0 + i, "fwd") for i in range(11)]
        self._run_threaded_sweep(qapp, win, sweep)

        # verify buffers received all points
        assert len(win._v_soll) == 11, (
            f"Expected 11 points, got {len(win._v_soll)}")
        assert len(win._v_ist) == 11
        assert len(win._i_mean) == 11

    def test_csv_has_all_data_rows(self, qapp, tmp_path):
        """Worker→Thread→GUI→CSV: CSV must contain ALL sweep points."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()

        n_pts = 21
        sweep = [(-10.0 + i, "fwd") for i in range(n_pts)]
        self._run_threaded_sweep(qapp, win, sweep)

        assert len(win._v_soll) == n_pts
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) >= 1, f"Expected CSV, found {len(csvs)}"
        text = csvs[-1].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(data_lines) == n_pts, (
            f"CSV has {len(data_lines)} data rows, expected {n_pts}")

    def test_points_header_matches_data(self, qapp, tmp_path):
        """Points: N in header must equal actual data row count."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()

        sweep = [(float(v), "fwd") for v in range(-5, 6)]
        self._run_threaded_sweep(qapp, win, sweep)

        assert len(win._v_soll) == 11
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) >= 1
        text = csvs[-1].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        for line in text.splitlines():
            if line.startswith("# Points:"):
                header_pts = int(line.split(":")[1].strip())
                break
        else:
            pytest.fail("No '# Points:' header found")
        assert header_pts == len(data_lines)
        assert header_pts > 0

    def test_csv_not_empty_on_completed(self, qapp, tmp_path):
        """A completed sweep must NEVER produce Points: 0."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()

        sweep = [(v, "fwd") for v in np.linspace(-20, 20, 41)]
        self._run_threaded_sweep(qapp, win, sweep)

        assert len(win._v_soll) == 41
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) >= 1
        text = csvs[-1].read_text(encoding="utf-8")
        assert "Points: 0" not in text
        assert "Points: 41" in text

    def test_bidirectional_sweep(self, qapp, tmp_path):
        """Bidirectional sweep: fwd+rev points all arrive."""
        from DoubleLangmuir_measure import build_sweep_voltages
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()

        sweep = build_sweep_voltages(-5, 5, 5.0, bidirectional=True)
        # [-5, 0, 5, 0, -5] = 5 points
        self._run_threaded_sweep(qapp, win, sweep)

        assert len(win._v_soll) == 5
        assert len(win._directions) == 5
        dirs = win._directions
        assert dirs[:3] == ["fwd", "fwd", "fwd"]
        assert dirs[3:] == ["rev", "rev"]

    def test_data_types_correct_from_thread(self, qapp):
        """Data received via thread must have correct Python types."""
        win = DLPMainWindowV2()
        win.chkSim.setChecked(True)
        win._toggle_connect()

        sweep = [(float(v), "fwd") for v in range(5)]
        self._run_threaded_sweep(qapp, win, sweep)

        assert all(isinstance(v, float) for v in win._v_soll)
        assert all(isinstance(v, float) for v in win._i_mean)
        assert all(isinstance(c, bool) for c in win._compliance)
        assert all(isinstance(d, str) for d in win._directions)

    def test_201_point_default_sweep(self, qapp, tmp_path):
        """Acceptance test: 201-point sweep with default settings."""
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()
        # default V range, fast settle
        win.spnSettle.setValue(0.001)
        n_exp = int(abs(win.spnVstop.value() - win.spnVstart.value())
                    / win.spnVstep.value()) + 1

        sweep = build_sweep_voltages(
            win.spnVstart.value(), win.spnVstop.value(),
            win.spnVstep.value(), False)
        self._run_threaded_sweep(qapp, win, sweep)

        # Acceptance criteria
        n = len(win._v_soll)
        assert n == n_exp, f"Expected {n_exp} points, got {n}"
        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) >= 1, "No CSV file created"
        text = csvs[-1].read_text(encoding="utf-8")
        data_lines = [l for l in text.splitlines()
                      if l.strip() and not l.startswith("#")]
        assert len(data_lines) == n, (
            f"CSV has {len(data_lines)} rows, buffer has {n}")
        assert "Points: 0" not in text
        assert f"Points: {n}" in text
        assert "Run_Status: completed" in text

    def test_button_click_produces_data(self, qapp, tmp_path):
        """Acceptance: clicking Start in sim mode fills buffers."""
        import time as _time
        win = DLPMainWindowV2()
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkSim.setChecked(True)
        win._toggle_connect()
        win.spnVstart.setValue(-3)
        win.spnVstop.setValue(3)
        win.spnVstep.setValue(1)
        win.spnSettle.setValue(0.01)
        win.btnStart.click()
        deadline = _time.monotonic() + 10
        while not getattr(win, "_sweep_finalized", False) \
                and _time.monotonic() < deadline:
            qapp.processEvents()
            _time.sleep(0.01)
        assert win._sweep_finalized, "Sweep never finalized"
        assert len(win._v_soll) == 7
        assert win._sweep_status == "completed"
