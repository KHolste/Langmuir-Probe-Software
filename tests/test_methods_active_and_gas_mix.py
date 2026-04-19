"""Tests for: (1) active-mode marking on the Langmuir Probe Methods
buttons, and (2) wiring the Triple analysis to the central Experiment
gas-mix data."""
from __future__ import annotations

import math
import os
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def lp_main(qapp):
    from LPmeasurement import LPMainWindow
    win = LPMainWindow()
    yield win
    win.close()


# ===========================================================================
# Method-button active state
# ===========================================================================
class TestMethodButtonsActiveState:
    def test_mode_buttons_are_checkable(self, lp_main):
        for b in (lp_main.btnMethodSingle, lp_main.btnMethodDouble,
                  lp_main.btnMethodTriple):
            assert b.isCheckable()

    def test_action_buttons_stay_non_checkable(self, lp_main):
        assert not lp_main.btnMethodCleaning.isCheckable()
        assert not lp_main.btnProbeParams.isCheckable()

    def test_button_group_is_exclusive(self, lp_main):
        assert hasattr(lp_main, "methodGroup")
        assert lp_main.methodGroup.exclusive()
        members = set(lp_main.methodGroup.buttons())
        assert lp_main.btnMethodSingle in members
        assert lp_main.btnMethodDouble in members
        assert lp_main.btnMethodTriple in members
        assert lp_main.btnMethodCleaning not in members

    def test_clicking_one_mode_unchecks_others(self, lp_main):
        # Manually drive the mode change because click() may also
        # open the Triple window in some configurations.
        lp_main.btnMethodSingle.setChecked(True)
        assert lp_main.btnMethodSingle.isChecked()
        assert not lp_main.btnMethodDouble.isChecked()
        assert not lp_main.btnMethodTriple.isChecked()
        lp_main.btnMethodDouble.setChecked(True)
        assert not lp_main.btnMethodSingle.isChecked()
        assert lp_main.btnMethodDouble.isChecked()

    def test_active_style_present(self, lp_main):
        """The Methods groupbox must define a :checked rule so the
        active mode is visually distinct."""
        grp = lp_main.grpMethods
        sheet = grp.styleSheet()
        assert ":checked" in sheet
        assert "border" in sheet


# ===========================================================================
# Gas-mix wiring (Experiment dialog → Triple analysis)
# ===========================================================================
class TestGasMixWiring:
    def test_lp_window_no_longer_has_species_combo(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        win = LPMeasurementWindow(MagicMock(), MagicMock())
        assert hasattr(win, "lblGasMix")
        assert not hasattr(win, "cmbSpecies")

    def test_main_window_builds_label_and_mass_from_experiment(
            self, lp_main):
        # Configure a binary mix in the parent's experiment params.
        lp_main._experiment_params = {
            "gases": [
                {"gas": "Ar", "flow_sccm": 1.0},
                {"gas": "Xe", "flow_sccm": 1.0},
                {"gas": "", "flow_sccm": 0.0},
            ]
        }
        label, mi_kg = lp_main._build_lp_gas_context()
        assert "Ar" in label and "Xe" in label
        assert "sccm" in label
        # Ar 39.948 + Xe 131.293, equal flow → mean ≈ 85.6 u → kg
        expected = (39.948 + 131.293) / 2 * 1.6605e-27
        assert mi_kg == pytest.approx(expected, rel=1e-3)

    def test_default_when_no_flow_falls_back_to_argon(self, lp_main):
        lp_main._experiment_params = {
            "gases": [
                {"gas": "Ar", "flow_sccm": 0.0},
                {"gas": "", "flow_sccm": 0.0},
            ]
        }
        label, mi_kg = lp_main._build_lp_gas_context()
        assert label == "Argon (Ar)"
        assert mi_kg is None  # worker resolves via species_name fallback

    def test_open_triple_forwards_gas_context_to_lp_window(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.smu = MagicMock()
            win.k2000 = MagicMock()
            win._experiment_params = {
                "gases": [
                    {"gas": "Ar", "flow_sccm": 1.0},
                    {"gas": "Xe", "flow_sccm": 1.0},
                ]
            }
            win._open_triple_window()
            lp = win._lp_window
            assert "Ar" in lp._gas_mix_label
            assert "Xe" in lp._gas_mix_label
            assert lp._mi_kg_override is not None
            assert lp.lblGasMix.text() == lp._gas_mix_label
        finally:
            win.close()


# ===========================================================================
# Mixture actually changes the Triple n_e — sanity
# ===========================================================================
class TestMixtureAffectsAnalysis:
    def test_pure_xe_yields_lower_n_e_than_pure_ar(self):
        from dlp_triple_analysis import analyze_sample, mi_from_species
        out_ar = analyze_sample(
            v_d12=25.0, v_d13=3.0, i_measure_a=-3.0e-4,
            mi_kg=mi_from_species("Argon (Ar)"))
        out_xe = analyze_sample(
            v_d12=25.0, v_d13=3.0, i_measure_a=-3.0e-4,
            mi_kg=mi_from_species("Xenon (Xe)"))
        # Heavier ion → lower Bohm velocity → larger n_e.
        assert out_xe["n_e_m3"] > out_ar["n_e_m3"]
        assert math.isfinite(out_ar["n_e_m3"])

    def test_mix_lies_between_pure_components(self):
        from dlp_experiment_dialog import effective_ion_mass_kg
        from dlp_triple_analysis import analyze_sample, mi_from_species
        mi_mix = effective_ion_mass_kg(
            [{"gas": "Ar", "flow_sccm": 1.0},
             {"gas": "Xe", "flow_sccm": 1.0}])
        out_ar = analyze_sample(v_d12=25.0, v_d13=3.0,
                                 i_measure_a=-3.0e-4,
                                 mi_kg=mi_from_species("Argon (Ar)"))
        out_xe = analyze_sample(v_d12=25.0, v_d13=3.0,
                                 i_measure_a=-3.0e-4,
                                 mi_kg=mi_from_species("Xenon (Xe)"))
        out_mix = analyze_sample(v_d12=25.0, v_d13=3.0,
                                  i_measure_a=-3.0e-4,
                                  mi_kg=mi_mix)
        assert out_ar["n_e_m3"] < out_mix["n_e_m3"] < out_xe["n_e_m3"]
