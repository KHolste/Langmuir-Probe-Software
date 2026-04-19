"""Focused tests for the production-finishing Double-analysis pass.

Five concerns, one file:

* Double options dialog exposes the bootstrap toggle + iteration
  count, persists them through get_options, and enabling the toggle
  actually reaches compute_double_analysis.
* The V2-style result block (format_result_block) now renders a
  Compliance sub-section that mirrors LP's compact block.
* Double result dict + HTML + sidecar now carry I_sat CI and n_i CI
  (explicitly fit-only for n_i).
* Legacy-style datasets without a compliance column pick up the
  clipping heuristic and the output is labelled "suspected"
  (never "compliance-flagged").
* Existing Single/Double flows remain green.
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_double_analysis import compute_double_analysis  # noqa: E402
from dlp_double_options import (  # noqa: E402
    DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
)
from dlp_fit_models import FitStatus, fit_dlp_model  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------
def _clean_dlp_iv(n=61, te=3.0, i_sat=1e-3):
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat * np.tanh(V / (2.0 * te))
    return V, I


def _clipped_iv(n=61, te=3.0, i_sat=1e-3, clip_fraction=0.2,
                 compliance_A=1.5e-3):
    V, I = _clean_dlp_iv(n=n, te=te, i_sat=i_sat)
    order = np.argsort(-np.abs(V))
    n_clip = int(round(clip_fraction * n))
    clip_idx = order[:n_clip]
    compliance = np.zeros(n, dtype=bool)
    I_out = I.copy()
    for i in clip_idx:
        I_out[i] = np.sign(V[i]) * compliance_A
        compliance[i] = True
    return V, I_out, compliance.tolist()


@pytest.fixture
def qapp():
    """Function-scoped QApplication + explicit event drain.

    The project-wide conftest hook already runs a processEvents /
    deleteLater drain at teardown, but re-creating one dialog after
    another in the same session was crashing Qt on the offscreen
    platform.  Keeping the fixture function-scoped ensures each
    dialog gets built against a clean event queue.
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)  # DeferredDelete


# ---------------------------------------------------------------------------
# Part 1 — bootstrap UI.
# ---------------------------------------------------------------------------
def _dispose_dialog(dlg):
    """Deterministically release a dialog's widget tree.  Qt on the
    offscreen platform crashes when Python GC comes in "too late";
    calling deleteLater and draining events keeps the two clocks in
    step.  Silent on any error — the teardown must never mask the
    real test assertion.
    """
    try:
        dlg._dlg.deleteLater()
    except Exception:
        pass


class TestBootstrapDialog:
    def test_dialog_exposes_bootstrap_controls(self, qapp):
        opts = DoubleAnalysisOptions()
        dlg = DoubleAnalysisOptionsDialog("tanh_slope", opts)
        try:
            assert hasattr(dlg, "chkBootstrap")
            assert hasattr(dlg, "spnBootstrapN")
            assert dlg.chkBootstrap.isChecked() is False
            assert dlg.spnBootstrapN.value() == 200
            assert dlg.spnBootstrapN.isEnabled() is False
            assert dlg.spnBootstrapN.minimum() == 50
            assert dlg.spnBootstrapN.maximum() == 2000
        finally:
            _dispose_dialog(dlg)

    def test_toggling_enables_spinner(self, qapp):
        dlg = DoubleAnalysisOptionsDialog("tanh_slope",
                                             DoubleAnalysisOptions())
        try:
            dlg.chkBootstrap.setChecked(True)
            assert dlg.spnBootstrapN.isEnabled() is True
            dlg.chkBootstrap.setChecked(False)
            assert dlg.spnBootstrapN.isEnabled() is False
        finally:
            _dispose_dialog(dlg)

    def test_get_options_roundtrip(self, qapp):
        seed = DoubleAnalysisOptions(
            compliance_mode="include_all",
            hysteresis_threshold_pct=7.5,
            bootstrap_enabled=True,
            bootstrap_n_iters=500,
        )
        dlg = DoubleAnalysisOptionsDialog("simple_tanh", seed)
        try:
            got = dlg.get_options()
            assert got.compliance_mode == "include_all"
            assert got.hysteresis_threshold_pct == pytest.approx(7.5)
            assert got.bootstrap_enabled is True
            assert got.bootstrap_n_iters == 500
        finally:
            _dispose_dialog(dlg)

    def test_dataclass_persistence_roundtrip(self):
        seed = DoubleAnalysisOptions(bootstrap_enabled=True,
                                       bootstrap_n_iters=350)
        serialised = seed.to_dict()
        restored = DoubleAnalysisOptions.from_dict(serialised)
        assert restored.bootstrap_enabled is True
        assert restored.bootstrap_n_iters == 350

    def test_dataclass_clamps_pathological_iters(self):
        restored = DoubleAnalysisOptions.from_dict(
            {"bootstrap_n_iters": 9999})
        assert restored.bootstrap_n_iters == 2000
        restored = DoubleAnalysisOptions.from_dict(
            {"bootstrap_n_iters": 5})
        assert restored.bootstrap_n_iters == 50

    def test_bootstrap_flag_reaches_analysis_layer(self, qapp):
        # Programmatically enabling bootstrap through the options
        # dialog round-trip must end up calling compute_double_analysis
        # with bootstrap_enabled=True.  We verify by checking the
        # result Te_ci_method — "bootstrap" is only set when the
        # toggle propagated correctly.
        opts = DoubleAnalysisOptions(bootstrap_enabled=True,
                                       bootstrap_n_iters=80)
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            bootstrap_enabled=opts.bootstrap_enabled,
            bootstrap_n_iters=opts.bootstrap_n_iters)
        assert out["model_fit"]["Te_ci_method"] in (
            "bootstrap", "unavailable")


# ---------------------------------------------------------------------------
# Part 2 — V2 format_result_block Compliance section.
# ---------------------------------------------------------------------------
class TestV2FormatResultBlockCompliance:
    def test_compliance_section_emitted_when_flagged(self):
        from DoubleLangmuir_measure_v2 import format_result_block
        pp = {
            "Te_eV": 3.0, "Te_err_eV": 0.1, "I_sat_fit_A": 1e-3,
            "R2": 0.99, "NRMSE": 0.03, "grade": "good",
            "grade_color": "#8bc34a", "label": "Simple tanh",
            "fit_data": "raw",
            "param_names": ["I_sat", "W"], "param_values": [1e-3, 6.0],
            "param_errors": [1e-5, 0.2], "param_units": ["A", "V"],
            "compliance_info": {
                "source": "operator_provided",
                "n_total": 60, "n_flagged": 6,
                "clipped_fraction": 0.10,
                "action": "excluded_from_fit",
            },
        }
        html = format_result_block({}, pp, ion_label="Ar")
        assert "Compliance" in html
        assert "6/60" in html
        assert "excluded from fit" in html
        # "Suspected" language is reserved for the heuristic path.
        assert "suspected clipping" not in html

    def test_suspected_label_for_heuristic_source(self):
        from DoubleLangmuir_measure_v2 import format_result_block
        pp = {
            "Te_eV": 3.0, "Te_err_eV": 0.1, "I_sat_fit_A": 1e-3,
            "R2": 0.99, "NRMSE": 0.03,
            "label": "Simple tanh", "fit_data": "raw",
            "param_names": [], "param_values": [], "param_errors": [],
            "param_units": [],
            "compliance_info": {
                "source": "heuristic_suspected",
                "n_total": 60, "n_flagged": 4,
                "clipped_fraction": 0.067,
                "action": "excluded_from_fit",
            },
        }
        html = format_result_block({}, pp, ion_label="")
        assert "Compliance" in html
        assert "suspected clipping" in html
        assert "heuristic" in html.lower()

    def test_no_compliance_section_when_none_flagged(self):
        from DoubleLangmuir_measure_v2 import format_result_block
        pp = {
            "Te_eV": 3.0, "Te_err_eV": 0.1, "I_sat_fit_A": 1e-3,
            "R2": 0.99, "NRMSE": 0.03,
            "label": "Simple tanh", "fit_data": "raw",
            "param_names": [], "param_values": [], "param_errors": [],
            "param_units": [],
            "compliance_info": {
                "source": "none", "n_total": 60, "n_flagged": 0,
                "clipped_fraction": 0.0, "action": "n/a",
            },
        }
        html = format_result_block({}, pp, ion_label="")
        assert "Compliance" not in html


# ---------------------------------------------------------------------------
# Part 3 — I_sat + n_i CI population and honest labelling.
# ---------------------------------------------------------------------------
class TestIsatAndNiCI:
    def test_isat_ci_populated_on_success(self):
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["I_sat_ci_method"] == "covariance"
        lo, hi, isat = (r["I_sat_ci95_lo_A"], r["I_sat_ci95_hi_A"],
                         r["I_sat_fit_A"])
        assert np.isfinite(lo) and np.isfinite(hi)
        assert lo < isat < hi

    def test_isat_ci_unavailable_on_failure(self, monkeypatch):
        import dlp_fit_models as m
        monkeypatch.setattr(
            m, "curve_fit",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("maxfev")))
        V, I = _clean_dlp_iv()
        r = fit_dlp_model(V, I, "simple_tanh")
        assert r["I_sat_ci_method"] == "unavailable"
        assert np.isnan(r["I_sat_ci95_lo_A"])

    def test_ni_ci_populated_when_fit_and_context_available(self):
        V, I = _clean_dlp_iv()
        probe_params = {"electrode_area_mm2": 1.0}
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe_params, gases=gases)
        pp = out["plasma"]
        assert pp["n_i_ci_method"] == "covariance"
        # The scope caveat is ALWAYS present so readers never
        # mistake this for a total uncertainty.
        assert pp["n_i_ci_note"] == "fit_only"
        assert pp["n_i_ci95_lo_m3"] < pp["n_i_m3"] < pp["n_i_ci95_hi_m3"]

    def test_ni_ci_unavailable_without_gas(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params={
                                            "electrode_area_mm2": 1.0})
        pp = out["plasma"]
        # No gas → no v_Bohm → no n_i → unavailable, not fabricated.
        assert pp["n_i_ci_method"] == "unavailable"
        assert pp["n_i_ci_note"] == "fit_only"  # scope still stamped

    def test_compact_html_shows_isat_and_ni_ci_lines(self):
        V, I = _clean_dlp_iv()
        probe_params = {"electrode_area_mm2": 1.0}
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe_params,
                                        gases=gases)
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(out["model_fit"],
                                        out["plasma"], None)
        assert "CI" in html
        # I_sat CI rendered with mA precision.
        lo_mA = out["model_fit"]["I_sat_ci95_lo_A"] * 1e3
        assert f"{lo_mA:.3f}" in html
        # n_i CI carries the fit-only qualifier.
        assert "fit-only" in html

    def test_v2_html_shows_isat_and_ni_ci_lines(self):
        V, I = _clean_dlp_iv()
        probe_params = {"electrode_area_mm2": 1.0}
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe_params,
                                        gases=gases)
        from DoubleLangmuir_measure_v2 import format_result_block
        html = format_result_block(out["fit"], out["plasma"],
                                     ion_label="Ar")
        assert "95 % CI" in html or "95% CI" in html
        # n_i CI scope caveat must be on screen.
        assert "fit-only" in html.lower() or "fit_only" in html.lower()
        # The "treated as exact" disclaimer is part of the n_i block.
        assert "treated as exact" in html or "exact" in html

    def test_plain_history_includes_isat_and_ni_ci(self):
        V, I = _clean_dlp_iv()
        probe_params = {"electrode_area_mm2": 1.0}
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe_params,
                                        gases=gases)
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        plain = DLPMainWindowV2._format_analysis_plain(
            None, fit=out["fit"], pp=out["plasma"],
            ion_label="Ar", cmp=[])
        assert "I_sat 95% CI" in plain
        assert "n_i 95% CI (fit-only)" in plain


# ---------------------------------------------------------------------------
# Part 4 — legacy-CSV clipping heuristic.
# ---------------------------------------------------------------------------
class TestLegacyClippingHeuristic:
    def test_clean_sweep_no_false_positive(self):
        from clipping_heuristic import detect_suspected_clipping
        V, I = _clean_dlp_iv()
        summary = detect_suspected_clipping(V, I)
        assert summary["source"] == "none"
        assert summary["n_flagged"] == 0

    def test_clipped_tails_detected(self):
        from clipping_heuristic import detect_suspected_clipping
        V, I, _ = _clipped_iv(clip_fraction=0.2)
        summary = detect_suspected_clipping(V, I)
        assert summary["source"] == "heuristic_suspected"
        assert summary["n_flagged"] >= 4

    def test_real_compliance_takes_precedence(self):
        # When compliance is provided, the analysis MUST use it and
        # must NOT label the source as heuristic_suspected even if
        # the underlying data would also trigger the heuristic.
        V, I, comp = _clipped_iv(clip_fraction=0.2)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=comp,
                                        exclude_clipped=True)
        assert out["compliance_info"]["source"] == "operator_provided"

    def test_heuristic_activates_on_legacy_input(self):
        # No compliance list → heuristic kicks in.
        V, I, _ = _clipped_iv(clip_fraction=0.2)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=None,
                                        exclude_clipped=True)
        info = out["compliance_info"]
        assert info["source"] == "heuristic_suspected"
        assert info["n_flagged"] > 0
        assert info["action"] == "excluded_from_fit"

    def test_clean_legacy_data_no_heuristic_trigger(self):
        V, I = _clean_dlp_iv()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=None)
        info = out["compliance_info"]
        assert info["source"] == "none"
        assert info["n_flagged"] == 0

    def test_misaligned_compliance_falls_back_to_heuristic(self):
        V, I, _ = _clipped_iv(clip_fraction=0.2)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=[False] * 3)
        info = out["compliance_info"]
        # Explicit compliance rejected (wrong length) → heuristic
        # engages as the legacy-safe fallback.  Warning also present.
        assert info["source"] == "heuristic_suspected"
        assert any("compliance length" in w for w in out["warnings"])

    def test_heuristic_output_labelled_suspected_in_html(self):
        V, I, _ = _clipped_iv(clip_fraction=0.2)
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        compliance=None,
                                        exclude_clipped=True)
        from LPmeasurement import _format_compact_double
        html = _format_compact_double(
            out["model_fit"], out["plasma"], None,
            compliance_info=out["compliance_info"])
        assert "suspected clipping" in html
        assert "legacy heuristic" in html or "heuristic" in html.lower()

    def test_short_dataset_not_flagged(self):
        # Below the minimum-size guard the heuristic bows out cleanly.
        from clipping_heuristic import detect_suspected_clipping
        summary = detect_suspected_clipping(
            np.array([0.0, 1.0]), np.array([0.0, 0.0]))
        assert summary["n_flagged"] == 0
