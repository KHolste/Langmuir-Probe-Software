"""Tests for the per-method analysis-options ownership split.

After the convergence pass, the Double-probe pipeline reads its
own knobs from :class:`dlp_double_options.DoubleAnalysisOptions`
and never falls back to the Single-probe options.  These tests
prove three things:

1.  V2's ``_run_analysis`` delegates the math to the pure
    :func:`dlp_double_analysis.compute_double_analysis` instead of
    keeping its own duplicate pipeline.
2.  Mutating :attr:`LPMainWindow._single_analysis_options` does not
    influence the Double-probe path (no silent leak).
3.  ``DoubleAnalysisOptions`` survives the JSON config round-trip
    via ``LPMainWindow.get_config`` / ``apply_config``.
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


def _make_double_iv(n=80):
    from fake_b2901_v2 import FakeB2901v2
    f = FakeB2901v2(model="double_langmuir", te_eV=4.0,
                    sheath_conductance=5e-6,
                    current_compliance=10.0)
    f.connect(); f.output(True)
    V = np.linspace(-50, 50, n)
    I = []
    for v in V:
        f.set_voltage(v)
        I.append(f.read_current())
    return V, np.array(I)


# ---------------------------------------------------------------------------
class TestV2RuntimeOnPureFunction:
    """V2 must delegate to compute_double_analysis — proving the
    convergence (no duplicated math path remains)."""

    def test_v2_run_analysis_calls_pure_function(self, qapp,
                                                  monkeypatch):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        import dlp_double_analysis as _dda
        win = DLPMainWindowV2()
        try:
            V, I = _make_double_iv()
            win._v_soll = list(V); win._v_ist = list(V)
            win._i_mean = list(I); win._i_std = [0.0] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            win._fit_model = "tanh_slope"

            calls = {"n": 0, "kwargs": None}
            real = _dda.compute_double_analysis

            def _spy(V_, I_, **kw):
                calls["n"] += 1
                calls["kwargs"] = kw
                return real(V_, I_, **kw)

            monkeypatch.setattr(_dda, "compute_double_analysis", _spy)
            win._run_analysis()
            assert calls["n"] == 1, (
                "V2 _run_analysis must dispatch through "
                "compute_double_analysis exactly once.")
            kw = calls["kwargs"]
            assert kw["fit_model"] == "tanh_slope"
            assert kw["sat_fraction"] == win.spnSatFrac.value()
            assert kw["probe_params"] is win._probe_params
        finally:
            win.close()

    def test_v2_state_keys_preserved(self, qapp):
        """`_last_fit`, `_last_model_fit`, `_last_plasma`,
        `_last_comparison` must still be populated after the
        delegation refactor — downstream HTML / history readers
        depend on them."""
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        try:
            V, I = _make_double_iv()
            win._v_soll = list(V); win._v_ist = list(V)
            win._i_mean = list(I); win._i_std = [0.0] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            win._fit_model = "tanh_slope"
            win._run_analysis()
            assert win._last_fit is not None
            assert win._last_model_fit is not None
            assert win._last_plasma is not None
            # comparison is an iterable; emptiness is OK as long as
            # the attribute exists (V2 stored an empty list on failure).
            assert hasattr(win, "_last_comparison")
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestPerMethodOwnershipSeparation:
    """Single options must NOT leak into the Double pipeline and
    vice-versa — the whole point of the ownership split."""

    def test_single_compliance_setting_does_not_disable_double_filter(
            self, qapp):
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        from dlp_double_options import DoubleAnalysisOptions
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            f = FakeB2901v2(model="double_langmuir",
                            current_compliance=10.0)
            f.connect(); f.output(True)
            for v in np.linspace(-50, 50, 80):
                f.set_voltage(v)
                win._v_soll.append(v); win._v_ist.append(v)
                win._i_mean.append(f.read_current())
                win._i_std.append(0.0)
                win._directions.append("fwd")
                win._compliance.append(False)
            for i in range(70, 80):
                win._compliance[i] = True

            # Single says "include all" — this MUST NOT disable
            # the Double-side compliance filter (it used to, before
            # the ownership split).
            win._single_analysis_options = SingleAnalysisOptions(
                compliance_mode="include_all")
            win._double_analysis_options = DoubleAnalysisOptions(
                compliance_mode="exclude_clipped")
            win._dataset_method = "double"
            win.txtLog.clear()
            win._run_analysis()
            log_text = win.txtLog.toPlainText().lower()
            assert "excluded" in log_text and "compliance" in log_text, (
                "Double's own compliance_mode=exclude_clipped must "
                "still drive the buffer-swap exclusion regardless of "
                "what Single's options say.")
        finally:
            win.close()

    def test_double_compliance_setting_does_not_affect_single(
            self, qapp, monkeypatch):
        """Mutating Double's options must not change what Single's
        ``_run_single_analysis`` passes to ``analyze_single_iv`` —
        Single still consults its own ``_single_analysis_options``."""
        from LPmeasurement import LPMainWindow
        from dlp_single_options import SingleAnalysisOptions
        from dlp_double_options import DoubleAnalysisOptions
        from fake_b2901_v2 import FakeB2901v2
        win = LPMainWindow()
        try:
            f = FakeB2901v2(model="single_probe",
                            current_compliance=10.0,
                            sheath_conductance=0.0,
                            electron_sat_slope=0.0)
            f.connect(); f.output(True)
            for v in np.linspace(-50, 50, 100):
                f.set_voltage(v)
                win._v_ist.append(v)
                win._i_mean.append(f.read_current())
                win._compliance.append(False)
                win._directions.append("fwd")
            win._compliance[80] = True

            # Single keeps the default "exclude_clipped"; flipping
            # Double to "include_all" must NOT influence Single.
            win._single_analysis_options = SingleAnalysisOptions(
                compliance_mode="exclude_clipped")
            win._double_analysis_options = DoubleAnalysisOptions(
                compliance_mode="include_all")
            win._dataset_method = "single"

            captured = {}
            from dlp_single_analysis import analyze_single_iv as _orig

            def _spy(*a, **k):
                captured.update(k)
                return _orig(*a, **k)

            monkeypatch.setattr(
                "dlp_single_analysis.analyze_single_iv", _spy)
            win._run_single_analysis()
            assert captured.get("compliance") is not None, (
                "Single must still pass its compliance buffer when "
                "its own option is exclude_clipped — Double's option "
                "must have no effect on Single's pipeline.")
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestDoubleOptionsPersistence:
    """JSON round-trip parity — the new options must survive
    get_config / apply_config so operator preferences are preserved
    across launches."""

    def test_double_options_serialised_in_get_config(self, qapp):
        from LPmeasurement import LPMainWindow
        from dlp_double_options import DoubleAnalysisOptions
        win = LPMainWindow()
        try:
            win._double_analysis_options = DoubleAnalysisOptions(
                compliance_mode="include_all",
                hysteresis_threshold_pct=12.5)
            cfg = win.get_config()
            assert "double_analysis_options" in cfg
            d = cfg["double_analysis_options"]
            assert d["compliance_mode"] == "include_all"
            assert d["hysteresis_threshold_pct"] == 12.5
        finally:
            win.close()

    def test_double_options_restored_via_apply_config(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.apply_config({"double_analysis_options": {
                "compliance_mode": "include_all",
                "hysteresis_threshold_pct": 8.0}})
            o = win._double_analysis_options
            assert o.compliance_mode == "include_all"
            assert o.hysteresis_threshold_pct == 8.0
        finally:
            win.close()

    def test_double_options_dataclass_defaults_and_validation(self):
        from dlp_double_options import DoubleAnalysisOptions
        d = DoubleAnalysisOptions()
        assert d.compliance_mode == "exclude_clipped"
        assert d.hysteresis_threshold_pct == 5.0
        # Invalid values fall back to defaults.
        d2 = DoubleAnalysisOptions.from_dict({
            "compliance_mode": "garbage",
            "hysteresis_threshold_pct": "abc"})
        assert d2.compliance_mode == "exclude_clipped"
        assert d2.hysteresis_threshold_pct == 5.0
        # None → defaults.
        assert DoubleAnalysisOptions.from_dict(None) == DoubleAnalysisOptions()
