"""Regression guard: plot, analysis and CSV must use Ist (readback) data.

These tests deliberately feed *divergent* setpoint vs. readback values
(``v_soll != v_ist``) into the DLP pipeline.  If a future refactor
accidentally swaps the two, the divergence shows up as a failed
assertion here – instead of as silently wrong measurements.

Touched paths:
    * Plot lines on the v2 main window
    * In-window analysis (`_run_analysis`)
    * CSV export (`write_csv`)
    * CSV reload via `parse_dlp_csv`
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from DoubleLangmuir_measure import write_csv, make_csv_path  # noqa: E402
from DoubleLangmuirAnalysis_v2 import parse_dlp_csv  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _divergent_buffers(n: int = 20):
    """Return v_soll, v_ist, i_mean such that soll and ist clearly differ."""
    v_soll = np.linspace(-10.0, 10.0, n)
    # Ist is shifted by a deterministic offset so any swap is visible.
    v_ist = v_soll + 0.7
    # Current depends on Ist – realistic SMU behaviour.
    i_mean = 1e-3 * np.tanh(v_ist / 3.0)
    return v_soll.tolist(), v_ist.tolist(), i_mean.tolist()


# ---------------------------------------------------------------------------
# Pure function: write_csv keeps both columns separate
# ---------------------------------------------------------------------------
class TestCsvKeepsBothColumns:
    def test_csv_first_two_columns_are_soll_then_ist(self, tmp_path):
        v_soll, v_ist, i_mean = _divergent_buffers(5)
        i_std = [0.0] * 5
        directions = ["fwd"] * 5
        compl = [False] * 5
        path = tmp_path / "out.csv"
        write_csv(path, {"Date": "test"},
                  v_soll, i_mean, i_std, v_ist,
                  directions, compl)

        text = path.read_text(encoding="utf-8")
        # Header order is fixed: V_soll_V then V_ist_V
        assert "# V_soll_V,V_ist_V,I_mean_A,I_std_A,dir,compl" in text

        # Data rows: parse and assert column 1 == v_soll, column 2 == v_ist
        rows = [l for l in text.splitlines()
                if l and not l.startswith("#")]
        assert len(rows) == 5
        for row, vs, vi in zip(rows, v_soll, v_ist):
            parts = row.split(",")
            assert float(parts[0]) == pytest.approx(vs)
            assert float(parts[1]) == pytest.approx(vi)
            # Crucially, column 2 must NOT equal v_soll when soll!=ist
            assert float(parts[1]) != pytest.approx(vs)

    def test_parser_reads_back_both_columns(self, tmp_path):
        v_soll, v_ist, i_mean = _divergent_buffers(8)
        path = tmp_path / "out.csv"
        write_csv(path, {"Date": "test"},
                  v_soll, i_mean, [0.0] * 8, v_ist,
                  ["fwd"] * 8, [False] * 8)

        # write_csv uses %.6g formatting → rel tolerance 1e-4 is enough
        # to verify "soll/ist routed correctly" without false positives.
        meta, data = parse_dlp_csv(str(path))
        assert list(data["V_soll"]) == pytest.approx(v_soll, rel=1e-4)
        assert list(data["V_ist"]) == pytest.approx(v_ist, rel=1e-4)
        assert list(data["I_mean"]) == pytest.approx(i_mean, rel=1e-4)
        # Standalone analysis CLI uses data["V_ist"] – which here is
        # provably different from V_soll.
        assert any(abs(a - b) > 1e-6
                   for a, b in zip(data["V_soll"], data["V_ist"]))


# ---------------------------------------------------------------------------
# GUI-near: plot lines and analysis come from Ist, not Soll
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class TestPlotUsesIst:
    """Drive the live ``_on_point`` slot end-to-end so the plot lines are
    populated by the production code path, not by us pre-seeding the
    buffers."""

    def _emit_all(self, win, n: int = 20):
        v_soll, v_ist, i_mean = _divergent_buffers(n)
        for idx in range(n):
            win._on_point(idx, n, v_soll[idx], v_ist[idx],
                          i_mean[idx], 0.0, False, "fwd")
        return v_soll, v_ist, i_mean

    def test_v2_plot_x_data_is_v_ist(self, qapp):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        v_soll, v_ist, i_mean = self._emit_all(win)
        x = list(win.line_fwd.get_xdata())
        y = list(win.line_fwd.get_ydata())
        assert x == pytest.approx(v_ist), \
            "Plot x-axis must use Ist (readback), not Soll"
        assert y == pytest.approx(i_mean)
        # Also assert it's NOT the soll list (paranoia for divergence>0)
        assert any(abs(a - b) > 1e-6 for a, b in zip(x, v_soll))

    def test_base_plot_x_data_is_v_ist(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        v_soll, v_ist, i_mean = self._emit_all(win)
        x = list(win.line_fwd.get_xdata())
        assert x == pytest.approx(v_ist)
        assert any(abs(a - b) > 1e-6 for a, b in zip(x, v_soll))


class TestAnalysisUsesIst:
    def test_run_analysis_consumes_v_ist(self, qapp, monkeypatch):
        """Hijack ``fit_saturation_branches`` to capture the V it gets.

        After the V2-on-pure-function convergence, V2 no longer
        imports ``fit_saturation_branches`` itself — the call lives
        inside :mod:`dlp_double_analysis.compute_double_analysis`,
        which lazy-imports it from :mod:`DoubleLangmuirAnalysis_v2`.
        Patch the source module so the hook still fires regardless of
        which caller resolves the symbol.
        """
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        import DoubleLangmuirAnalysis_v2 as dlp_ana

        captured: dict[str, np.ndarray] = {}

        def fake_fit(V, I, sat_fraction=0.20):
            captured["V"] = np.asarray(V).copy()
            captured["I"] = np.asarray(I).copy()
            # Return a minimal valid fit so the rest of the pipeline runs
            return {
                "v_pos_min": float(V.max() - 1.0),
                "v_neg_max": float(V.min() + 1.0),
                "slope_pos": 0.0, "intercept_pos": 0.0,
                "slope_neg": 0.0, "intercept_neg": 0.0,
                "slope_avg": 0.0,
                "i_sat_pos": 1e-3, "i_sat_neg": -1e-3,
                "n_pos": 4, "n_neg": 4,
            }

        monkeypatch.setattr(dlp_ana, "fit_saturation_branches", fake_fit)

        win = DLPMainWindowV2()
        v_soll, v_ist, i_mean = _divergent_buffers(30)
        win._v_soll = list(v_soll)
        win._v_ist = list(v_ist)
        win._i_mean = list(i_mean)

        # Run analysis – may raise downstream because we returned a stub
        # fit; we only care that fit_saturation_branches saw v_ist.
        try:
            win._run_analysis()
        except Exception:
            pass

        assert "V" in captured, "fit_saturation_branches was not called"
        assert list(captured["V"]) == pytest.approx(v_ist), \
            "Analysis must run on Ist values, not Soll"
        assert list(captured["I"]) == pytest.approx(i_mean)
        # Sanity: v_ist actually differs from v_soll
        assert any(abs(a - b) > 1e-6
                   for a, b in zip(captured["V"], v_soll))


# ---------------------------------------------------------------------------
# End-to-end: CSV written by the live save path keeps Ist intact
# ---------------------------------------------------------------------------
class TestSaveCsvRoundtrip:
    def test_v2_save_csv_writes_v_ist_column_with_actual_ist(self, qapp,
                                                              tmp_path):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        v_soll, v_ist, i_mean = _divergent_buffers(12)
        win._v_soll = list(v_soll)
        win._v_ist = list(v_ist)
        win._i_mean = list(i_mean)
        win._i_std = [0.0] * 12
        win._directions = ["fwd"] * 12
        win._compliance = [False] * 12
        win._save_folder = tmp_path
        win.chkSave.setChecked(True)
        win.chkAutoAnalyze.setChecked(False)

        win._save_csv(run_status="completed")

        csvs = list(tmp_path.rglob("LP_*.csv"))
        assert len(csvs) == 1
        meta, data = parse_dlp_csv(str(csvs[0]))
        assert list(data["V_soll"]) == pytest.approx(v_soll, rel=1e-4)
        assert list(data["V_ist"]) == pytest.approx(v_ist, rel=1e-4)
        # Hard guarantee: the Ist column must NOT have been overwritten
        # with the Soll list.
        assert any(abs(a - b) > 1e-6
                   for a, b in zip(data["V_soll"], data["V_ist"]))
