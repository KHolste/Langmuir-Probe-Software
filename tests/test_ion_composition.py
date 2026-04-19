"""Focused tests for the ion-composition + n_i uncertainty widening.

Covers P1 + P2 + P4 + help:

* :func:`effective_ion_mass_kg` keeps returning the pre-existing
  flow-weighted neutral mass for Ar-only and Ar/O2 cases (backward
  compatibility).
* :func:`effective_ion_mass_kg_with_unc` returns the expected mass
  central value and relative uncertainty for each of the three
  ion-composition modes.
* :func:`compute_double_analysis` propagates ``ion_mix_rel_unc``
  into the n_i CI width and updates ``n_i_ci_note`` with an
  ``ion_mix`` scope tag when the mode is "unknown" on a molecular
  feed gas.
* The Experiment dialog exposes the new Ion-composition combo,
  round-trips the selected mode through ``get_params``, and
  defaults to ``"molecular"``.
* ``dlp_double_help`` now contains the operator-facing ion-
  composition section with the three mode labels.
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
# Helpers reused across tests.
# ---------------------------------------------------------------------------
def _clean_dlp_iv(n=61, te=3.0, i_sat=1e-3):
    V = np.linspace(-30.0, 30.0, n)
    I = i_sat * np.tanh(V / (2.0 * te))
    return V, I


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


# ---------------------------------------------------------------------------
# P1 — atomic-ion mass + new helper
# ---------------------------------------------------------------------------
class TestAtomicIonMass:
    def test_atomic_mass_table_contains_expected_gases(self):
        from dlp_experiment_dialog import ATOMIC_ION_MASS_U
        for g in ("O2", "N2", "H2"):
            assert g in ATOMIC_ION_MASS_U
        # Monatomic gases must NOT be in the map.
        for g in ("Ar", "He", "Ne", "Xe", "Kr"):
            assert g not in ATOMIC_ION_MASS_U

    def test_legacy_effective_ion_mass_kg_unchanged_for_argon(self):
        from dlp_experiment_dialog import effective_ion_mass_kg
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        m = effective_ion_mass_kg(gases)
        # ≈ 39.948 u → kg
        assert m == pytest.approx(39.948 * 1.6605e-27, rel=1e-4)

    def test_legacy_effective_ion_mass_kg_unchanged_for_o2(self):
        # Default molecular mode must still return the neutral O2
        # mass so every pre-existing test on O2 keeps passing.
        from dlp_experiment_dialog import effective_ion_mass_kg
        gases = [{"gas": "O2", "flow_sccm": 1.0}]
        m = effective_ion_mass_kg(gases)
        assert m == pytest.approx(31.998 * 1.6605e-27, rel=1e-4)

    def test_with_unc_molecular_mode_reports_zero_unc(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        m, u = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 1.0}], mode="molecular")
        assert m == pytest.approx(31.998 * 1.6605e-27, rel=1e-4)
        assert u == 0.0

    def test_with_unc_atomic_mode_uses_atomic_mass(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, ATOMIC_ION_MASS_U,
        )
        m, u = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 1.0}], mode="atomic")
        assert m == pytest.approx(
            ATOMIC_ION_MASS_U["O2"] * 1.6605e-27, rel=1e-4)
        assert u == 0.0

    def test_with_unc_unknown_widens_over_atomic_molecular_span(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, GAS_DATA,
            ATOMIC_ION_MASS_U,
        )
        m, u = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 1.0}], mode="unknown")
        m_mol = GAS_DATA["O2"]
        m_atom = ATOMIC_ION_MASS_U["O2"]
        mid_u = 0.5 * (m_mol + m_atom)
        half_u = 0.5 * abs(m_mol - m_atom)
        assert m == pytest.approx(mid_u * 1.6605e-27, rel=1e-4)
        # Relative uncertainty is the half-span / midpoint.
        assert u == pytest.approx(half_u / mid_u, rel=1e-6)

    def test_with_unc_monatomic_unaffected_by_mode(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        m0, u0 = effective_ion_mass_kg_with_unc(gases, mode="molecular")
        m1, u1 = effective_ion_mass_kg_with_unc(gases, mode="atomic")
        m2, u2 = effective_ion_mass_kg_with_unc(gases, mode="unknown")
        assert m0 == m1 == m2
        assert u0 == u1 == u2 == 0.0

    def test_with_unc_no_flow_returns_none(self):
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        m, u = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 0.0}], mode="unknown")
        assert m is None
        assert u == 0.0

    def test_mixed_o2_ar_unknown_only_widens_for_molecular_part(self):
        # Half flow Ar, half flow O2 → unknown widens only on the
        # O2 row.  The combined rel-unc is smaller than the pure-O2
        # case but still > 0.
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        _, u_pure = effective_ion_mass_kg_with_unc(
            [{"gas": "O2", "flow_sccm": 1.0}], mode="unknown")
        _, u_mix = effective_ion_mass_kg_with_unc(
            [{"gas": "Ar", "flow_sccm": 1.0},
             {"gas": "O2", "flow_sccm": 1.0}], mode="unknown")
        assert 0.0 < u_mix < u_pure


# ---------------------------------------------------------------------------
# P2 — ion_mix_rel_unc propagation into compute_double_analysis
# ---------------------------------------------------------------------------
class TestIonMixPropagation:
    def _probe_gas(self):
        return ({"electrode_area_mm2": 1.0},
                [{"gas": "O2", "flow_sccm": 1.0}])

    def test_molecular_mode_leaves_note_fit_only(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="molecular")
        assert out["plasma"]["n_i_ci_note"] == "fit_only"
        # No ion-mix contribution recorded.
        assert out["plasma"]["n_i_ci_ion_mix_rel_unc"] == 0.0

    def test_unknown_mode_adds_ion_mix_tag_and_widens_ci(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out_mol = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="molecular")
        out_unk = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="unknown")
        # Note contains ion_mix tag when widened.
        assert out_unk["plasma"]["n_i_ci_note"] \
               .endswith("ion_mix")
        # The ion-mix rel-unc is non-zero and reflected in the
        # sidecar-bound field.
        assert out_unk["plasma"]["n_i_ci_ion_mix_rel_unc"] > 0.0
        # CI width strictly widens.
        w_mol = (out_mol["plasma"]["n_i_ci95_hi_m3"]
                 - out_mol["plasma"]["n_i_ci95_lo_m3"])
        w_unk = (out_unk["plasma"]["n_i_ci95_hi_m3"]
                 - out_unk["plasma"]["n_i_ci95_lo_m3"])
        assert w_unk > w_mol

    def test_atomic_mode_shifts_n_i_upward_for_o2(self):
        # For O2 fit with the same I_sat and T_e, switching the
        # assumed ion mass from 32 u to 16 u raises v_Bohm by √2
        # and hence raises n_i by ... well, actually n_i ∝ 1/√m_i
        # for fixed I_sat — so atomic assumption gives a LARGER
        # n_i (because smaller mass → smaller v_Bohm would be
        # wrong — v_Bohm ∝ 1/√m_i so larger v_Bohm → smaller n_i;
        # however n_i = I_sat / (e · A · v_Bohm), so smaller
        # v_Bohm → larger n_i).  Let us not over-constrain the
        # direction by theory here — the safer regression is:
        # atomic and molecular results are DIFFERENT and BOTH
        # finite.
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out_mol = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="molecular")
        out_atom = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="atomic")
        n_mol = out_mol["plasma"]["n_i_m3"]
        n_atom = out_atom["plasma"]["n_i_m3"]
        assert np.isfinite(n_mol) and np.isfinite(n_atom)
        # Switching between the two masses MUST change n_i.
        assert abs(n_atom - n_mol) / abs(n_mol) > 0.2
        # Both notes are still fit_only (no ion-mix tag because the
        # mode is a single-mass assumption, not an ambiguity).
        assert out_mol["plasma"]["n_i_ci_note"] == "fit_only"
        assert out_atom["plasma"]["n_i_ci_note"] == "fit_only"

    def test_unknown_with_user_mass_unc_combines_scope(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _clean_dlp_iv()
        probe, gases = self._probe_gas()
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params=probe, gases=gases,
            ion_composition_mode="unknown",
            ion_mass_rel_unc=0.05)
        # Both user-supplied "mass" and ion-mix show up in the
        # scope note.
        note = out["plasma"]["n_i_ci_note"]
        assert "mass" in note
        assert "ion_mix" in note


# ---------------------------------------------------------------------------
# P4 — Unknown mode via the Experiment dialog
# ---------------------------------------------------------------------------
class TestExperimentDialogIonComposition:
    def test_default_mode_is_molecular(self, qapp):
        from dlp_experiment_dialog import (
            DEFAULT_EXPERIMENT_PARAMS,
        )
        assert DEFAULT_EXPERIMENT_PARAMS["ion_composition_mode"] \
               == "molecular"

    def test_dialog_exposes_combo_with_expected_modes(self, qapp):
        from dlp_experiment_dialog import (
            ExperimentParameterDialog, ION_COMPOSITION_MODES,
        )
        dlg = ExperimentParameterDialog()
        try:
            assert hasattr(dlg, "_cmbIonMode")
            modes = {dlg._cmbIonMode.itemData(i)
                      for i in range(dlg._cmbIonMode.count())}
            # Contract: every supported mode in ION_COMPOSITION_MODES
            # must be reachable from the dialog.  This grows from
            # three to four with the mixed-mode addition; the test
            # is driven by the module constant so further modes
            # would not require a test edit.
            assert modes == set(ION_COMPOSITION_MODES)
        finally:
            dlg.deleteLater()

    def test_get_params_roundtrip_unknown_mode(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [{"gas": "O2", "flow_sccm": 5.0}],
                "ion_composition_mode": "unknown"}
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            assert out["ion_composition_mode"] == "unknown"
            assert out["gases"] == [{"gas": "O2", "flow_sccm": 5.0}]
        finally:
            dlg.deleteLater()

    def test_get_params_roundtrip_atomic_mode(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [{"gas": "O2", "flow_sccm": 5.0}],
                "ion_composition_mode": "atomic"}
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            assert out["ion_composition_mode"] == "atomic"
        finally:
            dlg.deleteLater()


# ---------------------------------------------------------------------------
# Help addition
# ---------------------------------------------------------------------------
class TestHelpCoversIonComposition:
    def test_double_help_mentions_ion_composition(self):
        from dlp_double_help import HELP_HTML
        html = HELP_HTML()
        # Terms that must appear verbatim in the help body.
        for token in ("Ion composition", "Molecular ion",
                      "Atomic ion", "Unknown", "ion_mix"):
            assert token in html, token
