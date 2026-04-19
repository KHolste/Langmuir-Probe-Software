"""Plausibility tests for the new single-probe simulation model."""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fake_b2901_v2 import FakeB2901v2


def _sweep(fake, voltages):
    """Run an IV sweep against the fake SMU."""
    fake.connect()
    fake.output(True)
    out = []
    for v in voltages:
        fake.set_voltage(v)
        out.append(fake.read_current())
    fake.close()
    return out


# ---------------------------------------------------------------------------
class TestModelRegistration:
    def test_model_listed(self):
        assert "single_probe" in FakeB2901v2._VALID_MODELS

    def test_constructable_with_defaults(self):
        f = FakeB2901v2(model="single_probe")
        assert f.model == "single_probe"
        assert f.i_ion_sat > 0
        assert f.i_electron_sat > 0
        assert f.i_electron_sat > f.i_ion_sat


# ---------------------------------------------------------------------------
class TestCurveShape:
    def _fake(self, **kw):
        # Default sheath conductance ~ 0 so the order-of-magnitude
        # tests assess only ion vs. electron saturation; individual
        # tests can override it where the slope behaviour matters.
        defaults = dict(model="single_probe", te_eV=4.0,
                        i_ion_sat=1e-5, i_electron_sat=1e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        current_compliance=10.0)
        defaults.update(kw)
        return FakeB2901v2(**defaults)

    def test_ion_saturation_branch_is_negative(self):
        f = self._fake()
        f.connect(); f.output(True); f.set_voltage(-50.0)
        i = f.read_current()
        assert i < 0
        # Magnitude in the same order as i_ion_sat (sheath term adds
        # a small negative offset because V is large negative).
        assert abs(i) >= 0.5 * f.i_ion_sat

    def test_electron_branch_dominates_at_high_v(self):
        f = self._fake()
        f.connect(); f.output(True); f.set_voltage(+50.0)
        i = f.read_current()
        assert i > 0
        # At V well above V_p the electron branch sits near i_e_sat.
        assert i >= 0.8 * f.i_electron_sat

    def test_electron_orders_of_magnitude_above_ion(self):
        f = self._fake()
        f.connect(); f.output(True)
        f.set_voltage(-50.0); ion = abs(f.read_current())
        f.set_voltage(+50.0); ele = abs(f.read_current())
        assert ele / ion >= 10.0

    def test_curve_is_monotonically_rising(self):
        f = self._fake()
        voltages = [v * 1.0 for v in range(-40, 41, 2)]
        currents = _sweep(f, voltages)
        # Each step must not decrease (allow tiny float wobble).
        for a, b in zip(currents, currents[1:]):
            assert b >= a - 1e-9, (a, b)

    def test_no_hard_jumps_anywhere(self):
        f = self._fake()
        voltages = [v * 0.5 for v in range(-100, 101)]
        currents = _sweep(f, voltages)
        # No step exceeds a generous fraction of i_electron_sat —
        # the sigmoid keeps the transition smooth.
        max_step = max(abs(b - a) for a, b in zip(currents, currents[1:]))
        assert max_step < 0.25 * f.i_electron_sat, max_step

    def test_sheath_slope_breaks_perfect_saturation(self):
        f = self._fake(sheath_conductance=1e-5)
        f.connect(); f.output(True)
        f.set_voltage(+30.0); i_30 = f.read_current()
        f.set_voltage(+60.0); i_60 = f.read_current()
        # Both are in electron saturation; the higher V must give a
        # measurably higher current via the sheath term.
        assert i_60 > i_30 + 1e-6


# ---------------------------------------------------------------------------
class TestCurrentLimitInteraction:
    def test_compliance_clips_extreme_branch(self):
        f = FakeB2901v2(model="single_probe",
                        i_electron_sat=5e-3,
                        current_compliance=1e-3)
        f.connect(); f.output(True); f.set_voltage(+50.0)
        i = f.read_current()
        # Electron branch above 1 mA → clipped to compliance.
        assert abs(i) == pytest.approx(1e-3, rel=1e-9)
        assert f.is_in_compliance()


# ---------------------------------------------------------------------------
class TestSimOptionsForwardsModel:
    """Guards the sim-dialog → FakeB2901v2 wiring contract: the
    helper that translates GUI options into constructor kwargs must
    forward the ``model`` key when present and stay silent otherwise.
    """

    def test_kwargs_include_model_when_set(self):
        from dlp_sim_dialog import sim_options_to_fake_kwargs
        kw = sim_options_to_fake_kwargs({"model": "single_probe"})
        assert kw.get("model") == "single_probe"

    def test_kwargs_omit_model_when_absent(self):
        from dlp_sim_dialog import sim_options_to_fake_kwargs
        kw = sim_options_to_fake_kwargs({})
        assert "model" not in kw


# ---------------------------------------------------------------------------
class TestElectronSaturationSlope:
    """Real single-probe IVs are not perfectly flat above V_p — the
    electron sheath expands with bias, giving a small positive
    residual slope on the electron-saturation arm.  These guards
    pin the moderate, gated slope behaviour: visible above the
    knee, absent on the ion-saturation plateau."""

    def test_arm_has_visible_positive_slope(self):
        f = FakeB2901v2(model="single_probe", current_compliance=10.0)
        f.connect(); f.output(True)
        f.set_voltage(f.v_plasma_V + 30.0); i_30 = f.read_current()
        f.set_voltage(f.v_plasma_V + 50.0); i_50 = f.read_current()
        delta = i_50 - i_30
        # Visible: ≥ 2 % of I_e_sat across the upper sweep range.
        assert delta >= 0.02 * f.i_electron_sat, (i_30, i_50, delta)

    def test_arm_remains_saturation_like(self):
        f = FakeB2901v2(model="single_probe", current_compliance=10.0)
        f.connect(); f.output(True)
        f.set_voltage(f.v_plasma_V + 30.0); i_30 = f.read_current()
        f.set_voltage(f.v_plasma_V + 50.0); i_50 = f.read_current()
        delta = i_50 - i_30
        # Moderate: ≤ 30 % of I_e_sat — must NOT look exponential.
        assert delta <= 0.30 * f.i_electron_sat, (i_30, i_50, delta)
        # Absolute current still in saturation band.
        assert 0.8 * f.i_electron_sat <= i_50 <= 1.5 * f.i_electron_sat

    def test_explicit_zero_slope_yields_flat_arm(self):
        f = FakeB2901v2(model="single_probe",
                        electron_sat_slope=0.0,
                        sheath_conductance=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        f.set_voltage(30.0); i_30 = f.read_current()
        f.set_voltage(50.0); i_50 = f.read_current()
        # No slope, no sheath → truly flat (within fp noise).
        assert abs(i_50 - i_30) < 1e-9, (i_30, i_50)

    def test_explicit_slope_overrides_default(self):
        f = FakeB2901v2(model="single_probe",
                        electron_sat_slope=5.0e-6)
        assert f.electron_sat_slope == pytest.approx(5.0e-6)

    def test_slope_does_not_lift_negative_plateau(self):
        # Gating by the Gompertz factor must keep the ion-saturation
        # branch unaffected — that was the whole reason for adding a
        # *separate* term instead of bumping sheath_conductance.
        f = FakeB2901v2(model="single_probe",
                        sheath_conductance=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        f.set_voltage(-50.0); i_neg = f.read_current()
        # Within tight margin of i_ion_sat (no sheath, no slope kick).
        assert abs(abs(i_neg) - f.i_ion_sat) <= 0.05 * f.i_ion_sat, i_neg

    def test_slope_does_not_break_asymmetry(self):
        f = FakeB2901v2(model="single_probe",
                        sheath_conductance=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        f.set_voltage(-50.0); i_neg = abs(f.read_current())
        f.set_voltage(+50.0); i_pos = abs(f.read_current())
        # Asymmetry preserved despite the new positive slope.
        assert i_pos / i_neg >= 100.0, (i_neg, i_pos)


# ---------------------------------------------------------------------------
class TestModelAwareSheathDefault:
    """The historic sheath_conductance default (5e-5) was sized for
    the double-Langmuir form (I_sat ≈ 2 mA).  Applied unchanged to
    the single-probe form (I_i_sat ≈ 5.6 µA) it would dominate the
    ion-saturation plateau by ~450× across a ±50 V sweep — exactly
    the GUI-vs-test mismatch this regression guard catches."""

    def test_single_probe_default_sheath_is_small(self):
        f = FakeB2901v2(model="single_probe")
        # Must be at least two orders of magnitude below the historic
        # double default to keep the negative plateau visually flat.
        assert f.sheath_conductance < 5.0e-7, f.sheath_conductance

    def test_double_langmuir_default_sheath_yields_clean_s_curve(self):
        # The historic 5e-5 default let the sheath term (±2.5 mA at
        # ±50 V) swamp the ±2 mA tanh saturation — Double looked
        # linear.  The new value (5e-6) keeps Double sättigungsartig
        # with a small visible slope on the saturation arms.
        f = FakeB2901v2(model="double_langmuir")
        assert f.sheath_conductance == pytest.approx(5.0e-6)
        # End-to-end: at ±50 V the curve must be dominated by i_sat
        # (≈ ±2 mA), not by the sheath term (≤ ±0.5 mA at the limit).
        f.connect(); f.output(True)
        f.set_voltage(+50.0); i_pos = f.read_current()
        f.set_voltage(-50.0); i_neg = f.read_current()
        # Total magnitude within ~25 % of i_sat — clearly saturated.
        assert 0.95 * f.i_sat <= abs(i_pos) <= 1.30 * f.i_sat, i_pos
        assert 0.95 * f.i_sat <= abs(i_neg) <= 1.30 * f.i_sat, i_neg
        # And symmetric (asymmetry default = 0).
        assert abs(abs(i_pos) - abs(i_neg)) <= 1e-6

    def test_explicit_sheath_overrides_model_default(self):
        f = FakeB2901v2(model="single_probe", sheath_conductance=1e-3)
        assert f.sheath_conductance == pytest.approx(1e-3)

    def test_explicit_zero_sheath_is_honoured(self):
        # All test fixtures rely on this — passing 0.0 must NOT be
        # silently replaced by the model default.
        f = FakeB2901v2(model="single_probe", sheath_conductance=0.0)
        assert f.sheath_conductance == 0.0

    def test_default_negative_branch_does_not_swamp_ion_sat(self):
        """End-to-end physics check: with model-aware defaults a
        sweep at -50 V must stay within a small multiple of i_ion_sat
        — the symptom that originally exposed the GUI mismatch."""
        f = FakeB2901v2(model="single_probe", current_compliance=10.0)
        f.connect(); f.output(True); f.set_voltage(-50.0)
        i_neg = f.read_current()
        # |i_neg| must be of the same order as i_ion_sat, not 100s of
        # times larger.  Allow at most 2× to leave room for the small
        # residual sheath slope.
        assert abs(i_neg) <= 2.0 * f.i_ion_sat, (i_neg, f.i_ion_sat)
        assert i_neg < 0


# ---------------------------------------------------------------------------
class TestSaturationRatio:
    """The Maxwellian Bohm flux balance fixes I_e_sat / I_i_sat ≈
    0.665 · √(m_i/m_e) — about 28 (H), 180 (Ar), 330 (Xe).  The
    backend's defaults must land in that physical range, and the
    helper must increase monotonically with the ion mass."""

    def test_default_ratio_is_in_argon_band(self):
        f = FakeB2901v2(model="single_probe")
        ratio = f.i_electron_sat / f.i_ion_sat
        # Argon target ≈ 180; allow generous spread for any future
        # tweak of the prefactor or constants.
        assert 100.0 <= ratio <= 300.0, ratio

    def test_helper_ratio_grows_with_ion_mass(self):
        r_h = FakeB2901v2._bohm_e_to_i_ratio(1.008)
        r_ar = FakeB2901v2._bohm_e_to_i_ratio(39.948)
        r_xe = FakeB2901v2._bohm_e_to_i_ratio(131.293)
        assert r_h < r_ar < r_xe
        # Sanity-check absolute magnitudes against textbook values.
        assert 20.0 <= r_h <= 35.0
        assert 150.0 <= r_ar <= 220.0
        assert 280.0 <= r_xe <= 380.0

    def test_explicit_i_ion_sat_overrides_default(self):
        f = FakeB2901v2(model="single_probe", i_ion_sat=2.0e-5)
        assert f.i_ion_sat == pytest.approx(2.0e-5)

    def test_ion_branch_much_smaller_than_electron(self):
        f = FakeB2901v2(model="single_probe", sheath_conductance=0.0)
        f.connect(); f.output(True)
        f.set_voltage(-50.0); ion = abs(f.read_current())
        f.set_voltage(+50.0); ele = abs(f.read_current())
        # Default Argon ratio: ele / ion ≈ 180.
        assert ele / ion >= 100.0, (ele, ion)


# ---------------------------------------------------------------------------
class TestFloatingPotential:
    """Zero crossing of the single-probe IV must sit at V_f, not at
    V = 0 V.  V_f is derived from the Maxwellian balance; any model
    change that breaks this relationship would silently invalidate
    every Te / n_e fit downstream, hence the tight regression
    coverage here."""

    def _fake(self, **kw):
        defaults = dict(model="single_probe", te_eV=4.0,
                        i_electron_sat=1.0e-3, i_ion_sat=5.5e-6,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        current_compliance=10.0)
        defaults.update(kw)
        return FakeB2901v2(**defaults)

    def test_v_float_property_below_v_plasma(self):
        f = self._fake()
        # I_e_sat ≫ I_i_sat ⇒ V_f must be more negative than V_p.
        assert f.v_float_V < f.v_plasma_V

    def test_v_float_argon_in_physical_range(self):
        # For Te = 4 eV with Argon-like ratio (~180): V_p − V_f ≈ 20 V.
        f = self._fake(te_eV=4.0)
        gap = f.v_plasma_V - f.v_float_V
        assert 15.0 <= gap <= 30.0, gap

    def test_v_float_shifts_one_to_one_with_v_plasma(self):
        f0 = self._fake(v_plasma_V=0.0)
        f5 = self._fake(v_plasma_V=5.0)
        assert f5.v_float_V == pytest.approx(f0.v_float_V + 5.0, abs=1e-9)

    def test_v_float_grows_with_te(self):
        # V_p − V_f scales linearly with Te (same I-ratio).
        f1 = self._fake(te_eV=2.0)
        f2 = self._fake(te_eV=4.0)
        gap1 = f1.v_plasma_V - f1.v_float_V
        gap2 = f2.v_plasma_V - f2.v_float_V
        assert gap2 == pytest.approx(2.0 * gap1, rel=1e-6)

    def test_zero_crossing_lands_at_v_float(self):
        f = self._fake()
        f.connect(); f.output(True)
        f.set_voltage(f.v_float_V)
        i = f.read_current()
        # Current at V_f must be << I_i_sat (textbook: exactly zero).
        assert abs(i) < 0.05 * f.i_ion_sat, i

    def test_zero_crossing_is_not_at_zero_volts(self):
        # The whole point of moving from sigmoid-around-0 to a
        # physics-derived V_f: the curve must NOT cross zero at 0 V.
        f = self._fake()
        f.connect(); f.output(True)
        f.set_voltage(0.0)
        i_at_0 = f.read_current()
        # At V = V_p (= 0 here) we sit on the electron-saturation
        # knee, so the current is firmly positive — far from zero.
        assert i_at_0 > 0.5 * f.i_electron_sat, i_at_0


# ---------------------------------------------------------------------------
class TestCurveShapeRefined:
    """Form-of-the-curve checks that the Gompertz model adds beyond
    the older sigmoid form: a tighter knee at V_p and a steep,
    near-exponential rise just below V_p."""

    def _fake(self, **kw):
        defaults = dict(model="single_probe", te_eV=4.0,
                        i_electron_sat=1.0e-3, i_ion_sat=5.5e-6,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        current_compliance=10.0)
        defaults.update(kw)
        return FakeB2901v2(**defaults)

    def test_knee_is_close_to_full_saturation(self):
        # Sigmoid would give 0.5; Gompertz gives 1 − e⁻¹ ≈ 0.63 at
        # V_p and ≈ 0.93 already at V_p + Te.  The latter is the
        # interesting textbook signature.
        f = self._fake()
        f.connect(); f.output(True)
        f.set_voltage(f.v_plasma_V + f.te_eV)
        i = f.read_current()
        assert i >= 0.85 * f.i_electron_sat, i

    def test_retarding_region_is_quasi_exponential(self):
        # Two points one Te apart in the retarding region must
        # differ by a factor ≈ e (well below the knee).
        f = self._fake()
        f.connect(); f.output(True)
        # Far enough below V_p so the saturation cap is irrelevant.
        v_a = f.v_plasma_V - 4.0 * f.te_eV
        v_b = f.v_plasma_V - 3.0 * f.te_eV
        f.set_voltage(v_a); i_a = f.read_current() + f.i_ion_sat
        f.set_voltage(v_b); i_b = f.read_current() + f.i_ion_sat
        # Both are positive electron contributions; ratio ≈ e.
        assert i_a > 0 and i_b > 0
        assert 2.0 <= (i_b / i_a) <= 3.5, (i_a, i_b, i_b / i_a)

    def test_no_hard_jumps_with_new_model(self):
        # Same generosity as the old sigmoid test, asserted for the
        # Gompertz form across a fine sweep grid.
        f = self._fake()
        f.connect(); f.output(True)
        voltages = [v * 0.5 for v in range(-100, 101)]
        currents = []
        for v in voltages:
            f.set_voltage(v)
            currents.append(f.read_current())
        max_step = max(abs(b - a) for a, b in zip(currents, currents[1:]))
        assert max_step < 0.25 * f.i_electron_sat, max_step


# ---------------------------------------------------------------------------
class TestSingleVsDoubleCurveShape:
    """The two IV models must look visibly different in the sweep
    range used by the GUI: Single is asymmetric (negative ion sat,
    dominant positive electron branch); Double is symmetric (tanh
    around V = 0)."""

    def _current(self, model: str, voltage: float) -> float:
        f = FakeB2901v2(model=model, te_eV=4.0,
                        i_ion_sat=1e-5, i_electron_sat=1e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True); f.set_voltage(voltage)
        return f.read_current()

    def test_single_branch_ratio_is_at_least_50(self):
        i_neg = self._current("single_probe", -50.0)
        i_pos = self._current("single_probe", +50.0)
        # Electron branch dwarfs ion branch — typical ratio ≈ 100.
        assert abs(i_pos) / abs(i_neg) >= 50.0

    def test_double_branch_ratio_is_near_unity(self):
        i_neg = self._current("double_langmuir", -50.0)
        i_pos = self._current("double_langmuir", +50.0)
        # Symmetric tanh: |i+| ≈ |i-| within a few percent.
        assert abs(abs(i_pos) - abs(i_neg)) <= 0.05 * abs(i_pos)

    def test_models_disagree_on_sign_of_neg_branch_magnitude(self):
        """Concrete head-to-head: the negative-V branch of Single
        must have a magnitude orders of magnitude smaller than the
        Double curve's at the same voltage — that visual gap is the
        whole point of the per-method model wiring."""
        i_single_neg = abs(self._current("single_probe", -50.0))
        i_double_neg = abs(self._current("double_langmuir", -50.0))
        # i_double_neg ≈ 2e-3, i_single_neg ≈ 1e-5 → ratio ≈ 200.
        assert i_double_neg / max(i_single_neg, 1e-15) >= 50.0
