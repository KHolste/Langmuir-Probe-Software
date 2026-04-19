"""Tests for the new pure-function Double-probe analysis module.

Proves that ``compute_double_analysis`` produces a result dict
materially identical to V2's stored ``_last_*`` state on the same
inputs — i.e. the refactor is behaviour-neutral.
"""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_double_iv(n=80, te=4.0, i_sat=2.0e-3, sheath=5e-6):
    from fake_b2901_v2 import FakeB2901v2
    f = FakeB2901v2(model="double_langmuir", te_eV=te,
                    sheath_conductance=sheath,
                    current_compliance=10.0)
    f.connect(); f.output(True)
    f.i_sat = i_sat
    V = np.linspace(-50, 50, n)
    I = []
    for v in V:
        f.set_voltage(v)
        I.append(f.read_current())
    return V, np.array(I)


# ---------------------------------------------------------------------------
class TestPureFunctionShape:
    def test_returns_documented_keys(self):
        from dlp_double_analysis import compute_double_analysis
        V, I = _make_double_iv()
        out = compute_double_analysis(
            V, I, fit_model="tanh_slope",
            sat_fraction=0.20,
            probe_params={"geometry": "cylindrical",
                           "electrode_length_mm": 5.0,
                           "electrode_radius_mm": 0.1},
            gases=[{"gas": "Ar", "flow_sccm": 1.0}])
        assert out["ok"]
        assert set(out.keys()) >= {
            "fit", "model_fit", "plasma", "comparison",
            "ion_label", "ok", "warnings"}
        assert out["fit"]["i_sat_pos"] == pytest.approx(2.25e-3, rel=0.30)
        assert out["model_fit"]["Te_eV"] == pytest.approx(4.0, rel=0.30)
        assert out["plasma"]["n_i_m3"] > 0

    def test_too_few_points_returns_warning(self):
        from dlp_double_analysis import compute_double_analysis
        out = compute_double_analysis(
            np.array([0.0, 1.0]), np.array([0.0, 1.0]),
            fit_model="tanh_slope")
        assert not out["ok"]
        assert any("less than 10" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
class TestParityWithV2:
    """Run V2's _run_analysis on the same buffers and compare its
    stored state to the pure function's output.  Allows a small
    numerical tolerance because V2 also goes through Qt slots and
    plot updates, but the math should be byte-identical."""

    def test_pure_matches_v2_state(self, qapp):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        from dlp_double_analysis import compute_double_analysis
        win = DLPMainWindowV2()
        try:
            V, I = _make_double_iv()
            win._v_soll = list(V); win._v_ist = list(V)
            win._i_mean = list(I); win._i_std = [0.0] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            win._fit_model = "tanh_slope"
            win._run_analysis()
            v2_fit = win._last_fit
            v2_mfit = win._last_model_fit
            v2_plasma = win._last_plasma

            pure = compute_double_analysis(
                V, I, fit_model="tanh_slope",
                sat_fraction=win.spnSatFrac.value(),
                probe_params=win._probe_params,
                gases=win._experiment_params.get("gases", []))
            # Saturation fit numbers must match.
            assert pure["fit"]["i_sat_pos"] == pytest.approx(
                v2_fit["i_sat_pos"], rel=1e-9, abs=1e-15)
            assert pure["fit"]["i_sat_neg"] == pytest.approx(
                v2_fit["i_sat_neg"], rel=1e-9, abs=1e-15)
            # Te match within fp noise.
            assert pure["model_fit"]["Te_eV"] == pytest.approx(
                v2_mfit["Te_eV"], rel=1e-6)
            # Plasma density match.
            if not np.isnan(v2_plasma["n_i_m3"]):
                assert pure["plasma"]["n_i_m3"] == pytest.approx(
                    v2_plasma["n_i_m3"], rel=1e-6)
        finally:
            win.close()
