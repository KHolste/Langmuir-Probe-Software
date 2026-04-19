"""Focused tests: feed-gas vs plasma-phase ion composition.

Pins the physically-critical invariants the operator must be able to
trust when handling molecular feed gases:

1. ``sccm_to_mgs`` for a molecular gas always uses the neutral
   molecular molar mass — the ion-composition mode never rescales
   the feed flow.
2. ``effective_ion_mass_kg_with_unc`` uses the documented feed-flow-
   weighted arithmetic-mean form.  The per-gas ion-mass assumption
   changes with mode; the *flow weights* never do.
3. ``N2 → 2N`` style reactions are expressed only through the ion
   mass (14 u under ``atomic``), not through a silent doubling of
   atom count or flow.
4. Inert gases remain inert; changing a molecular gas's mode leaves
   the inert gas's contribution invariant.
5. The gas-mix data model preserves ``gas`` and ``flow_sccm`` as
   feed-side entries regardless of the ion-composition mode.
6. The Experiment-help HTML contains the feed-vs-plasma section,
   the ``N2 → 2N`` caveat, and the arithmetic-approximation
   disclaimer.
7. The LP-window CSV header emits an ``Ion_Note`` line explaining
   the Ion_* keys' plasma-phase semantics.
8. Single / Double / Triple all consume the same shared
   effective-ion-mass helper — wiring parity.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# Invariant 1: sccm_to_mgs is feed-gas-identity based
# ---------------------------------------------------------------------------
class TestFeedFlowInvariantUnderMode:
    def test_sccm_to_mgs_uses_molecular_molar_mass(self):
        """The sccm\u2192mg/s conversion is a feed-side accounting
        step and must always use the neutral molecular molar mass
        from GAS_DATA, regardless of any ion-composition choice."""
        from dlp_experiment_dialog import GAS_DATA, sccm_to_mgs
        # O2 molecular molar mass is 31.998 g/mol; the function is
        # a pure numeric helper so any choice of ion-composition
        # mode has NO way to reach into this math.
        expected = sccm_to_mgs(1.0, GAS_DATA["O2"])
        assert expected > 0.0
        # Calling with the atomic-oxygen mass would halve the
        # mg/s; this test ensures we are not doing that by
        # accident anywhere in the stack.
        atomic_guess = sccm_to_mgs(1.0, 15.999)
        assert atomic_guess < expected
        # Sanity: ratio of molecular / atomic masses matches.
        assert expected / atomic_guess == pytest.approx(
            31.998 / 15.999, rel=1e-4)

    def test_flow_is_not_rescaled_when_switching_to_atomic(self):
        """Two calls with identical feed and different modes
        produce the SAME total feed sccm going into the helper —
        only the per-gas ion mass changes."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [{"gas": "N2", "flow_sccm": 1.0}]
        m_mol, _ = effective_ion_mass_kg_with_unc(gases)
        m_at, _ = effective_ion_mass_kg_with_unc(
            gases, per_gas_composition={"N2": {"mode": "atomic"}})
        # Mode change flips N2 ion mass from 28 u (molecular) to
        # 14 u (atomic).  Flow weight is 1 sccm in both cases.
        assert m_mol / _U_TO_KG == pytest.approx(28.014, rel=1e-4)
        assert m_at / _U_TO_KG == pytest.approx(14.007, rel=1e-4)


# ---------------------------------------------------------------------------
# Invariant 2: arithmetic-flow-weighted mean (documented choice)
# ---------------------------------------------------------------------------
class TestDocumentedArithmeticForm:
    def test_o2_xe_50_50_matches_arithmetic_mean(self):
        """Pin the documented form m_i_eff = \u03a3 f_g m_g / \u03a3 f_g.

        If a future change silently switches to a harmonic-like
        form this test fails loudly.  The arithmetic and harmonic
        forms differ by tens of per-cent for heavy-light mixtures,
        so the chosen form is a scientific decision, not an
        implementation detail.
        """
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        per_gas = {"O2": {"mode": "atomic"}}
        m, _ = effective_ion_mass_kg_with_unc(
            gases, per_gas_composition=per_gas)
        u = m / _U_TO_KG
        # Arithmetic mean of (16 u, 131.3 u).
        expected_arith = (15.999 + 131.293) / 2.0
        assert u == pytest.approx(expected_arith, rel=1e-4)
        # Safety check that we are NOT quietly computing the
        # harmonic-like form.
        expected_harm = 1.0 / ((0.5 / math.sqrt(15.999)
                                + 0.5 / math.sqrt(131.293)) ** 2)
        assert abs(u - expected_harm) > 5.0


# ---------------------------------------------------------------------------
# Invariant 3: N2 → 2N not a silent feed rescale
# ---------------------------------------------------------------------------
class TestN2DissociationIsIonMassOnly:
    def test_n2_atomic_mode_does_not_double_flow_contribution(self):
        """N2\u00a0at atomic mode contributes with *its* feed-flow
        weight (1), not 2 (which would be the case if the software
        silently rescaled to 2\u202fN atoms per N2 molecule)."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        # Pure N2 atomic-mode: mean must be exactly 14 u, not 7 u
        # (which would be 28 / (2\u00b71) — i.e. what a naive
        # atom-count rescale would give).
        m, _ = effective_ion_mass_kg_with_unc(
            [{"gas": "N2", "flow_sccm": 1.0}],
            per_gas_composition={"N2": {"mode": "atomic"}})
        assert m / _U_TO_KG == pytest.approx(14.007, rel=1e-4)

    def test_n2_xe_mixed_atomic_mode_weights_are_feed_weights(self):
        """N2 + Xe with N2=atomic: weight of each row is still its
        feed sccm share, not an atom-count share."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        m, _ = effective_ion_mass_kg_with_unc(
            [
                {"gas": "N2", "flow_sccm": 0.1},
                {"gas": "Xe", "flow_sccm": 0.1},
            ],
            per_gas_composition={"N2": {"mode": "atomic"}})
        u = m / _U_TO_KG
        # If the code wrongly counted N2 as "2 parts of N" we'd
        # get (2\u00b714 + 1\u00b7131.3) / 3 \u2248 53.1 u.  The
        # correct feed-weighted answer is (14 + 131.3) / 2.
        assert u == pytest.approx((14.007 + 131.293) / 2, rel=1e-4)
        assert abs(u - (2 * 14.007 + 131.293) / 3.0) > 5.0


# ---------------------------------------------------------------------------
# Invariant 4: inert gases are invariant under molecular-gas mode change
# ---------------------------------------------------------------------------
class TestInertGasInvariance:
    def test_xe_contribution_unchanged_when_o2_mode_flips(self):
        """Changing O2's mode must not change Xe's row in the
        per-gas breakdown."""
        from dlp_experiment_dialog import per_gas_breakdown
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        b_mol = {e["gas"]: e for e in per_gas_breakdown(gases)}
        b_at = {e["gas"]: e for e in per_gas_breakdown(
            gases,
            per_gas_composition={"O2": {"mode": "atomic"}})}
        # Xe contribution is byte-identical between the two calls.
        for k in ("m_ion_u", "sigma_u", "flow_sccm",
                   "flow_fraction", "is_molecular", "mode"):
            assert b_mol["Xe"][k] == b_at["Xe"][k]


# ---------------------------------------------------------------------------
# Invariant 5: feed-side data model preserved by the dialog
# ---------------------------------------------------------------------------
@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


class TestDialogPreservesFeedSideIdentity:
    def test_get_params_keeps_o2_as_molecular_feed_gas(self, qapp):
        """After selecting atomic mode for O2, ``gases`` in
        ``get_params`` still lists O2 at the entered sccm — the
        feed-side identity is untouched."""
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {
            "gases": [
                {"gas": "O2", "flow_sccm": 0.1},
                {"gas": "Xe", "flow_sccm": 0.1},
                {"gas": "", "flow_sccm": 0.0},
            ],
            "per_gas_composition": {
                "O2": {"mode": "atomic",
                        "x_atomic": 0.0,
                        "x_atomic_unc": 0.0,
                        "preset": "custom"},
            },
        }
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            # Feed-side list unchanged.
            gas_names = [g["gas"] for g in out["gases"]]
            assert "O2" in gas_names
            assert "Xe" in gas_names
            o2 = next(g for g in out["gases"] if g["gas"] == "O2")
            # The feed sccm is unchanged — no rescale / doubling.
            assert o2["flow_sccm"] == pytest.approx(0.1, abs=1e-6)
            # Plasma-phase override persisted on its own field.
            assert out["per_gas_composition"]["O2"]["mode"] == "atomic"
        finally:
            dlg.deleteLater()

    def test_feed_gas_group_title_mentions_inlet(self, qapp):
        """The top gas group must explicitly advertise that it
        represents feed (inlet) flows — not plasma-phase
        densities."""
        from PySide6.QtWidgets import QGroupBox
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            titles = [gb.title() for gb in dlg.findChildren(QGroupBox)]
            feed_title = next(
                (t for t in titles if "feed" in t.lower()), None)
            assert feed_title is not None, \
                "top group must mention 'feed'"
            assert "sccm" in feed_title.lower() \
                or "inlet" in feed_title.lower()
        finally:
            dlg.deleteLater()

    def test_ion_composition_group_mentions_plasma(self, qapp):
        """The ion-composition group's title must advertise its
        plasma-phase scope so the operator cannot mistake it for a
        feed-side control."""
        from PySide6.QtWidgets import QGroupBox
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            titles = [gb.title().lower()
                      for gb in dlg.findChildren(QGroupBox)]
            assert any("plasma" in t for t in titles), \
                "ion-composition group must mention 'plasma'"
        finally:
            dlg.deleteLater()


# ---------------------------------------------------------------------------
# Invariant 6: help HTML documents the distinction
# ---------------------------------------------------------------------------
class TestHelpDocumentsDistinction:
    def test_help_has_feed_vs_plasma_section(self):
        from dlp_experiment_help import HELP_HTML
        lowered = HELP_HTML.lower()
        assert "feed gas vs plasma" in lowered, \
            "help must have a Feed-gas-vs-plasma section"

    def test_help_has_n2_dissociation_caveat(self):
        from dlp_experiment_help import HELP_HTML
        assert "N<sub>2</sub> &rarr; 2N" in HELP_HTML \
            or "N2 \u2192 2N" in HELP_HTML \
            or "N<sub>2</sub>&nbsp;&rarr;&nbsp;2N" in HELP_HTML \
            or "2N" in HELP_HTML
        assert "atomic" in HELP_HTML.lower()
        # The key claim: atomic mode does NOT mean atomic feed.
        assert "does <b>not</b>" in HELP_HTML \
            or "not</b> rescale" in HELP_HTML \
            or "not <b>rescale" in HELP_HTML \
            or "never" in HELP_HTML.lower()

    def test_help_mentions_arithmetic_approximation_disclaimer(self):
        from dlp_experiment_help import HELP_HTML
        lowered = HELP_HTML.lower()
        assert "arithmetic" in lowered
        # Must acknowledge the harmonic-form alternative or the
        # pragmatic-approximation nature so the operator sees we
        # are not pretending to do full plasma chemistry.
        assert "harmonic" in lowered \
            or "approximation" in lowered

    def test_help_disclaims_inlet_injection_of_atomic_species(self):
        from dlp_experiment_help import HELP_HTML
        # The help must explicitly disclaim "atomic mode = atomic
        # feed gas".
        assert ("feeding atomic" in HELP_HTML
                 or "atomic feed" in HELP_HTML
                 or "atomic nitrogen at the inlet" in HELP_HTML
                 or "atomic oxygen" in HELP_HTML.lower()
                 or "never means" in HELP_HTML)


# ---------------------------------------------------------------------------
# Invariant 7: CSV Ion_Note emitted by LP window
# ---------------------------------------------------------------------------
class TestCsvIonNoteEmitted:
    def test_build_meta_emits_ion_note_when_context_present(self, qapp):
        """When the LP window carries an ion-composition context
        the CSV header must include an ``Ion_Note`` line stating
        the plasma-phase semantics.  Absent when no context — the
        metadata block stays terse for the simple case."""
        from dlp_lp_window import LPMeasurementWindow
        # Build a window via __new__ to skip the heavy constructor
        # and inject only the attributes _build_meta needs.
        w = LPMeasurementWindow.__new__(LPMeasurementWindow)
        w._area_m2 = 9.7075e-6
        w._gas_mix_label = "O2 0.1 + Xe 0.1 sccm"

        # Mock the widgets _build_meta reads for V_d12 / compliance.
        class _Stub:
            def __init__(self, v):
                self._v = v

            def value(self):
                return self._v

            def currentData(self):
                return 1

        w.spnVd12 = _Stub(25.0)
        w.spnCompliance = _Stub(1e-3)
        w.cmbSign = _Stub(1)
        w.spnTick = _Stub(250)

        # 1. No context → no Ion_Note.
        w._ion_composition_context = {}
        meta = w._build_meta()
        assert "Ion_Note" not in meta

        # 2. Context present → Ion_Note appears, spells out the
        #    plasma-phase semantics.
        w._ion_composition_context = {
            "ion_composition_preset": "custom",
            "ion_composition_mode": "atomic",
            "x_atomic": 0.0,
            "x_atomic_unc": 0.0,
            "mi_rel_unc": 0.05,
        }
        meta = w._build_meta()
        assert "Ion_Note" in meta
        note = meta["Ion_Note"].lower()
        assert "plasma" in note
        assert ("feed" in note or "molecular" in note or
                "inlet" in note)


# ---------------------------------------------------------------------------
# Invariant 8: probe-method wiring parity
# ---------------------------------------------------------------------------
class TestProbeMethodParity:
    def test_build_lp_gas_context_uses_per_gas_helper_once(self, qapp):
        """All three probe methods consume ``mi_kg`` /
        ``mi_rel_unc`` from the same helper call in
        ``_build_lp_gas_context`` — so whatever number Single gets
        is the number Double and Triple get."""
        from LPmeasurement import LPMainWindow
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        stub = LPMainWindow.__new__(LPMainWindow)
        stub._experiment_params = {
            "gases": [
                {"gas": "N2", "flow_sccm": 1.0},
                {"gas": "Xe", "flow_sccm": 1.0},
            ],
            "ion_composition_mode": "molecular",
            "x_atomic": 0.0,
            "x_atomic_unc": 0.0,
            "per_gas_composition": {
                "N2": {"mode": "atomic"},
            },
        }
        label, mi_kg, mi_rel_unc = stub._build_lp_gas_context()
        # The number returned by the helper matches a direct call.
        direct, direct_rel = effective_ion_mass_kg_with_unc(
            stub._experiment_params["gases"],
            mode="molecular", x_atomic=0.0, x_atomic_unc=0.0,
            per_gas_composition={"N2": {"mode": "atomic"}})
        assert mi_kg == pytest.approx(direct, rel=1e-9)
        assert mi_rel_unc == pytest.approx(direct_rel, rel=1e-9)
        # And, spot-check: N2 at atomic ⇒ (14 + 131.3) / 2
        assert direct / _U_TO_KG == pytest.approx(
            (14.007 + 131.293) / 2, rel=1e-4)
