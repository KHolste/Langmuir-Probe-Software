"""Tests for the explicit fit-status reporting in ``dlp_fit_models``.

Covers:

* the shape of :class:`dlp_fit_models.FitStatus`,
* backward-compat of the success path (existing result keys preserved
  and grade-based status is ``OK`` / ``POOR`` as expected),
* explicit recording of ``INSUFFICIENT_DATA`` on too-few-points input,
* explicit recording of ``BAD_INPUT`` on non-finite / shape-mismatched
  input,
* explicit recording of ``NON_CONVERGED`` when the optimiser cannot
  meet ``maxfev`` (simulated by monkey-patching ``curve_fit``),
* ``BOUNDS_ERROR`` when SciPy raises ``ValueError``,
* ``NUMERICAL_ERROR`` when an unexpected exception is captured (not
  silently swallowed into an indistinguishable NaN result),
* that the procedure-level status propagates into
  :func:`compare_all_models` per-row entries,
* that the user-visible renderers surface the failure reason
  (compact HTML in :mod:`LPmeasurement`, V2 HTML block, and the
  plain-text history record).
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

# Make the project root importable without relying on pytest config.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_fit_models import (
    FitStatus, FAILURE_STATUSES, MODELS, compare_all_models,
    fit_dlp_model,
)


def _clean_iv(te_true=3.0, i_sat_true=1e-3, n=61):
    """Generate a clean DLP I-V curve the tanh-family fits well."""
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat_true * np.tanh(V / (2.0 * te_true))
    return V, I


# ---------------------------------------------------------------------------
# Backward-compat on success path.
# ---------------------------------------------------------------------------
class TestSuccessPathBackwardCompat:
    def test_existing_keys_preserved(self):
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        # Every key that existed before the fit-status change must
        # still exist; any test / caller reading the old schema keeps
        # working.
        for key in (
            "model_key", "label", "formula", "fit_data",
            "param_names", "param_units", "param_values", "param_errors",
            "Te_eV", "Te_err_eV", "I_sat_fit_A", "W_fit_V",
            "R2", "RMSE", "NRMSE", "grade", "grade_color",
            "fit_V", "fit_I",
        ):
            assert key in r, f"success result lost key: {key}"
        assert r["Te_eV"] == pytest.approx(3.0, rel=0.15)

    def test_new_status_fields_present_and_ok(self):
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.OK
        assert r["fit_error_reason"] is None
        # warning_reason may be None or empty; either is acceptable
        # on a clean fit, but we insist it isn't a bogus non-None
        # string when the fit is genuinely fine.
        assert not r["fit_warning_reason"]


# ---------------------------------------------------------------------------
# "Poor but computed" vs. procedural failure.
# ---------------------------------------------------------------------------
class TestGradedOutcomes:
    def test_poor_but_converged_is_status_poor(self):
        # Strong additive noise forces "poor" grading without
        # breaking the optimiser.
        rng = np.random.default_rng(0)
        V = np.linspace(-30.0, 30.0, 61)
        I = 1e-3 * np.tanh(V / 6.0) + rng.normal(0.0, 6e-4, size=V.size)
        r = fit_dlp_model(V, I, "simple_tanh")
        # The fit must have run (numbers present) but be flagged.
        assert np.isfinite(r["Te_eV"])
        assert r["fit_status"] in (FitStatus.OK, FitStatus.POOR)
        if r["grade"] == "poor":
            assert r["fit_status"] == FitStatus.POOR
            assert r["fit_warning_reason"]  # operator-visible reason


# ---------------------------------------------------------------------------
# Insufficient / bad input.
# ---------------------------------------------------------------------------
class TestInputValidation:
    def test_too_few_points_is_insufficient_data(self):
        V = np.array([0.0, 1.0])
        I = np.array([0.0, 1e-3])
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.INSUFFICIENT_DATA
        assert "sample" in r["fit_error_reason"]  # count-reason message
        assert np.isnan(r["Te_eV"])
        assert r["grade"] == "n/a"

    def test_nan_in_data_is_bad_input(self):
        V, I = _clean_iv()
        I[10] = np.nan
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.BAD_INPUT
        assert "non-finite" in r["fit_error_reason"]

    def test_zero_voltage_range_is_insufficient_data(self):
        V = np.zeros(50)
        I = np.linspace(-1e-3, 1e-3, 50)
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.INSUFFICIENT_DATA
        assert "zero range" in r["fit_error_reason"]


# ---------------------------------------------------------------------------
# Procedural failure modes via targeted curve_fit monkey-patch.
# ---------------------------------------------------------------------------
class TestCurveFitFailureModes:
    def test_runtimeerror_becomes_non_converged(self, monkeypatch):
        import dlp_fit_models as m
        def _raise(*a, **k):
            raise RuntimeError("maxfev exceeded")
        monkeypatch.setattr(m, "curve_fit", _raise)
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.NON_CONVERGED
        assert "converge" in r["fit_error_reason"].lower()
        assert np.isnan(r["Te_eV"])
        assert r["grade"] == "n/a"

    def test_valueerror_becomes_bounds_error(self, monkeypatch):
        import dlp_fit_models as m
        def _raise(*a, **k):
            raise ValueError("bounds violated")
        monkeypatch.setattr(m, "curve_fit", _raise)
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["fit_status"] == FitStatus.BOUNDS_ERROR
        assert "bounds" in r["fit_error_reason"].lower() \
            or "input" in r["fit_error_reason"].lower()

    def test_unexpected_exception_captured_not_swallowed(self, monkeypatch):
        import dlp_fit_models as m
        def _raise(*a, **k):
            raise TypeError("some unexpected internal bug")
        monkeypatch.setattr(m, "curve_fit", _raise)
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        # The critical property: the exception type is still in the
        # reason, i.e. we did NOT silently collapse it to an opaque
        # NaN result.
        assert r["fit_status"] == FitStatus.NUMERICAL_ERROR
        assert "TypeError" in r["fit_error_reason"]
        assert "some unexpected internal bug" in r["fit_error_reason"]


# ---------------------------------------------------------------------------
# Status propagates through compare_all_models.
# ---------------------------------------------------------------------------
class TestCompareAllModelsStatus:
    def test_clean_data_every_row_ok(self):
        V, I = _clean_iv()
        rows = compare_all_models(V, I)
        assert len(rows) == len(MODELS)
        for row in rows:
            assert row["fit_status"] in (FitStatus.OK, FitStatus.POOR)
            # error_reason must be None on OK/POOR
            if row["fit_status"] == FitStatus.OK:
                assert row.get("fit_error_reason") is None

    def test_failure_propagates_per_row(self, monkeypatch):
        import dlp_fit_models as m
        def _raise(*a, **k):
            raise RuntimeError("maxfev exceeded in mock")
        monkeypatch.setattr(m, "curve_fit", _raise)
        V, I = _clean_iv()
        rows = compare_all_models(V, I)
        assert all(r["fit_status"] == FitStatus.NON_CONVERGED for r in rows)
        assert all(r.get("fit_error_reason") for r in rows)


# ---------------------------------------------------------------------------
# User-visible surfacing.
# ---------------------------------------------------------------------------
class TestRenderedOutputSurfacesStatus:
    """The operator must be able to *see* the status in the places
    where analysis results are displayed or persisted."""

    def test_compact_double_html_shows_failure_banner(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("maxfev reached"))
        )
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(r, None, None)
        assert "Fit failed" in html
        assert "non_converged" in html
        assert "maxfev" in html

    def test_compact_double_html_hides_banner_on_ok(self):
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(r, None, None)
        # A clean fit must not render the failure banner.
        assert "Fit failed" not in html
        assert "Fit warning" not in html

    def test_v2_result_block_shows_failure_banner(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                ValueError("bounds violated")))
        V, I = _clean_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from DoubleLangmuir_measure_v2 import format_result_block
        html = format_result_block({}, r, "")
        assert "Fit failed" in html
        assert "bounds_error" in html

    def test_plain_history_contains_status(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("maxfev reached")))
        V, I = _clean_iv()
        mfit = fit_dlp_model(V, I, "simple_tanh")

        # Extract the unbound formatter so we do not have to construct
        # a full Qt main window.  The method body does not touch self.
        # ``fit`` here is the saturation-branch dict (the formatter
        # always renders a "Fit region:" line from it) — supply the
        # four keys it reads so the failure-path branches we care
        # about run to completion.
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        formatter = DLPMainWindowV2._format_analysis_plain
        sat_stub = {"v_neg_max": -10.0, "n_neg": 20,
                    "v_pos_min": 10.0, "n_pos": 20}
        plain = formatter(None, fit=sat_stub, pp=mfit,
                           ion_label="", cmp=[])
        assert "Status" in plain and "non_converged" in plain
        assert "Failure reason" in plain
        assert "maxfev" in plain

    def test_model_comparison_html_annotates_failed_row(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("maxfev")))
        V, I = _clean_iv()
        rows = compare_all_models(V, I)
        from DoubleLangmuir_measure_v2 import format_model_comparison
        html = format_model_comparison(rows, "tanh_slope")
        # Per-row failure tag must show the reason *or* the status.
        # The reason is preferred when available (richer for triage);
        # fall back to the bare status when the row only has a code.
        assert "failed" in html.lower()
        assert "non_converged" in html or "did not converge" in html


# ---------------------------------------------------------------------------
# Failure-set constant is a closed frozenset and separates the good
# outcomes from the bad ones — protects the renderers' banner logic.
# ---------------------------------------------------------------------------
class TestFitStatusTaxonomy:
    def test_failure_statuses_disjoint_from_converged(self):
        converged = {FitStatus.OK, FitStatus.POOR, FitStatus.WARNING}
        assert not (FAILURE_STATUSES & converged), (
            "Renderers rely on converged ∩ failure == ∅")

    def test_every_module_status_is_a_string(self):
        for name in ("OK", "POOR", "WARNING", "INSUFFICIENT_DATA",
                     "BAD_INPUT", "NON_CONVERGED", "BOUNDS_ERROR",
                     "NUMERICAL_ERROR"):
            assert isinstance(getattr(FitStatus, name), str)
