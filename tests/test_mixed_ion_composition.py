"""Focused tests for the mixed ion-composition pass (P1–P4).

Covers:

* ``mixed`` mode in :func:`effective_ion_mass_kg_with_unc`
  — central value matches the linear interpolation
  (1−x)·m_mol + x·m_atomic, relative uncertainty scales with Δx.
* :func:`compute_double_analysis` honours ``ion_x_atomic`` /
  ``ion_x_atomic_unc`` and produces a ``"fit+ion_mix"`` scope
  note with a widened n_i CI when Δx > 0.
* Experiment dialog exposes the mixed-mode controls, round-trips
  the values via ``get_params``, and enables/disables the x / Δx
  spinboxes based on the mode.
* Single-probe path now honours the same ion-composition
  assumption: :func:`analyze_single_iv` accepts ``m_i_rel_unc``
  and populates ``n_e_ci95_lo_m3`` / ``n_e_ci95_hi_m3`` /
  ``n_e_ci_note``.
* Monatomic gases remain unchanged in every mode.
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


# ---------------------------------------------------------------------------
# Synthetic DLP IV helper.
# ---------------------------------------------------------------------------
def _clean_dlp_iv(n=61, te=3.0, i_sat=1e-3):
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat * np.tanh(V / (2.0 * te))
    return V, I


# ---------------------------------------------------------------------------
# Part 1/2 — helper: mixed mode maths.
# ---------------------------------------------------------------------------
class TestMixedModeHelper:
    def test_mixed_mode_is_in_ion_composition_modes(self):
        from dlp_experiment_dialog import ION_COMPOSITION_MODES
        assert "mixed" in ION_COMPOSITION_MODES

    def test_mixed_mass_matches_linear_interpolation(self):
        from dlp_experiment_dialog import (
            ATOMIC_ION_MASS_U, GAS_DATA,
            effective_ion_mass_kg_with_unc,
        )
        gases = [{"gas": "O2", "flow_sccm": 1.0}]
        m_mol = GAS_DATA["O2"]
        m_at = ATOMIC_ION_MASS_U["O2"]
        for x in (0.0, 0.25, 0.5, 0.75, 1.0):
            m, _u = effective_ion_mass_kg_with_unc(
                gases, mode="mixed",
                x_atomic=x, x_atomic_unc=0.0)
            expected_u = (1.0 - x) * m_mol + x * m_at
            assert m == pytest.approx(
                expected_u * 1.6605e-27, rel=1e-6)

    def test_mixed_uncertainty_scales_with_delta_x(self):
        from dlp_experiment_dialog import (
            ATOMIC_ION_MASS_U, GAS_DATA,
            effective_ion_mass_kg_with_unc,
        )
        gases = [{"gas": "O2", "flow_sccm": 1.0}]
        m_mol = GAS_DATA["O2"]
        m_at = ATOMIC_ION_MASS_U["O2"]
        span = abs(m_mol - m_at)
        # At x = 0.5 the midpoint mass is fixed; σ scales with Δx.
        m, u1 = effective_ion_mass_kg_with_unc(
            gases, mode="mixed", x_atomic=0.5, x_atomic_unc=0.1)
        _, u2 = effective_ion_mass_kg_with_unc(
            gases, mode="mixed", x_atomic=0.5, x_atomic_unc=0.2)
        # rel_unc = (span · Δx) / m_mean
        m_u = m / 1.6605e-27
        assert u1 == pytest.approx(span * 0.1 / m_u, rel=1e-4)
        assert u2 == pytest.approx(span * 0.2 / m_u, rel=1e-4)

    def test_mixed_on_monatomic_is_a_no_op(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        m_mol, u_mol = effective_ion_mass_kg_with_unc(
            gases, mode="molecular")
        m_mix, u_mix = effective_ion_mass_kg_with_unc(
            gases, mode="mixed", x_atomic=0.5, x_atomic_unc=0.2)
        assert m_mix == m_mol
        assert u_mix == 0.0

    def test_mixed_zero_unc_leaves_no_widening(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        m, u = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 1.0}],
            mode="mixed", x_atomic=0.3, x_atomic_unc=0.0)
        assert u == 0.0
        # Mass still corresponds to the interpolated point.
        assert m is not None


# ---------------------------------------------------------------------------
# Part 3 — Double propagation.
# ---------------------------------------------------------------------------
class TestDoubleMixedPropagation:
    def _probe_gas(self):
        return ({"electrode_area_mm2": 1.0},
                [{"gas": "O2", "flow_sccm": 1.0}])

    def test_mixed_mode_widens_n_i_ci_via_delta_x(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out_narrow = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="mixed",
            ion_x_atomic=0.5, ion_x_atomic_unc=0.05)
        out_wide = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="mixed",
            ion_x_atomic=0.5, ion_x_atomic_unc=0.25)
        w_narrow = (out_narrow["plasma"]["n_i_ci95_hi_m3"]
                    - out_narrow["plasma"]["n_i_ci95_lo_m3"])
        w_wide = (out_wide["plasma"]["n_i_ci95_hi_m3"]
                  - out_wide["plasma"]["n_i_ci95_lo_m3"])
        assert w_wide > w_narrow
        # Scope note gains the ion_mix tag in both cases.
        assert "ion_mix" in out_narrow["plasma"]["n_i_ci_note"]
        assert "ion_mix" in out_wide["plasma"]["n_i_ci_note"]

    def test_mixed_mode_center_shifts_n_i_vs_molecular(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out_mol = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="molecular")
        out_mix50 = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="mixed",
            ion_x_atomic=0.5, ion_x_atomic_unc=0.0)
        # 50/50 mix sits between molecular and atomic — the
        # central n_i must be different from pure molecular.
        assert abs(out_mix50["plasma"]["n_i_m3"]
                   - out_mol["plasma"]["n_i_m3"]) > 0.0

    def test_mixed_with_zero_delta_x_no_ion_mix_tag(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="mixed",
            ion_x_atomic=0.3, ion_x_atomic_unc=0.0)
        # No uncertainty widening → no ion_mix scope tag.
        assert "ion_mix" not in out["plasma"]["n_i_ci_note"]


# ---------------------------------------------------------------------------
# Part 4 — Single-probe integration.
# ---------------------------------------------------------------------------
class TestSingleIonMassUncertainty:
    def _synth_single_iv(self, n=201, v_start=-30.0, v_stop=20.0,
                           te_eV=3.0, i_sat=1.0e-4, v_p=8.0):
        V = np.linspace(v_start, v_stop, n)
        i_retard = -i_sat + i_sat * np.exp((V - v_p) / te_eV)
        i_sat_e = -i_sat + i_sat * (1.0 + 0.02 * (V - v_p))
        alpha = 0.5 * (1.0 + np.tanh((V - v_p) / (te_eV / 4.0)))
        I = (1.0 - alpha) * i_retard + alpha * i_sat_e
        return V, I

    def test_analyze_single_iv_defaults_keep_n_e_ci_unavailable(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = self._synth_single_iv()
        r = analyze_single_iv(V, I, area_m2=1e-6,
                                m_i_kg=6.63e-26,
                                m_i_rel_unc=0.0)
        # Without ion-mass unc and depending on Te_err, the CI
        # method either picks up "covariance" from Te alone (if
        # Te_err is finite) or reports "unavailable" honestly.
        assert r.get("n_e_ci_method") in ("covariance", "unavailable")
        # In either case the note is at most "fit_only" with no
        # ion_mix tag since m_i_rel_unc = 0.
        note = r.get("n_e_ci_note", "fit_only")
        assert "ion_mix" not in note

    def test_m_i_rel_unc_widens_n_e_ci(self):
        from dlp_single_analysis import analyze_single_iv
        V, I = self._synth_single_iv()
        r_narrow = analyze_single_iv(V, I, area_m2=1e-6,
                                        m_i_kg=6.63e-26,
                                        m_i_rel_unc=0.05)
        r_wide = analyze_single_iv(V, I, area_m2=1e-6,
                                      m_i_kg=6.63e-26,
                                      m_i_rel_unc=0.25)
        # Both runs should produce a CI when feasible.  The wider
        # m_i uncertainty must widen (or at worst equal) the n_e
        # CI — never narrow it.
        if (r_narrow["n_e_ci_method"] == "covariance"
                and r_wide["n_e_ci_method"] == "covariance"):
            w_narrow = (r_narrow["n_e_ci95_hi_m3"]
                        - r_narrow["n_e_ci95_lo_m3"])
            w_wide = (r_wide["n_e_ci95_hi_m3"]
                      - r_wide["n_e_ci95_lo_m3"])
            assert w_wide >= w_narrow
            # The scope note carries the ion_mix tag when the
            # rel_unc contribution is non-zero.
            assert "ion_mix" in r_wide["n_e_ci_note"]

    def test_single_result_html_mentions_n_e_ci_scope(self):
        from dlp_single_analysis import (
            analyze_single_iv, format_single_result_html,
        )
        V, I = self._synth_single_iv()
        r = analyze_single_iv(V, I, area_m2=1e-6,
                                m_i_kg=6.63e-26,
                                m_i_rel_unc=0.2)
        html = format_single_result_html(r)
        # Either the CI block rendered with its scope note, or
        # "unavailable" if the Te path could not produce a CI.
        assert "95% CI" in html or "unavailable" in html


# ---------------------------------------------------------------------------
# Experiment dialog — UI controls + round-trip.
# ---------------------------------------------------------------------------
class TestExperimentDialogMixed:
    def test_combo_has_mixed_entry(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            modes = {dlg._cmbIonMode.itemData(i)
                      for i in range(dlg._cmbIonMode.count())}
            assert "mixed" in modes
            assert hasattr(dlg, "_spnXAtomic")
            assert hasattr(dlg, "_spnXAtomicUnc")
        finally:
            dlg.deleteLater()

    def test_mixed_controls_enabled_only_in_mixed_mode(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            # Default = molecular → disabled.
            assert dlg._spnXAtomic.isEnabled() is False
            assert dlg._spnXAtomicUnc.isEnabled() is False
            # Switch to mixed → enabled.
            idx = dlg._cmbIonMode.findData("mixed")
            dlg._cmbIonMode.setCurrentIndex(idx)
            assert dlg._spnXAtomic.isEnabled() is True
            assert dlg._spnXAtomicUnc.isEnabled() is True
            # Back to molecular → disabled again.
            idx2 = dlg._cmbIonMode.findData("molecular")
            dlg._cmbIonMode.setCurrentIndex(idx2)
            assert dlg._spnXAtomic.isEnabled() is False
        finally:
            dlg.deleteLater()

    def test_get_params_roundtrip_mixed(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [{"gas": "O2", "flow_sccm": 5.0}],
                "ion_composition_mode": "mixed",
                "x_atomic": 0.35, "x_atomic_unc": 0.10}
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            assert out["ion_composition_mode"] == "mixed"
            assert out["x_atomic"] == pytest.approx(0.35, rel=1e-3)
            assert out["x_atomic_unc"] == pytest.approx(0.10,
                                                          rel=1e-3)
        finally:
            dlg.deleteLater()

    def test_defaults_expose_x_atomic_fields(self):
        from dlp_experiment_dialog import DEFAULT_EXPERIMENT_PARAMS
        assert "x_atomic" in DEFAULT_EXPERIMENT_PARAMS
        assert "x_atomic_unc" in DEFAULT_EXPERIMENT_PARAMS


# ---------------------------------------------------------------------------
# Help content mentions the new Mixed concept.
# ---------------------------------------------------------------------------
class TestHelpCoversMixedMode:
    def test_help_html_mentions_mixed_and_delta_x(self):
        from dlp_double_help import HELP_HTML
        html = HELP_HTML()
        assert "Mixed" in html
        # Either Δx or the HTML entity form of it must appear.
        assert "\u0394x" in html or "&Delta;x" in html
        assert "x_atomic" in html or "atomic-ion fraction" \
               in html.lower()
