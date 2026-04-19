"""Plausibility tests for the LP simulation path.

In simulation mode the Triple-Probe demo numbers must land in a
realistic plasma-physics window:
    * Te in [3, 5] eV,
    * n_e positive and around 1e17 m⁻³.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
class TestWorkerSimCurrent:
    def test_sim_current_overrides_smu_read(self, qapp):
        from dlp_triple_worker import TripleProbeWorker
        smu = MagicMock(); smu.read_current.return_value = +9.99
        smu.read_voltage.return_value = 25.0
        k = MagicMock(); k.read_voltage.return_value = 3.0
        w = TripleProbeWorker(
            smu, k, v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
            sim_current_a=-3.0e-4)
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start(); w.request_stop()
        # SMU read_current must NOT have been used.
        smu.read_current.assert_not_called()
        assert samples[0]["i_a"] == pytest.approx(-3.0e-4)

    def test_sim_current_yields_plausible_te_and_ne(self, qapp):
        from dlp_triple_worker import TripleProbeWorker
        smu = MagicMock(); smu.read_voltage.return_value = 25.0
        k = MagicMock(); k.read_voltage.return_value = 3.0
        w = TripleProbeWorker(
            smu, k, v_d12_setpoint=25.0, current_limit_a=0.01,
            species_name="Argon (Ar)", tick_ms=20,
            sim_current_a=-3.0e-4)
        samples = []
        w.sample.connect(lambda s: samples.append(s))
        w.start(); w.request_stop()
        s = samples[0]
        assert 3.0 <= s["Te_eV"] <= 5.0, s["Te_eV"]
        assert s["n_e_m3"] > 0
        # Within an order of magnitude of 1e17 m⁻³.
        assert 3e16 <= s["n_e_m3"] <= 3e17, s["n_e_m3"]


# ---------------------------------------------------------------------------
class TestLPMainWindowSimPath:
    def test_open_with_sim_devices_pre_tunes_k2000_and_passes_current(
            self, qapp):
        from LPmeasurement import LPMainWindow
        from fake_b2901_v2 import FakeB2901v2
        from fake_keithley_2000 import FakeKeithley2000
        win = LPMainWindow()
        try:
            win.smu = FakeB2901v2(current_compliance=0.01)
            win.smu.connect()
            win.k2000 = FakeKeithley2000()
            win.k2000.connect()
            win._open_triple_window()
            # K2000 demo voltage was bumped to 3.0 V.
            assert win.k2000.read_voltage() == pytest.approx(3.0)
            # Sim-current override was forwarded to the LP window.
            lp = win._lp_window
            assert lp._sim_current_a == pytest.approx(-3.0e-4)
        finally:
            win.close()

    def test_open_with_real_devices_does_not_inject_sim_current(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.smu = MagicMock()        # not a Fake instance
            win.k2000 = MagicMock()
            win._open_triple_window()
            lp = win._lp_window
            assert lp._sim_current_a is None
        finally:
            win.close()
