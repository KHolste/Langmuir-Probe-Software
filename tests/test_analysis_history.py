"""Tests for the persistent analysis history helper and log window."""
from __future__ import annotations

import os
import pathlib
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from analysis_history import (  # noqa: E402
    AnalysisRecord, append_record, load_records, default_history_path,
)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------
class TestHistoryFile:
    def test_missing_file_returns_empty_list(self, tmp_path):
        p = tmp_path / "nope.txt"
        assert load_records(str(p)) == []

    def test_corrupt_file_returns_empty_list(self, tmp_path):
        p = tmp_path / "analysis_history.txt"
        p.write_bytes(b"\xff\xfe\x00\x01 not-a-valid-block")
        # Corrupt-but-readable: header pattern is absent → empty list.
        assert load_records(str(p)) == []

    def test_append_creates_file_and_parent_dir(self, tmp_path):
        p = tmp_path / "sub" / "missing" / "history.txt"
        rec = append_record("hello", path=str(p))
        assert p.is_file()
        assert rec.body == "hello"
        assert rec.timestamp  # non-empty

    def test_append_is_additive(self, tmp_path):
        p = tmp_path / "hist.txt"
        append_record("first entry", path=str(p))
        time.sleep(0.01)
        append_record("second entry", path=str(p))
        recs = load_records(str(p))
        assert len(recs) == 2
        # newest-first
        assert recs[0].body == "second entry"
        assert recs[1].body == "first entry"

    def test_explicit_timestamp_is_preserved(self, tmp_path):
        p = tmp_path / "hist.txt"
        append_record("body", path=str(p), timestamp="2026-04-17T12:00:00")
        recs = load_records(str(p))
        assert recs[0].timestamp == "2026-04-17T12:00:00"

    def test_multiline_body_roundtrip(self, tmp_path):
        p = tmp_path / "hist.txt"
        body = "T_e  = 2.34 eV\nI_sat = 1.23e-03 A\nR^2   = 0.9876"
        append_record(body, path=str(p), timestamp="2026-04-17T10:00:00")
        recs = load_records(str(p))
        assert recs[0].body == body

    def test_default_path_points_inside_data_dir(self):
        p = default_history_path()
        assert p.endswith(os.path.join("data", "analysis_history.txt"))

    def test_written_block_header_format(self, tmp_path):
        p = tmp_path / "hist.txt"
        append_record("x", path=str(p), timestamp="2026-04-17T10:00:00")
        text = p.read_text(encoding="utf-8")
        assert "=== 2026-04-17T10:00:00 ===" in text
        # Block is terminated by a blank line so subsequent appends stay readable.
        assert text.endswith("\n\n")


# ---------------------------------------------------------------------------
# Analysis log window – GUI-near but fast
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestAnalysisLogWindow:
    def test_empty_history_yields_empty_view(self, qapp, tmp_path):
        from analysis_log_window import AnalysisLogWindow
        w = AnalysisLogWindow(history_path=str(tmp_path / "no-file.txt"))
        assert w.txt.toPlainText() == ""

    def test_prepend_puts_new_entry_on_top(self, qapp):
        from analysis_log_window import AnalysisLogWindow
        w = AnalysisLogWindow(history_path=None)
        w.clear_view()
        w.prepend_record(AnalysisRecord("2026-04-17T10:00:00", "first"))
        w.prepend_record(AnalysisRecord("2026-04-17T11:00:00", "second"))
        text = w.txt.toPlainText()
        # "second" must appear before "first" in the view
        pos_second = text.find("2026-04-17T11:00:00")
        pos_first = text.find("2026-04-17T10:00:00")
        assert pos_second >= 0 and pos_first > pos_second

    def test_reload_shows_persisted_history_newest_first(self, qapp, tmp_path):
        from analysis_log_window import AnalysisLogWindow
        hist = tmp_path / "hist.txt"
        append_record("older", path=str(hist), timestamp="2026-04-17T09:00:00")
        append_record("newer", path=str(hist), timestamp="2026-04-17T12:00:00")

        w = AnalysisLogWindow(history_path=str(hist))
        text = w.txt.toPlainText()
        pos_newer = text.find("2026-04-17T12:00:00")
        pos_older = text.find("2026-04-17T09:00:00")
        assert 0 <= pos_newer < pos_older

    def test_clear_view_leaves_file_intact(self, qapp, tmp_path):
        from analysis_log_window import AnalysisLogWindow
        hist = tmp_path / "hist.txt"
        append_record("keep me", path=str(hist))

        w = AnalysisLogWindow(history_path=str(hist))
        assert "keep me" in w.txt.toPlainText()
        w.clear_view()
        assert w.txt.toPlainText() == ""
        # File was not touched
        assert hist.read_text(encoding="utf-8").count("keep me") == 1

    def test_show_or_raise_is_singleton(self, qapp):
        from analysis_log_window import show_or_raise
        from PySide6.QtWidgets import QWidget
        host = QWidget()
        w1 = show_or_raise(host)
        w2 = show_or_raise(host)
        assert w1 is w2

    def test_html_escaping_protects_view(self, qapp):
        from analysis_log_window import AnalysisLogWindow
        w = AnalysisLogWindow(history_path=None)
        w.prepend_record(AnalysisRecord(
            "2026-04-17T10:00:00",
            "dangerous <script>alert('x')</script> tag"))
        # The literal <script> tag must NOT end up rendered – it is
        # escaped, so the raw text still shows it.
        assert "<script>" in w.txt.toPlainText()


# ---------------------------------------------------------------------------
# Integration: DLPMainWindowV2 run_analysis writes history + opens window
# ---------------------------------------------------------------------------
class TestRunAnalysisIntegration:
    def _feed_data(self, win):
        from fake_b2901_v2 import FakeB2901v2
        V = np.linspace(-30, 30, 61)
        f = FakeB2901v2(i_sat=2e-3, sheath_conductance=5e-5, seed=0)
        f.connect(); f.output(True)
        I = []
        for v in V:
            f.set_voltage(v)
            I.append(f.read_current())
        win._v_ist = list(V)
        win._i_mean = list(np.array(I))

    def test_analysis_appends_one_block_to_history(self, qapp, tmp_path):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        hist = tmp_path / "analysis_history.txt"
        win._analysis_history_path = str(hist)
        self._feed_data(win)

        win._run_analysis()

        recs = load_records(str(hist))
        assert len(recs) == 1
        assert "T_e" in recs[0].body

    def test_two_analyses_produce_two_records_newest_first(
            self, qapp, tmp_path):
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        hist = tmp_path / "analysis_history.txt"
        win._analysis_history_path = str(hist)
        self._feed_data(win)

        win._run_analysis()
        time.sleep(0.01)
        win._run_analysis()

        recs = load_records(str(hist))
        assert len(recs) == 2
        assert recs[0].timestamp >= recs[1].timestamp

    def test_analysis_log_window_defaults_off(self, qapp, tmp_path):
        # Contract change (Double-probe analyze-log toggle): with
        # the flag off — the new default — Analyze must NOT pop the
        # separate log window.  The persistent history file is
        # still written (covered by the sibling persistence tests
        # in this class).
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        win._analysis_history_path = str(tmp_path / "hist.txt")
        self._feed_data(win)
        assert getattr(win, "_analysis_window", None) is None
        win._run_analysis()
        assert getattr(win, "_analysis_window", None) is None

    def test_analysis_log_window_reused_when_opted_in(self, qapp, tmp_path):
        # With the flag explicitly on, the singleton-reuse contract
        # still holds: repeated Analyze clicks reuse the same window
        # instead of spawning duplicates.
        from DoubleLangmuir_measure_v2 import DLPMainWindowV2
        win = DLPMainWindowV2()
        win._analysis_history_path = str(tmp_path / "hist.txt")
        win._show_analysis_log = True
        self._feed_data(win)
        win._run_analysis()
        first = win._analysis_window
        assert first is not None
        win._run_analysis()
        assert win._analysis_window is first  # reused, not recreated


# ---------------------------------------------------------------------------
# Layout changes – scrollable central + vertical splitter on the right
# ---------------------------------------------------------------------------
class TestLayoutResizability:
    def test_central_is_scroll_area(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        central = win.centralWidget()
        from PySide6.QtWidgets import QScrollArea
        assert isinstance(central, QScrollArea)
        assert central.widgetResizable() is True

    def test_right_side_has_vertical_splitter(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSplitter
        win = DLPMainWindow()
        splitter = win.findChild(QSplitter, "splitRight")
        assert splitter is not None
        assert splitter.orientation() == Qt.Orientation.Vertical
        assert splitter.count() == 2  # plot + log

    def test_scroll_policies_are_as_needed(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        from PySide6.QtCore import Qt
        win = DLPMainWindow()
        central = win.centralWidget()
        assert central.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert central.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
