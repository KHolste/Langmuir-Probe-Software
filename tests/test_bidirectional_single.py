"""Regression tests for the bidirectional-sweep bug in the
derivative-based V_p estimator.

Reproduces the `savgol_filter failed: ZeroDivisionError('float
division by zero')` symptom the operator reported on a Single-probe
bidirectional sweep and locks in the fix.

Covers:
* :func:`dlp_single_analysis._monotonize_iv` sorts + averages
  duplicates and leaves monotonic input unchanged;
* :func:`estimate_v_plasma_derivative` no longer raises the ZeroDiv
  on bidirectional data;
* :func:`analyze_single_iv` flags bidirectional input explicitly
  and still yields a usable result;
* representative monotonic single-direction data behaves identically
  to the previous release.
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
# Synthetic Single-probe I-V curves.
# ---------------------------------------------------------------------------
def _synth_single_iv(n=201, v_start=-40.0, v_stop=20.0,
                      te_eV=3.0, i_sat=1e-4, v_p=8.0):
    """Return a Single-probe I-V curve with a clearly visible knee
    at v_p suitable for the derivative V_p method to act on."""
    V = np.linspace(v_start, v_stop, n)
    # Piecewise: retarding region exponential below v_p, saturated
    # linear above, smoothed near the knee via a tanh transition.
    i_ion = -i_sat
    i_retard = i_ion + i_sat * np.exp((V - v_p) / te_eV)
    i_sat_e = i_ion + i_sat * (1.0 + 0.02 * (V - v_p))
    # Smooth transition width ~ Te/4 so the knee is prominent.
    w = te_eV / 4.0
    alpha = 0.5 * (1.0 + np.tanh((V - v_p) / w))
    I = (1.0 - alpha) * i_retard + alpha * i_sat_e
    return V, I


def _make_bidirectional(V, I):
    """Duplicate monotonic data into fwd+rev concatenation exactly
    the way DoubleLangmuir_measure.build_sweep_voltages does."""
    fwd_V = V.copy()
    fwd_I = I.copy()
    rev_V = V[-2::-1].copy()    # exclude turning point
    rev_I = I[-2::-1].copy()
    V_bi = np.concatenate([fwd_V, rev_V])
    I_bi = np.concatenate([fwd_I, rev_I])
    directions = ["fwd"] * len(fwd_V) + ["rev"] * len(rev_V)
    return V_bi, I_bi, directions


# ---------------------------------------------------------------------------
# _monotonize_iv helper.
# ---------------------------------------------------------------------------
class TestMonotonizeIV:
    def test_noop_on_monotonic_unique(self):
        from dlp_single_analysis import _monotonize_iv
        V = np.array([0.0, 1.0, 2.0, 3.0])
        I = np.array([-1e-3, -5e-4, 0.0, 5e-4])
        Vo, Io, n_merged = _monotonize_iv(V, I)
        assert n_merged == 0
        np.testing.assert_allclose(Vo, V)
        np.testing.assert_allclose(Io, I)

    def test_sort_and_merge_duplicates(self):
        from dlp_single_analysis import _monotonize_iv
        # Classic bidirectional: V_start..V_stop,V_stop-dv..V_start.
        V_fwd = np.array([0.0, 1.0, 2.0, 3.0])
        V_rev = np.array([2.0, 1.0, 0.0])
        I_fwd = np.array([0.0, 1.0, 2.0, 3.0])
        I_rev = np.array([2.5, 1.5, 0.5])  # small fwd/rev difference
        V = np.concatenate([V_fwd, V_rev])
        I = np.concatenate([I_fwd, I_rev])
        Vo, Io, n_merged = _monotonize_iv(V, I)
        # Unique V values are 0,1,2,3 → 4 groups, 3 merges.
        assert n_merged == 3
        np.testing.assert_allclose(Vo, [0.0, 1.0, 2.0, 3.0])
        np.testing.assert_allclose(
            Io, [(0.0 + 0.5) / 2, (1.0 + 1.5) / 2,
                 (2.0 + 2.5) / 2, 3.0])

    def test_mismatched_shapes_is_noop(self):
        from dlp_single_analysis import _monotonize_iv
        V = np.array([0.0, 1.0])
        I = np.array([0.0])  # wrong length
        Vo, Io, n_merged = _monotonize_iv(V, I)
        assert n_merged == 0
        np.testing.assert_allclose(Vo, V)
        np.testing.assert_allclose(Io, I)


# ---------------------------------------------------------------------------
# Regression: the actual operator-reported failure.
# ---------------------------------------------------------------------------
class TestDerivativeVpNoZeroDiv:
    def test_bidirectional_does_not_trip_savgol(self):
        from dlp_single_analysis import estimate_v_plasma_derivative
        V_mono, I_mono = _synth_single_iv(n=201)
        V_bi, I_bi, _ = _make_bidirectional(V_mono, I_mono)
        # Before the fix, this call raised
        # `savgol_filter failed: ZeroDivisionError('float division by
        # zero')`.  It must now return a clean result envelope.
        v_p, conf, status, diag = estimate_v_plasma_derivative(
            V_bi, I_bi, te=3.0, v_float=-5.0)
        # Either a usable value OR an honest "n/a" — never an
        # unhandled exception.
        assert conf in ("high", "medium", "low", "n/a")
        # The helper must record the merge in diagnostics so
        # downstream log messages and tests can assert it.
        assert diag["bidirectional_merged"] > 0
        # Status must not carry the historical ZeroDivisionError.
        assert "ZeroDivisionError" not in status

    def test_bidirectional_recovers_v_p(self):
        # On clean synthetic data with a clear knee, the derivative
        # method should still locate v_p to within ~Te/2 even after
        # the bidirectional merge.
        from dlp_single_analysis import estimate_v_plasma_derivative
        V_mono, I_mono = _synth_single_iv(
            n=401, te_eV=3.0, v_p=8.0)
        V_bi, I_bi, _ = _make_bidirectional(V_mono, I_mono)
        v_p, conf, status, diag = estimate_v_plasma_derivative(
            V_bi, I_bi, te=3.0, v_float=-5.0)
        assert v_p is not None
        assert abs(v_p - 8.0) < 1.5  # within Te/2
        # "n/a" is ruled out because our synthetic knee is strong.
        assert conf in ("high", "medium", "low")

    def test_monotonic_unchanged_behaviour(self):
        from dlp_single_analysis import estimate_v_plasma_derivative
        V, I = _synth_single_iv(n=201, te_eV=3.0, v_p=8.0)
        v_p, conf, status, diag = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=-5.0)
        assert v_p is not None
        assert diag["bidirectional_merged"] == 0
        assert abs(v_p - 8.0) < 1.5

    def test_all_same_voltage_degenerate_reports_na(self):
        # Pathological input: every sample at one voltage.  Must
        # not crash — instead report an honest "n/a".
        from dlp_single_analysis import estimate_v_plasma_derivative
        V = np.zeros(30)
        I = np.arange(30, dtype=float)
        v_p, conf, status, diag = estimate_v_plasma_derivative(
            V, I, te=3.0, v_float=-1.0)
        assert v_p is None
        assert conf == "n/a"


# ---------------------------------------------------------------------------
# Orchestrator-level behaviour.
# ---------------------------------------------------------------------------
class TestAnalyzeSingleIVBidirectional:
    def test_bidirectional_flag_and_warning(self):
        from dlp_single_analysis import analyze_single_iv
        V_mono, I_mono = _synth_single_iv(n=201, te_eV=3.0, v_p=8.0)
        V_bi, I_bi, dirs = _make_bidirectional(V_mono, I_mono)
        r = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                v_p_method="derivative")
        assert r.get("bidirectional_mode_used") is True
        # Warning string must mention the merge so operators know.
        assert any("bidirectional" in w.lower() for w in r["warnings"])
        # T_e is still computed and finite — the precise fit value
        # depends on window choices + the synthetic curve's shape; a
        # finite positive result is sufficient to confirm the
        # orchestrator is not broken by bidirectional input.
        assert r["te_eV"] is not None
        assert np.isfinite(r["te_eV"]) and r["te_eV"] > 0.0

    def test_monotonic_flag_false(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = _synth_single_iv(n=201, te_eV=3.0, v_p=8.0)
        r = analyze_single_iv(V, I, directions=None,
                                v_p_method="derivative")
        assert r.get("bidirectional_mode_used") is False
        # No bidirectional warning.
        assert not any("bidirectional" in w.lower()
                        for w in r["warnings"])

    def test_bidirectional_detected_without_directions(self):
        # Loaded from a CSV whose "dir" column was missing — the
        # orchestrator should still spot the non-monotonic V and
        # set the flag.
        from dlp_single_analysis import analyze_single_iv
        V_mono, I_mono = _synth_single_iv(n=101)
        V_bi, I_bi, _ = _make_bidirectional(V_mono, I_mono)
        r = analyze_single_iv(V_bi, I_bi, directions=None,
                                v_p_method="derivative")
        assert r.get("bidirectional_mode_used") is True


# ---------------------------------------------------------------------------
# Regression: Te inflation bug (operator report).
# ---------------------------------------------------------------------------
#
# Observed on the same simulation data:
#   - Single direction: Te ≈ 3.5 eV, R² ≈ 0.994, NRMSE ≈ 2.5 %, n = 35
#   - Bidirectional   : Te ≈ 20.9 eV, R² ≈ 0.53,  NRMSE ≈ 18.9 %, n = 222
# Root cause: initial_te_estimate sliced v_above[:n] by array position,
# which on bidirectional data spanned the entire forward leg (retarding
# + saturation), flattening the semilog slope and inflating Te.  The
# inflated seed then blew up the fit-Te window to V_f + 63 V, which
# collected every point above V_f on both branches (n=222).
#
# Fix has two layers:
#   1. initial_te_estimate now sorts by V before slicing.
#   2. analyze_single_iv canonicalises bidirectional data via
#      _monotonize_iv before running the downstream pipeline.
# ---------------------------------------------------------------------------
class TestTeInflationRegression:
    """The central scientific-correctness guard for this bug."""

    def _sim_iv(self):
        """Reproduce the kind of curve that triggered the blow-up.

        A classic single-probe curve with a clear knee + a hard
        electron-saturation plateau above v_p.  This is the
        geometry that showed the inflation pattern most clearly
        because the plateau dominates the upper half of the
        electron branch.
        """
        V = np.linspace(-30.0, 20.0, 201)
        te = 3.0
        v_p = 8.0
        i_sat = 1.0e-4
        # Retarding side: exponential toward v_p.
        i_retard = -i_sat + i_sat * np.exp((V - v_p) / te)
        # Saturation side: hard plateau with a small residual slope.
        i_sat_e = -i_sat + i_sat * (1.0 + 0.02 * (V - v_p))
        w = te / 4.0
        alpha = 0.5 * (1.0 + np.tanh((V - v_p) / w))
        I = (1.0 - alpha) * i_retard + alpha * i_sat_e
        return V, I

    def test_initial_te_estimate_no_longer_inflates(self):
        # The primitive-level fix must return a T_e close to the
        # simulated 3 eV on bidirectional input — NOT the >20 eV
        # value the un-hardened slice produced.
        from dlp_single_analysis import initial_te_estimate
        V, I = self._sim_iv()
        V_bi, I_bi, _ = _make_bidirectional(V, I)
        te_seed_mono = initial_te_estimate(V, I, v_float=-3.0)
        te_seed_bi = initial_te_estimate(V_bi, I_bi,
                                            v_float=-3.0)
        assert te_seed_mono is not None and te_seed_bi is not None
        # Difference between the two estimates must be well within
        # one simulated Te — the two regimes now agree.
        assert abs(te_seed_bi - te_seed_mono) < 1.0, (
            te_seed_mono, te_seed_bi)
        # And the bidirectional seed is nowhere near the historical
        # blow-up value.
        assert te_seed_bi < 10.0, te_seed_bi

    def test_analyze_single_iv_te_matches_between_modes(self):
        """The smoking-gun regression: same physics, two sweep
        modes, the reported T_e values must agree to within a
        fraction of an eV — not by a factor of six.
        """
        from dlp_single_analysis import analyze_single_iv
        V, I = self._sim_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r_mono = analyze_single_iv(V, I, directions=None,
                                      v_p_method="derivative")
        r_bi = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                    v_p_method="derivative")
        assert r_mono["te_eV"] is not None
        assert r_bi["te_eV"] is not None
        # Same physics → same Te within tight tolerance.  The
        # historical bug gave a ~6x delta; we now require <0.5 eV.
        assert abs(r_bi["te_eV"] - r_mono["te_eV"]) < 0.5, (
            r_mono["te_eV"], r_bi["te_eV"])
        # The fit quality on the canonicalised bidirectional sweep
        # must not be dramatically worse than the monodirectional
        # one.  The old bug produced R² ≈ 0.53 / NRMSE ≈ 19 %; we
        # lock the bidirectional path to NRMSE <= 10 %.
        assert r_bi["fit_NRMSE_te"] is not None
        assert r_bi["fit_NRMSE_te"] <= 0.10, r_bi["fit_NRMSE_te"]
        # The bidirectional merge must be recorded explicitly — not
        # hidden.
        assert r_bi["bidirectional_mode_used"] is True
        assert r_bi["n_bidirectional_merged"] > 0

    def test_analyze_single_iv_n_points_sensible(self):
        """The old bug collected n=222 points for the semilog T_e
        fit because the window ran to V_f + 63 V.  With the seed
        fixed the window is ~V_f + 3·Te ≈ ~9 V wide and n is
        similar to the monotonic case (within one sweep's worth of
        data, since coincident voltages are averaged)."""
        from dlp_single_analysis import analyze_single_iv
        V, I = self._sim_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r_mono = analyze_single_iv(V, I, directions=None,
                                      v_p_method="derivative")
        r_bi = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                    v_p_method="derivative")
        # Bidirectional n should be comparable to monodirectional,
        # NOT 5-6x larger as the historical bug produced.
        assert r_bi["fit_n_points_te"] <= r_mono["fit_n_points_te"] + 5

    def test_warning_mentions_canonicalisation(self):
        # Operator must SEE that bidirectional handling happened.
        from dlp_single_analysis import analyze_single_iv
        V, I = self._sim_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                v_p_method="derivative")
        msg = " ".join(r["warnings"]).lower()
        assert "bidirectional" in msg
        assert "canonicalised" in msg or "merged" in msg \
               or "averaged" in msg
