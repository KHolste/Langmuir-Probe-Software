"""Tests for the Double-probe clipping guard + quantitative T_e CI.

Covers:

* clipping detection at the analysis layer (``compute_double_analysis``),
* exclude_clipped vs retained_in_fit behaviour,
* status degrade thresholds (5 % advisory, 25 % exclude-mode POOR,
  10 % include-mode POOR),
* covariance-based 95 % CI always populated when σ is finite,
* opt-in residual-bootstrap CI on clean data,
* honest "unavailable" degrade when the bootstrap cannot resolve,
* HTML and plain-text surfaces render the CI + compliance summary,
* sidecar persistence carries the new fields.

Tests intentionally do NOT exercise the GUI Qt widgets end-to-end —
the analysis layer and formatters are all pure functions / classes
with no Qt dependency.
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_fit_models import (  # noqa: E402
    FitStatus, bootstrap_te_ci_double, fit_dlp_model,
)
from dlp_double_analysis import (  # noqa: E402
    CLIPPING_ADVISORY_THRESHOLD,
    CLIPPING_DEGRADE_THRESHOLD,
    INCLUDE_ALL_DEGRADE_THRESHOLD,
    compute_double_analysis,
)


def _clean_dlp_iv(n=61, te=3.0, i_sat=1e-3):
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat * np.tanh(V / (2.0 * te))
    return V, I


def _clipped_iv(n=61, te=3.0, i_sat=1e-3, clip_fraction=0.3,
                  compliance_A=1.5e-3):
    """Return (V, I, compliance).  The ``clip_fraction`` of points
    with the largest |V| have their current pinned to the compliance
    plateau and their compliance flag set."""
    V, I = _clean_dlp_iv(n=n, te=te, i_sat=i_sat)
    order = np.argsort(-np.abs(V))  # most-saturated voltages first
    n_clip = int(round(clip_fraction * n))
    clip_idx = order[:n_clip]
    compliance = np.zeros(n, dtype=bool)
    I_out = I.copy()
    for i in clip_idx:
        I_out[i] = np.sign(V[i]) * compliance_A
        compliance[i] = True
    return V, I_out, compliance.tolist()


# ---------------------------------------------------------------------------
# Clipping summary without / with / misaligned compliance.
# ---------------------------------------------------------------------------
class TestComplianceSummary:
    def test_no_compliance_no_info(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(V, I, fit_model="simple_tanh")
        info = out["compliance_info"]
        assert info["source"] == "none"
        assert info["n_flagged"] == 0
        assert info["action"] == "n/a"

    def test_operator_provided_no_flags(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=[False] * len(V))
        info = out["compliance_info"]
        assert info["source"] == "operator_provided"
        assert info["n_flagged"] == 0

    def test_misaligned_compliance_skipped_with_warning(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=[False])  # wrong len
        assert out["compliance_info"]["source"] == "none"
        assert any("compliance length" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Exclude-clipped path.
# ---------------------------------------------------------------------------
class TestExcludeClippedMode:
    def test_small_clip_excluded_advisory_only(self):
        # ~10 % (6/61 points) — above advisory, below degrade.
        # Fit must still converge; status stays OK but an
        # operator-visible warning must be attached.
        V, I, comp = _clipped_iv(clip_fraction=0.10)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=True)
        info = out["compliance_info"]
        assert info["n_flagged"] == 6
        assert info["action"] == "excluded_from_fit"
        assert CLIPPING_ADVISORY_THRESHOLD <= info["clipped_fraction"] \
               < CLIPPING_DEGRADE_THRESHOLD
        mfit = out["model_fit"]
        assert mfit["fit_status"] == FitStatus.OK
        assert mfit["fit_warning_reason"]
        assert "excluded" in mfit["fit_warning_reason"].lower()

    def test_large_clip_degrades_to_poor(self):
        V, I, comp = _clipped_iv(clip_fraction=0.35)  # 35 % clipped
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=True)
        info = out["compliance_info"]
        assert info["action"] == "excluded_from_fit"
        assert info["clipped_fraction"] >= CLIPPING_DEGRADE_THRESHOLD
        mfit = out["model_fit"]
        assert mfit["fit_status"] == FitStatus.POOR
        reason = mfit["fit_warning_reason"] or ""
        assert "under-sampled" in reason or "unreliable" in reason

    def test_include_all_mode_clipped_included_and_degraded(self):
        V, I, comp = _clipped_iv(clip_fraction=0.20)  # 20 % clipped
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=False)
        info = out["compliance_info"]
        assert info["action"] == "retained_in_fit"
        # Above INCLUDE_ALL_DEGRADE_THRESHOLD (10 %) — must degrade.
        assert info["clipped_fraction"] >= INCLUDE_ALL_DEGRADE_THRESHOLD
        mfit = out["model_fit"]
        assert mfit["fit_status"] == FitStatus.POOR
        reason = (mfit.get("fit_warning_reason") or "").lower()
        assert "retained" in reason or "clipped" in reason

    def test_advisory_threshold_boundary(self):
        # Below advisory: no warning injected when nothing flagged.
        V, I = _clean_dlp_iv()
        comp = [False] * len(V)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=True)
        mfit = out["model_fit"]
        assert mfit["fit_status"] == FitStatus.OK
        # Warning reason may legitimately be None OR a pre-existing
        # grade-level warning from the fit itself, but no clipping
        # advisory should leak in.
        reason = mfit.get("fit_warning_reason") or ""
        assert "clipped" not in reason.lower()
        assert "excluded" not in reason.lower()


# ---------------------------------------------------------------------------
# Covariance-based CI (always-on).
# ---------------------------------------------------------------------------
class TestCovarianceCI:
    def test_ci_brackets_point_estimate(self):
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        te = r["Te_eV"]
        lo, hi = r["Te_ci95_lo_eV"], r["Te_ci95_hi_eV"]
        assert r["Te_ci_method"] == "covariance"
        assert np.isfinite(lo) and np.isfinite(hi)
        assert lo < te < hi
        # Width is related to the 1-sigma — roughly 3.92 σ.
        width = hi - lo
        assert width == pytest.approx(3.92 * r["Te_err_eV"], rel=0.05)

    def test_ci_unavailable_when_nan_sigma(self, monkeypatch):
        # Force curve_fit to return a non-finite pcov so te_err is NaN.
        import dlp_fit_models as m
        real = m.curve_fit
        def _fake(*a, **k):
            popt, _ = real(*a, **k)
            return popt, np.array([[np.nan] * len(popt)] * len(popt))
        monkeypatch.setattr(m, "curve_fit", _fake)
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        # Converged but pcov is NaN → CI must be "unavailable", not
        # a silent Gaussian with NaN bounds passed off as real.
        assert r["Te_ci_method"] == "unavailable"
        assert np.isnan(r["Te_ci95_lo_eV"])
        assert np.isnan(r["Te_ci95_hi_eV"])

    def test_nan_result_carries_unavailable_ci(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("maxfev reached")))
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["Te_ci_method"] == "unavailable"
        assert np.isnan(r["Te_ci95_lo_eV"])
        assert np.isnan(r["Te_ci95_hi_eV"])


# ---------------------------------------------------------------------------
# Bootstrap CI (opt-in).
# ---------------------------------------------------------------------------
class TestBootstrapCI:
    def test_bootstrap_brackets_point_estimate_on_clean_data(self):
        V, I = _clean_dlp_iv()
        lo, hi, n = bootstrap_te_ci_double(
            V, I, "simple_tanh", n_iters=80, seed=1)
        assert n >= 40  # majority converged
        assert lo is not None and hi is not None
        te_point = 3.0  # true value
        assert lo <= te_point + 1.5
        assert hi >= te_point - 1.5
        assert lo < hi

    def test_bootstrap_unavailable_when_base_fit_fails(self, monkeypatch):
        # With curve_fit raising, the base fit inside the bootstrap
        # helper also fails — return (None, None, 0) per contract.
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("never converges")))
        V, I = _clean_dlp_iv()
        lo, hi, n = bootstrap_te_ci_double(V, I, "simple_tanh",
                                             n_iters=20, seed=0)
        assert lo is None and hi is None
        assert n == 0

    def test_compute_double_analysis_populates_bootstrap_when_enabled(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            compliance=[False] * len(V),
            bootstrap_enabled=True, bootstrap_n_iters=60,
            bootstrap_seed=7)
        mfit = out["model_fit"]
        assert mfit["fit_status"] == FitStatus.OK
        # Either a bootstrap CI was computed, or an honest "unavailable"
        # fallback.  Never silently claim covariance as bootstrap.
        assert mfit["Te_ci_method"] in ("bootstrap", "unavailable")
        if mfit["Te_ci_method"] == "bootstrap":
            assert np.isfinite(mfit["Te_ci95_lo_eV"])
            assert np.isfinite(mfit["Te_ci95_hi_eV"])
            assert mfit["Te_ci95_lo_eV"] < mfit["Te_ci95_hi_eV"]


# ---------------------------------------------------------------------------
# Result renderers surface the CI + compliance info.
# ---------------------------------------------------------------------------
class TestRendererSurfaces:
    def test_compact_html_shows_ci_line(self):
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(r, None, None)
        assert "95% CI" in html or "CI" in html
        lo_str = f"{r['Te_ci95_lo_eV']:.3f}"
        assert lo_str in html

    def test_compact_html_shows_compliance_row(self):
        comp_info = {"source": "operator_provided", "n_total": 50,
                     "n_flagged": 5, "clipped_fraction": 0.10,
                     "action": "excluded_from_fit"}
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(r, None, None,
                                        compliance_info=comp_info)
        assert "Compliance" in html
        assert "5/50" in html
        assert "excluded" in html

    def test_compact_html_no_compliance_row_when_none(self):
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(r, None, None,
                                        compliance_info=None)
        assert "Compliance" not in html

    def test_plain_history_includes_ci_and_compliance(self):
        V, I, comp = _clipped_iv(clip_fraction=0.10)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=True)
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        # Stub container that exposes only the one attribute the
        # formatter reads; no QMainWindow construction required.
        class _Stub:
            _last_compliance_info = out["compliance_info"]
        formatter = DLPMainWindowV2._format_analysis_plain
        sat = out["fit"]
        plain = formatter(
            _Stub(), fit=sat, pp=out["plasma"],
            ion_label="", cmp=[])
        assert "T_e 95% CI" in plain
        assert "Compliance =" in plain


# ---------------------------------------------------------------------------
# Sidecar persistence carries the new fields.
# ---------------------------------------------------------------------------
class TestSidecarPersistence:
    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def test_sidecar_contains_ci_and_compliance(self, qapp, tmp_path):
        import numpy as np
        from LPmeasurement import LPMainWindow
        from analysis_options_sidecar import (
            read_sidecar, sidecar_path_for_csv,
        )
        win = LPMainWindow()
        csv = tmp_path / "LP_2026-04-19T12-00-00_double.csv"
        csv.write_text("# Langmuir Probe Measurement Export\n",
                         encoding="utf-8")
        win._last_csv_path = csv
        V, I, comp = _clipped_iv(clip_fraction=0.10)
        win._v_soll = V.tolist()
        win._v_ist = V.tolist()
        win._i_mean = I.tolist()
        win._i_std = [1e-5] * len(V)
        win._directions = ["fwd"] * len(V)
        win._compliance = comp
        try:
            win._run_analysis()
            data = read_sidecar(csv)
            assert data is not None
            summary = data.get("analysis", {})
            # CI fields present (possibly as "unavailable" — honest
            # report either way).
            assert "Te_ci_method" in summary
            # Compliance summary persisted for re-analysis audit.
            comp_in_sidecar = summary.get("compliance_info")
            assert comp_in_sidecar is not None
            assert int(comp_in_sidecar["n_flagged"]) == \
                   int(out_n_flagged := sum(comp))
        finally:
            win.close()
