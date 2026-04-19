"""Focused tests for the finishing pass:

* Triple help module content + wiring into the Triple window.
* Per-branch Single reporting on bidirectional sweeps.
* n_i uncertainty budget: optional area / ion-mass inputs fold into
  the CI and the scope note updates truthfully.
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


def _dispose(dlg):
    try:
        dlg._dlg.deleteLater()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Part 1 — Triple help
# ---------------------------------------------------------------------------
class TestTripleHelpContent:
    def test_help_html_covers_core_topics(self):
        from dlp_triple_help import HELP_HTML
        html = HELP_HTML()
        # Operator topics the Triple help must cover.
        for term in ("Triple-probe", "V<sub>d12</sub>",
                     "V<sub>d13</sub>", "ln 2", "Bohm",
                     "Maxwellian", "ion-saturation", "Compliance",
                     "Formula", "K2000", "Probe area"):
            assert term in html, f"missing help term: {term}"

    def test_help_reuses_shared_readable_style(self):
        from dlp_triple_help import HELP_HTML
        html = HELP_HTML()
        # The shared style block from dlp_double_help embeds a 13 pt
        # body size; Triple must inherit it verbatim so operators
        # see the same readable typography across Single / Double /
        # Triple help.
        assert "font-size: 13pt" in html
        assert "Segoe UI" in html


def _make_triple_window(qapp):
    """Build an LPMeasurementWindow with the Fake instrument
    stand-ins so the Triple window can be constructed in the
    offscreen test runner.  Returns the window; caller closes it.
    """
    from fake_b2901 import FakeB2901
    from fake_keithley_2000 import FakeKeithley2000
    from dlp_lp_window import LPMeasurementWindow
    smu = FakeB2901(current_compliance=0.01, noise_std=1e-8)
    smu.connect()
    k2000 = FakeKeithley2000(voltage=0.5)
    k2000.connect()
    return LPMeasurementWindow(smu=smu, k2000=k2000)


class TestTripleHelpWiring:
    def test_window_has_help_button(self, qapp):
        win = _make_triple_window(qapp)
        try:
            assert hasattr(win, "btnHelp")
            assert win.btnHelp.text().startswith("Help")
        finally:
            try:
                win.close()
            except Exception:
                pass

    def test_open_help_slot_calls_opener(self, qapp, monkeypatch):
        import dlp_triple_help as m
        called = {"n": 0}

        def _fake(parent=None):
            called["n"] += 1

        monkeypatch.setattr(m, "open_triple_help_dialog", _fake)
        win = _make_triple_window(qapp)
        try:
            win._on_open_help()
            assert called["n"] == 1
        finally:
            try:
                win.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Part 2 — Per-branch Single reporting
# ---------------------------------------------------------------------------
def _synth_single_iv(n=201, v_start=-30.0, v_stop=20.0,
                      te_eV=3.0, i_sat=1.0e-4, v_p=8.0):
    V = np.linspace(v_start, v_stop, n)
    i_retard = -i_sat + i_sat * np.exp((V - v_p) / te_eV)
    i_sat_e = -i_sat + i_sat * (1.0 + 0.02 * (V - v_p))
    alpha = 0.5 * (1.0 + np.tanh((V - v_p) / (te_eV / 4.0)))
    I = (1.0 - alpha) * i_retard + alpha * i_sat_e
    return V, I


def _make_bidirectional(V, I):
    V_bi = np.concatenate([V, V[-2::-1]])
    I_bi = np.concatenate([I, I[-2::-1]])
    directions = ["fwd"] * len(V) + ["rev"] * (len(V) - 1)
    return V_bi, I_bi, directions


class TestPerBranchSingle:
    def test_default_fields_on_monotonic(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = _synth_single_iv()
        r = analyze_single_iv(V, I, directions=None,
                                v_p_method="derivative")
        # Monotonic input → per-branch machinery is skipped cleanly.
        assert r["bidirectional_mode_used"] is False
        assert r["branch_analysis_status"] == "skipped"
        assert r["te_eV_fwd"] is None
        assert r["te_eV_rev"] is None
        assert r["branch_delta_pct_te"] is None

    def test_bidirectional_populates_branch_fields(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = _synth_single_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                v_p_method="derivative")
        assert r["bidirectional_mode_used"] is True
        assert r["branch_analysis_status"] == "ok"
        assert r["te_eV_fwd"] is not None
        assert r["te_eV_rev"] is not None
        assert r["v_float_V_fwd"] is not None
        assert r["v_float_V_rev"] is not None
        # No drift in synthetic data → fwd/rev match tightly.
        assert r["branch_delta_pct_te"] < 1.0
        # Both branch statuses report "ok".
        assert r["branch_fit_status_fwd"] == "ok"
        assert r["branch_fit_status_rev"] == "ok"

    def test_bidirectional_without_directions_skips_branch_split(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = _synth_single_iv()
        V_bi, I_bi, _ = _make_bidirectional(V, I)
        r = analyze_single_iv(V_bi, I_bi, directions=None,
                                v_p_method="derivative")
        assert r["bidirectional_mode_used"] is True
        assert r["branch_analysis_status"] == "no_direction_tags"
        assert r["te_eV_fwd"] is None
        assert r["te_eV_rev"] is None

    def test_branch_delta_mentioned_in_warning(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = _synth_single_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                v_p_method="derivative")
        joined = " ".join(r["warnings"]).lower()
        assert "per-branch" in joined
        assert "fwd" in joined and "rev" in joined

    def test_html_shows_branch_row_on_bidirectional(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html,
        )
        V, I = _synth_single_iv()
        V_bi, I_bi, dirs = _make_bidirectional(V, I)
        r = analyze_single_iv(V_bi, I_bi, directions=dirs,
                                v_p_method="derivative")
        html = format_single_result_html(r)
        assert "T_e fwd/rev" in html


# ---------------------------------------------------------------------------
# Part 3 — n_i uncertainty budget
# ---------------------------------------------------------------------------
def _clean_dlp_iv(n=61, te=3.0, i_sat=1e-3):
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat * np.tanh(V / (2.0 * te))
    return V, I


class TestNiUncertaintyBudget:
    def _probe_and_gas(self):
        return ({"electrode_area_mm2": 1.0},
                [{"gas": "Ar", "flow_sccm": 1.0}])

    def test_default_note_stays_fit_only(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe,
                                        gases=gases)
        assert out["plasma"]["n_i_ci_note"] == "fit_only"

    def test_area_input_widens_ci_and_updates_note(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out0 = compute_double_analysis(V, I, fit_model="simple_tanh",
                                         probe_params=probe,
                                         gases=gases)
        # 10 % relative area uncertainty
        out1 = compute_double_analysis(V, I, fit_model="simple_tanh",
                                         probe_params=probe,
                                         gases=gases,
                                         probe_area_rel_unc=0.10)
        pp0 = out0["plasma"]
        pp1 = out1["plasma"]
        assert pp1["n_i_ci_note"] == "fit+area"
        # CI width strictly widens when area uncertainty is folded in.
        w0 = pp0["n_i_ci95_hi_m3"] - pp0["n_i_ci95_lo_m3"]
        w1 = pp1["n_i_ci95_hi_m3"] - pp1["n_i_ci95_lo_m3"]
        assert w1 > w0
        # The relative-uncertainty attributes are echoed back.
        assert pp1["n_i_ci_area_rel_unc"] == pytest.approx(0.10)
        assert pp1["n_i_ci_mass_rel_unc"] == pytest.approx(0.0)

    def test_mass_input_updates_note(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe,
                                        gases=gases,
                                        ion_mass_rel_unc=0.10)
        assert out["plasma"]["n_i_ci_note"] == "fit+mass"

    def test_both_inputs_produce_area_plus_mass_label(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe,
                                        gases=gases,
                                        probe_area_rel_unc=0.10,
                                        ion_mass_rel_unc=0.05)
        assert out["plasma"]["n_i_ci_note"] == "fit+area+mass"

    def test_html_reflects_dynamic_note(self):
        from dlp_double_analysis import compute_double_analysis
        from DoubleLangmuir_measure_v2 import format_result_block
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe,
                                        gases=gases,
                                        probe_area_rel_unc=0.08)
        html = format_result_block(out["fit"], out["plasma"],
                                     ion_label="Ar")
        # The scope label should show the widened scope rather than
        # the default "fit-only" string.
        assert "fit+area" in html or "fit-area" in html

    def test_plain_history_reflects_dynamic_note(self):
        from dlp_double_analysis import compute_double_analysis
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_and_gas()
        out = compute_double_analysis(V, I, fit_model="simple_tanh",
                                        probe_params=probe,
                                        gases=gases,
                                        probe_area_rel_unc=0.10,
                                        ion_mass_rel_unc=0.10)
        plain = DLPMainWindowV2._format_analysis_plain(
            None, fit=out["fit"], pp=out["plasma"],
            ion_label="Ar", cmp=[])
        assert "fit+area+mass" in plain or "fit-area-mass" in plain


class TestDoubleOptionsDialogUncertaintyControls:
    def test_dialog_exposes_area_and_mass_fields(self, qapp):
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        dlg = DoubleAnalysisOptionsDialog("tanh_slope",
                                            DoubleAnalysisOptions())
        try:
            assert hasattr(dlg, "spnAreaUnc")
            assert hasattr(dlg, "spnMassUnc")
            # Defaults are 0 % — preserves pre-existing behaviour.
            assert dlg.spnAreaUnc.value() == pytest.approx(0.0)
            assert dlg.spnMassUnc.value() == pytest.approx(0.0)
        finally:
            _dispose(dlg)

    def test_dialog_roundtrips_new_fields(self, qapp):
        from dlp_double_options import (
            DoubleAnalysisOptions, DoubleAnalysisOptionsDialog,
        )
        seed = DoubleAnalysisOptions(probe_area_rel_unc_pct=7.5,
                                       ion_mass_rel_unc_pct=3.0)
        dlg = DoubleAnalysisOptionsDialog("tanh_slope", seed)
        try:
            got = dlg.get_options()
            assert got.probe_area_rel_unc_pct == pytest.approx(7.5)
            assert got.ion_mass_rel_unc_pct == pytest.approx(3.0)
        finally:
            _dispose(dlg)

    def test_dataclass_clamps_pathological_values(self):
        from dlp_double_options import DoubleAnalysisOptions
        restored = DoubleAnalysisOptions.from_dict(
            {"probe_area_rel_unc_pct": 500.0,
             "ion_mass_rel_unc_pct": -5.0})
        assert restored.probe_area_rel_unc_pct == 100.0
        assert restored.ion_mass_rel_unc_pct == 0.0
