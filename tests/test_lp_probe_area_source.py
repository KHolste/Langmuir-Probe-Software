"""Probe area is owned by Probe Params… in the main window; the
LP/Triple window only mirrors the value as a read-only label."""
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


# ---------------------------------------------------------------------------
class TestLPWindowAreaWidget:
    def test_no_editable_spn_area(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        win = LPMeasurementWindow(MagicMock(), MagicMock())
        assert not hasattr(win, "spnArea")
        assert hasattr(win, "lblArea")
        # Display only — labels never receive keyboard focus by tab.
        assert "mm²" in win.lblArea.text()

    def test_constructor_area_overrides_default(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        win = LPMeasurementWindow(
            MagicMock(), MagicMock(), area_m2=2.5e-5)
        assert win._area_m2 == pytest.approx(2.5e-5)
        # Label reflects the injected value (2.5e-5 m² == 25 mm²).
        assert "25" in win.lblArea.text()

    def test_label_updates_on_show_or_raise_re_inject(self, qapp):
        from PySide6.QtWidgets import QWidget
        from dlp_lp_window import show_or_raise
        p = QWidget()
        win = show_or_raise(p, MagicMock(), MagicMock(),
                             area_m2=1.0e-5)
        assert "10" in win.lblArea.text()  # 1e-5 m² → 10 mm²
        # Re-inject another area; same window comes back updated.
        win2 = show_or_raise(p, MagicMock(), MagicMock(),
                              area_m2=4.0e-5)
        assert win2 is win
        assert "40" in win.lblArea.text()
        p.close()


# ---------------------------------------------------------------------------
class TestMainWindowResolvesAreaFromProbeParams:
    def test_explicit_electrode_area_takes_precedence(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._probe_params = {
                "geometry": "cylindrical",
                "electrode_length_mm": 5.0,
                "electrode_radius_mm": 0.1,
                "electrode_area_mm2": 12.34,   # explicit override
            }
            area = win._build_lp_probe_area_m2()
            assert area == pytest.approx(12.34e-6)
        finally:
            win.close()

    def test_geometric_area_for_cylindrical(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._probe_params = {
                "geometry": "cylindrical",
                "electrode_length_mm": 5.0,
                "electrode_radius_mm": 0.1,
                "electrode_area_mm2": None,
            }
            area = win._build_lp_probe_area_m2()
            expected_mm2 = 2 * math.pi * 0.1 * 5.0
            assert area == pytest.approx(expected_mm2 * 1e-6, rel=1e-9)
        finally:
            win.close()

    def test_open_triple_forwards_area_to_lp_window(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.smu = MagicMock()
            win.k2000 = MagicMock()
            win._probe_params = {
                "geometry": "cylindrical",
                "electrode_length_mm": 5.0,
                "electrode_radius_mm": 0.1,
                "electrode_area_mm2": None,
            }
            win._open_triple_window()
            lp = win._lp_window
            expected_mm2 = 2 * math.pi * 0.1 * 5.0
            assert lp._area_m2 == pytest.approx(expected_mm2 * 1e-6,
                                                 rel=1e-9)
            assert "mm²" in lp.lblArea.text()
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestWorkerReceivesArea:
    def test_worker_constructed_with_window_area(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        win = LPMeasurementWindow(MagicMock(), MagicMock(),
                                    area_m2=7.5e-6)
        with patch("dlp_lp_window.TripleProbeWorker") as mock_cls:
            mock_cls.return_value.is_running = False
            win._on_start()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["area_m2"] == pytest.approx(7.5e-6)
