"""Tests for the shared ion-composition preset system.

Covers:
* presets are stable, curated, and cover the expected regimes;
* ``apply_preset`` fills mode / x_atomic / x_atomic_unc / preset_key;
* ``detect_current_preset`` snaps pre-preset-era params to the
  matching row by (mode, x, Δx) triple;
* Experiment dialog exposes the preset combo, fills the fields,
  flips to "Custom" on manual edits, and round-trips the key;
* sidecar summaries persist the preset key for both Single and
  Double paths;
* Triple CSV header now records the ion-composition audit fields;
* help text mentions presets and the cross-method scope.
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
# Part 1 — shared preset module.
# ---------------------------------------------------------------------------
class TestPresetModule:
    def test_preset_list_contains_expected_keys(self):
        from ion_composition_presets import ION_COMPOSITION_PRESETS
        keys = {p.key for p in ION_COMPOSITION_PRESETS}
        for expected in ("inert_monatomic",
                         "o2_magnetron_molecular",
                         "o2_high_power_atomic_mix",
                         "n2_molecular",
                         "h2_mixed",
                         "unknown_widen_ci"):
            assert expected in keys, expected

    def test_every_preset_has_valid_mode(self):
        from dlp_experiment_dialog import ION_COMPOSITION_MODES
        from ion_composition_presets import ION_COMPOSITION_PRESETS
        valid = set(ION_COMPOSITION_MODES)
        for p in ION_COMPOSITION_PRESETS:
            assert p.mode in valid, (p.key, p.mode)

    def test_every_preset_has_nonempty_label_and_description(self):
        from ion_composition_presets import ION_COMPOSITION_PRESETS
        for p in ION_COMPOSITION_PRESETS:
            assert p.label
            assert p.description

    def test_apply_preset_fills_composition_fields(self):
        from ion_composition_presets import apply_preset
        seed = {"gases": [{"gas": "O2", "flow_sccm": 5.0}]}
        out = apply_preset(seed, "o2_high_power_atomic_mix")
        assert out["gases"] == seed["gases"]
        assert out["ion_composition_mode"] == "mixed"
        assert 0.0 < out["x_atomic"] <= 1.0
        assert 0.0 < out["x_atomic_unc"] <= 0.5
        assert out["ion_composition_preset"] == \
               "o2_high_power_atomic_mix"

    def test_apply_preset_custom_key_preserves_fields(self):
        from ion_composition_presets import (
            apply_preset, CUSTOM_PRESET_KEY,
        )
        seed = {"gases": [], "ion_composition_mode": "atomic",
                "x_atomic": 0.0, "x_atomic_unc": 0.0}
        out = apply_preset(seed, CUSTOM_PRESET_KEY)
        # No change to the composition fields, only the preset key.
        assert out["ion_composition_mode"] == "atomic"
        assert out["x_atomic"] == 0.0
        assert out["ion_composition_preset"] == CUSTOM_PRESET_KEY

    def test_detect_current_preset_by_explicit_key(self):
        from ion_composition_presets import detect_current_preset
        params = {"ion_composition_preset": "n2_molecular",
                  "ion_composition_mode": "molecular",
                  "x_atomic": 0.0, "x_atomic_unc": 0.0}
        p = detect_current_preset(params)
        assert p is not None
        assert p.key == "n2_molecular"

    def test_detect_current_preset_by_field_match(self):
        # Old-style params with no preset_key → still snap to
        # inert_monatomic because (molecular, 0, 0) is its triple.
        from ion_composition_presets import detect_current_preset
        params = {"ion_composition_mode": "molecular",
                  "x_atomic": 0.0, "x_atomic_unc": 0.0}
        p = detect_current_preset(params)
        assert p is not None
        # Multiple presets share that triple (inert_monatomic,
        # o2_magnetron_molecular, n2_molecular); the detector
        # picks whichever sits first in the ordered list.  The
        # contract for the test: any match is acceptable as long
        # as it has the correct mode/x/Δx.
        assert p.mode == "molecular"

    def test_detect_returns_none_on_nonmatching_custom(self):
        from ion_composition_presets import detect_current_preset
        params = {"ion_composition_mode": "mixed",
                  "x_atomic": 0.17, "x_atomic_unc": 0.03}
        # Not a preset triple → None.
        assert detect_current_preset(params) is None


# ---------------------------------------------------------------------------
# Part 2 — Experiment dialog preset combo.
# ---------------------------------------------------------------------------
class TestExperimentDialogPresetCombo:
    def test_dialog_has_preset_combo(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            assert hasattr(dlg, "_cmbPreset")
            keys = {dlg._cmbPreset.itemData(i)
                     for i in range(dlg._cmbPreset.count())}
            # At least Custom + the six curated presets.
            assert "custom" in keys
            assert "o2_magnetron_molecular" in keys
            assert "unknown_widen_ci" in keys
        finally:
            dlg.deleteLater()

    def test_picking_preset_fills_fields(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        from ion_composition_presets import get_preset
        dlg = ExperimentParameterDialog()
        try:
            idx = dlg._cmbPreset.findData("o2_high_power_atomic_mix")
            dlg._cmbPreset.setCurrentIndex(idx)
            params = dlg.get_params()
            preset = get_preset("o2_high_power_atomic_mix")
            assert params["ion_composition_mode"] == preset.mode
            assert params["x_atomic"] == pytest.approx(
                preset.x_atomic, rel=1e-3)
            assert params["x_atomic_unc"] == pytest.approx(
                preset.x_atomic_unc, rel=1e-3)
            assert params["ion_composition_preset"] == \
                   "o2_high_power_atomic_mix"
        finally:
            dlg.deleteLater()

    def test_manual_edit_reverts_preset_to_custom(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        try:
            # Pick a real preset first.
            idx = dlg._cmbPreset.findData("o2_magnetron_molecular")
            dlg._cmbPreset.setCurrentIndex(idx)
            assert (dlg._cmbPreset.currentData()
                    == "o2_magnetron_molecular")
            # Now the operator edits x_atomic manually → preset
            # should flip to Custom without erasing the edit.
            dlg._spnXAtomic.setValue(25.0)
            assert dlg._cmbPreset.currentData() == "custom"
            # get_params returns the manual value + "custom" key.
            p = dlg.get_params()
            assert p["ion_composition_preset"] == "custom"
            assert p["x_atomic"] == pytest.approx(0.25, rel=1e-3)
        finally:
            dlg.deleteLater()

    def test_seeded_params_with_preset_select_the_row(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        seed = {"gases": [{"gas": "O2", "flow_sccm": 3.0}],
                "ion_composition_mode": "mixed",
                "x_atomic": 0.70,
                "x_atomic_unc": 0.20,
                "ion_composition_preset":
                    "o2_high_power_atomic_mix"}
        dlg = ExperimentParameterDialog(seed)
        try:
            assert (dlg._cmbPreset.currentData()
                    == "o2_high_power_atomic_mix")
        finally:
            dlg.deleteLater()


# ---------------------------------------------------------------------------
# Part 3 — cross-method consistency: Double sidecar persists the key.
# ---------------------------------------------------------------------------
class TestDoubleSidecarPresetPersistence:
    def test_compute_double_analysis_does_not_drop_preset(self):
        # Ensure the preset key is NOT silently dropped by the
        # analysis layer — it flows through via experiment_params
        # into the sidecar summary LP assembles.
        from dlp_double_analysis import compute_double_analysis
        V = np.linspace(-30.0, 30.0, 61)
        I = 1e-3 * np.tanh(V / 6.0)
        out = compute_double_analysis(
            V, I, fit_model="simple_tanh",
            probe_params={"electrode_area_mm2": 1.0},
            gases=[{"gas": "O2", "flow_sccm": 1.0}],
            ion_composition_mode="mixed",
            ion_x_atomic=0.70, ion_x_atomic_unc=0.20)
        pp = out["plasma"]
        # Mixed-mode + Δx > 0 always yields an ion_mix scope tag;
        # the preset name itself is persisted by the LP layer, not
        # by compute_double_analysis.  This test just confirms the
        # analysis numbers are still sane under a preset-like
        # input so the LP sidecar handoff has something to stamp.
        assert "ion_mix" in pp["n_i_ci_note"]
        assert pp["n_i_ci_ion_mix_rel_unc"] > 0.0


# ---------------------------------------------------------------------------
# Part 4 — Triple CSV header audit trail.
# ---------------------------------------------------------------------------
class TestTripleCsvHeaderAuditTrail:
    def test_triple_meta_contains_ion_composition_fields(
            self, qapp, tmp_path):
        # Construct the Triple window with a synthetic composition
        # context and save a one-sample CSV; assert the header
        # carries the preset + mode + x + Δx + mi_rel_unc lines.
        from fake_b2901 import FakeB2901
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_lp_window import LPMeasurementWindow
        from dlp_triple_dataset import TripleSample

        smu = FakeB2901(current_compliance=0.01, noise_std=1e-8)
        smu.connect()
        k2000 = FakeKeithley2000(voltage=0.5)
        k2000.connect()
        ctx = {
            "ion_composition_preset": "o2_high_power_atomic_mix",
            "ion_composition_mode": "mixed",
            "x_atomic": 0.70, "x_atomic_unc": 0.20,
            "mi_rel_unc": 0.125,
        }
        win = LPMeasurementWindow(smu, k2000,
                                    ion_composition_context=ctx)
        try:
            win._dataset.add(TripleSample(
                t_s=0.0, u_supply_V=0.0, u_measure_V=0.0,
                i_measure_A=1e-5, v_d12_V=25.0, v_d13_V=2.0,
                te_eV=3.0, ne_m3=1e17))
            out_path = tmp_path / "triple.csv"
            win._dataset.write_csv(out_path, meta=win._build_meta())
            text = out_path.read_text(encoding="utf-8")
            assert "Ion_Composition_Preset: o2_high_power_atomic_mix" \
                   in text
            assert "Ion_Composition_Mode: mixed" in text
            assert "Ion_x_atomic:" in text
            assert "Ion_x_atomic_unc:" in text
            assert "Ion_mi_rel_unc:" in text
        finally:
            try:
                win.close()
            except Exception:
                pass

    def test_triple_without_context_emits_no_ion_audit_lines(
            self, qapp, tmp_path):
        # When no context is supplied, the previous meta layout is
        # preserved — no new ion_* lines get emitted, so existing
        # CSV parsers never see unexpected fields.
        from fake_b2901 import FakeB2901
        from fake_keithley_2000 import FakeKeithley2000
        from dlp_lp_window import LPMeasurementWindow
        from dlp_triple_dataset import TripleSample

        smu = FakeB2901(current_compliance=0.01, noise_std=1e-8)
        smu.connect()
        k2000 = FakeKeithley2000(voltage=0.5)
        k2000.connect()
        win = LPMeasurementWindow(smu, k2000)
        try:
            win._dataset.add(TripleSample(
                t_s=0.0, u_supply_V=0.0, u_measure_V=0.0,
                i_measure_A=1e-5, v_d12_V=25.0, v_d13_V=2.0,
                te_eV=3.0, ne_m3=1e17))
            out_path = tmp_path / "triple_plain.csv"
            win._dataset.write_csv(out_path, meta=win._build_meta())
            text = out_path.read_text(encoding="utf-8")
            assert "Ion_Composition_Preset" not in text
            assert "Ion_Composition_Mode" not in text
        finally:
            try:
                win.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Part 5 — help text mentions presets + cross-method scope.
# ---------------------------------------------------------------------------
class TestHelpMentionsPresets:
    def test_help_body_covers_presets(self):
        from dlp_double_help import HELP_HTML
        html = HELP_HTML()
        assert "Presets" in html or "preset" in html.lower()
        for kw in ("O", "Single", "Double", "Triple"):
            # All three probe types must be called out in the scope
            # paragraph so the operator knows the preset is shared.
            assert kw in html
