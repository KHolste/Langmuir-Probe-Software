"""Focused tests for the per-gas ion-composition redesign.

Covers:

* ``effective_ion_mass_kg_with_unc`` honours a ``per_gas_composition``
  override and leaves inert gases out of the ambiguity budget.
* ``per_gas_breakdown`` returns the per-row diagnostic dicts used by
  the Experiment-dialog summary and the sidecar audit trail.
* ``ExperimentParameterDialog`` renders one per-gas editor for each
  currently-selected molecular gas, a read-only caption for inert
  gases, and round-trips the per-gas dict through ``get_params``.
* Legacy experiment params without ``per_gas_composition`` keep
  working through the global fallback — simple single-gas workflows
  are not broken.
* The Experiment dialog exposes a Help button that opens a
  ``QDialog`` whose body contains the expected operator-facing
  sections.
* ``ion_composition_presets.presets_for_gas`` filters to the gas
  scope so an O₂ row does not see N₂ presets and vice-versa.
* ``LPmeasurement._build_lp_gas_context`` forwards per-gas overrides
  into the shared ion-mass helper.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


# ---------------------------------------------------------------------------
# Helper: effective_ion_mass_kg_with_unc with per-gas overrides
# ---------------------------------------------------------------------------
class TestEffectiveIonMassPerGas:
    def test_legacy_signature_still_works(self):
        # No per_gas_composition: behaves exactly as before.
        from dlp_experiment_dialog import effective_ion_mass_kg_with_unc
        gases = [{"gas": "Ar", "flow_sccm": 1.0}]
        m, rel = effective_ion_mass_kg_with_unc(gases)
        assert m == pytest.approx(39.948 * 1.6605e-27, rel=1e-4)
        assert rel == 0.0

    def test_per_gas_mode_affects_only_that_gas(self):
        """Setting O2=atomic must shift only O2's contribution; Xe
        remains included as inert at its monatomic mass."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        # Without override: O2 molecular (32 u), Xe (131.3 u).
        m0, _ = effective_ion_mass_kg_with_unc(gases)
        u0 = m0 / _U_TO_KG
        assert u0 == pytest.approx((31.998 + 131.293) / 2, rel=1e-4)

        # With per-gas override: O2 atomic (16 u) → mean halves
        # the O2 contribution.
        per_gas = {"O2": {"mode": "atomic",
                          "x_atomic": 0.0,
                          "x_atomic_unc": 0.0}}
        m1, rel1 = effective_ion_mass_kg_with_unc(
            gases, per_gas_composition=per_gas)
        u1 = m1 / _U_TO_KG
        assert u1 == pytest.approx((15.999 + 131.293) / 2, rel=1e-4)
        # Xe must still be present — if we had lost Xe the mean
        # would collapse to just O.
        assert u1 > 50.0
        # Atomic mode carries no ambiguity → rel_unc stays zero.
        assert rel1 == 0.0

    def test_per_gas_unknown_mode_widens_only_that_gas(self):
        """O2=unknown widens the ambiguity budget; Xe (inert) does
        not contribute to the widening."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        per_gas = {"O2": {"mode": "unknown",
                          "x_atomic": 0.0,
                          "x_atomic_unc": 0.0}}
        _, rel = effective_ion_mass_kg_with_unc(
            gases, per_gas_composition=per_gas)
        # Unknown yields a non-zero widening on O2 alone.  The rel
        # unc is strictly positive.
        assert rel > 0.0

    def test_per_gas_overrides_global_triple(self):
        """When both global and per-gas are supplied, per-gas wins
        for gases with an override; others fall back to global."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [
            {"gas": "O2", "flow_sccm": 1.0},
            {"gas": "N2", "flow_sccm": 1.0},
        ]
        # Global default = atomic (would yield 16 for O2, 14 for N2).
        # Per-gas O2 = molecular overrides → 32 for O2 only.
        per_gas = {"O2": {"mode": "molecular"}}
        m, _ = effective_ion_mass_kg_with_unc(
            gases, mode="atomic", per_gas_composition=per_gas)
        u = m / _U_TO_KG
        # O2 = 32, N2 = 14 (global atomic) → mean 23.
        assert u == pytest.approx((31.998 + 14.007) / 2, rel=1e-4)

    def test_per_gas_entry_for_monatomic_gas_is_ignored(self):
        """A stale per-gas entry keyed for Xe must not flip Xe's
        treatment — monatomic gases have no atomic/molecular
        ambiguity to honour."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [{"gas": "Xe", "flow_sccm": 1.0}]
        per_gas = {"Xe": {"mode": "atomic"}}
        m, rel = effective_ion_mass_kg_with_unc(
            gases, per_gas_composition=per_gas)
        assert m / _U_TO_KG == pytest.approx(131.293, rel=1e-4)
        assert rel == 0.0

    def test_o2_xe_example_flow_share(self):
        """Concrete example from the user request:
        O2=0.1 sccm + Xe=0.1 sccm behaves exactly as two equally
        weighted rows.  Changing the O2 mode changes only the O2
        half of the mean."""
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc, _U_TO_KG,
        )
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        # Sweep modes on O2; Xe should stay at 131.293 throughout.
        expected_o2 = {
            "molecular": 31.998,
            "atomic":    15.999,
        }
        for mode, o2_u in expected_o2.items():
            m, _ = effective_ion_mass_kg_with_unc(
                gases,
                per_gas_composition={"O2": {"mode": mode}})
            u = m / _U_TO_KG
            assert u == pytest.approx((o2_u + 131.293) / 2,
                                        rel=1e-4)


# ---------------------------------------------------------------------------
# Helper: per_gas_breakdown
# ---------------------------------------------------------------------------
class TestPerGasBreakdown:
    def test_breakdown_flags_inert_and_molecular(self):
        from dlp_experiment_dialog import per_gas_breakdown
        gases = [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
        ]
        out = per_gas_breakdown(
            gases,
            per_gas_composition={"O2": {"mode": "mixed",
                                         "x_atomic": 0.5,
                                         "x_atomic_unc": 0.2}})
        assert len(out) == 2
        by_gas = {b["gas"]: b for b in out}
        assert by_gas["O2"]["is_molecular"] is True
        assert by_gas["Xe"]["is_molecular"] is False
        # Flow fractions sum to 1.
        assert (by_gas["O2"]["flow_fraction"]
                + by_gas["Xe"]["flow_fraction"]) == pytest.approx(1.0)
        # Only O2 contributes sigma (Xe is inert).
        assert by_gas["O2"]["sigma_u"] > 0.0
        assert by_gas["Xe"]["sigma_u"] == 0.0

    def test_breakdown_empty_for_no_flow(self):
        from dlp_experiment_dialog import per_gas_breakdown
        assert per_gas_breakdown([]) == []
        assert per_gas_breakdown([{"gas": "O2", "flow_sccm": 0.0}]) == []


# ---------------------------------------------------------------------------
# Presets: per-gas filtering
# ---------------------------------------------------------------------------
class TestPresetsForGas:
    def test_o2_row_sees_only_o2_and_any(self):
        from ion_composition_presets import presets_for_gas
        ps = presets_for_gas("O2")
        scopes = {p.scope for p in ps}
        assert scopes <= {"any", "O2"}
        # At least one O2-specific preset and at least one any preset.
        assert any(p.scope == "O2" for p in ps)
        assert any(p.scope == "any" for p in ps)
        # No N2 or H2 presets.
        assert not any(p.scope in ("N2", "H2") for p in ps)

    def test_unknown_gas_returns_only_any(self):
        from ion_composition_presets import presets_for_gas
        ps = presets_for_gas("CH4")  # not supported
        assert all(p.scope == "any" for p in ps)


# ---------------------------------------------------------------------------
# Dialog: per-gas UI + round-trip
# ---------------------------------------------------------------------------
class TestExperimentDialogPerGas:
    def test_default_params_include_per_gas_key(self):
        from dlp_experiment_dialog import DEFAULT_EXPERIMENT_PARAMS
        assert "per_gas_composition" in DEFAULT_EXPERIMENT_PARAMS
        assert DEFAULT_EXPERIMENT_PARAMS["per_gas_composition"] == {}

    def test_dialog_renders_per_gas_editor_for_o2(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [
            {"gas": "O2", "flow_sccm": 0.1},
            {"gas": "Xe", "flow_sccm": 0.1},
            {"gas": "", "flow_sccm": 0.0},
        ]}
        dlg = ExperimentParameterDialog(seed)
        try:
            editors = dlg._per_gas_editors
            assert "O2" in editors
            assert "Xe" in editors
            # Molecular → full editor; inert → caption only.
            assert editors["O2"]["molecular"] is True
            assert editors["Xe"]["molecular"] is False
            # O2 editor carries the expected widgets.
            for key in ("cmb_preset", "cmb_mode", "spn_x", "spn_dx"):
                assert key in editors["O2"]
        finally:
            dlg.deleteLater()

    def test_get_params_roundtrips_per_gas_composition(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {
            "gases": [
                {"gas": "O2", "flow_sccm": 0.1},
                {"gas": "Xe", "flow_sccm": 0.1},
                {"gas": "", "flow_sccm": 0.0},
            ],
            "per_gas_composition": {
                "O2": {"mode": "mixed",
                        "x_atomic": 0.3, "x_atomic_unc": 0.15,
                        "preset": "custom"},
            },
        }
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            assert "per_gas_composition" in out
            assert "O2" in out["per_gas_composition"]
            o2 = out["per_gas_composition"]["O2"]
            assert o2["mode"] == "mixed"
            assert o2["x_atomic"] == pytest.approx(0.3, abs=1e-3)
            assert o2["x_atomic_unc"] == pytest.approx(0.15, abs=1e-3)
            # Xe is inert → no per-gas entry.
            assert "Xe" not in out["per_gas_composition"]
        finally:
            dlg.deleteLater()

    def test_per_gas_edit_drops_stale_gas_on_gas_change(self, qapp):
        """Replacing O2 with N2 should drop the O2 override from the
        persisted per-gas dict (state is KEPT in memory so toggling
        back restores it, but ``get_params`` emits only currently-
        selected gases — no stale entries in sidecars)."""
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {
            "gases": [
                {"gas": "O2", "flow_sccm": 1.0},
                {"gas": "", "flow_sccm": 0.0},
                {"gas": "", "flow_sccm": 0.0},
            ],
            "per_gas_composition": {
                "O2": {"mode": "atomic",
                        "x_atomic": 0.0, "x_atomic_unc": 0.0,
                        "preset": "custom"},
            },
        }
        dlg = ExperimentParameterDialog(seed)
        try:
            # Swap O2 → N2 by setting the gas combo directly.
            from dlp_experiment_dialog import _set_combo
            _set_combo(dlg._gas_combos[0], "N2")
            out = dlg.get_params()
            assert "N2" in [g["gas"] for g in out["gases"]]
            # O2 is no longer selected → must not be persisted.
            assert "O2" not in out["per_gas_composition"]
        finally:
            dlg.deleteLater()

    def test_legacy_params_without_per_gas_load_cleanly(self, qapp):
        """Simple Ar-only workflow — seed has no per_gas_composition
        key — must still work and emit an empty per-gas dict."""
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [
            {"gas": "Ar", "flow_sccm": 1.0},
            {"gas": "", "flow_sccm": 0.0},
            {"gas": "", "flow_sccm": 0.0},
        ]}
        dlg = ExperimentParameterDialog(seed)
        try:
            out = dlg.get_params()
            assert out["per_gas_composition"] == {}
            # Legacy triple is still emitted unchanged.
            assert out["ion_composition_mode"] == "molecular"
        finally:
            dlg.deleteLater()

    def test_dialog_exposes_help_button(self, qapp):
        from PySide6.QtWidgets import QDialogButtonBox, QPushButton
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            found = False
            for btn in dlg.findChildren(QPushButton):
                if btn.text().lower().startswith("help") or \
                        btn.text().lower() == "&help" or \
                        "help" in btn.text().lower():
                    found = True
                    break
            assert found, "Experiment dialog should expose a Help button"
        finally:
            dlg.deleteLater()


# ---------------------------------------------------------------------------
# Help: module + dialog
# ---------------------------------------------------------------------------
class TestExperimentHelp:
    def test_help_html_covers_expected_sections(self):
        from dlp_experiment_help import HELP_HTML
        # Section headings / key phrases the operator MUST see.
        for needle in (
            "Gas rows",
            "Inert vs molecular",
            "Ion-composition modes",
            "O<sub>2</sub> + Xe",
            "flow-weighted",
        ):
            assert needle in HELP_HTML, \
                f"help missing section: {needle}"

    def test_help_html_mentions_xenon_stays_in_mixture(self):
        # Specifically called out in the user request: when the
        # mixture is O2 + Xe, Xe must still be included.  The help
        # must say so in plain language.
        from dlp_experiment_help import HELP_HTML
        assert "Xe" in HELP_HTML
        assert "inert" in HELP_HTML.lower()

    def test_help_dialog_constructs_and_shows(self, qapp):
        from dlp_experiment_help import ExperimentHelpDialog
        dlg = ExperimentHelpDialog()
        try:
            dlg._dlg.show()
            qapp.processEvents()
            assert dlg._dlg.isVisible()
        finally:
            dlg._dlg.close()
            dlg._dlg.deleteLater()


# ---------------------------------------------------------------------------
# LPmeasurement: per-gas forwarded into the ion-mass helper
# ---------------------------------------------------------------------------
class TestLPmeasurementForwardsPerGas:
    def test_build_lp_gas_context_uses_per_gas(self, qapp, monkeypatch):
        # Build a stub object that only carries the shape
        # ``_build_lp_gas_context`` needs, then call the method
        # directly.  Avoids standing up the whole LPMainWindow.
        from LPmeasurement import LPMainWindow
        from dlp_experiment_dialog import _U_TO_KG
        # Use __new__ to skip the real constructor (which would need
        # a full Qt stack).
        stub = LPMainWindow.__new__(LPMainWindow)
        stub._experiment_params = {
            "gases": [
                {"gas": "O2", "flow_sccm": 0.1},
                {"gas": "Xe", "flow_sccm": 0.1},
            ],
            "ion_composition_mode": "molecular",
            "x_atomic": 0.0,
            "x_atomic_unc": 0.0,
            "per_gas_composition": {
                "O2": {"mode": "atomic",
                        "x_atomic": 0.0,
                        "x_atomic_unc": 0.0,
                        "preset": "custom"},
            },
        }
        label, mi_kg, mi_rel_unc = stub._build_lp_gas_context()
        # O2 atomic (16 u) + Xe (131.3 u) → mean (16 + 131.3) / 2.
        u = mi_kg / _U_TO_KG
        assert u == pytest.approx((15.999 + 131.293) / 2, rel=1e-3)
        # Atomic mode carries no ambiguity.
        assert mi_rel_unc == 0.0
        # Label must mention both species.
        assert "O2" in label and "Xe" in label
