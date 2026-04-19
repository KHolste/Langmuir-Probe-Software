"""Layout polish contract for the V3 main window.

Three feintuning targets: left column breathes, log uses no-wrap,
methods band is visually present (button height/min-width + even
distribution).
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make():
    from DoubleLangmuir_measure_v3 import DLPMainWindowV3
    return DLPMainWindowV3()


class TestLeftColumnBreathing:
    def test_outer_margins_and_spacing(self, qapp):
        win = _make()
        left = win._splitter_main.widget(0)
        layout = left.layout()
        m = layout.contentsMargins()
        assert m.left() >= 6 and m.top() >= 6
        assert layout.spacing() >= 6

    def test_each_groupbox_has_inner_padding(self, qapp):
        from PySide6.QtWidgets import QGroupBox
        win = _make()
        left = win._splitter_main.widget(0)
        groups = left.findChildren(QGroupBox)
        # at least the four base groups (Instrument/Sweep/Control/Output)
        assert len(groups) >= 4
        for g in groups[:4]:
            inner = g.layout()
            if inner is None:
                continue
            m = inner.contentsMargins()
            assert m.left() >= 6, g.title()


class TestLogReadability:
    def test_log_is_no_wrap(self, qapp):
        from PySide6.QtWidgets import QTextEdit
        win = _make()
        assert win.txtLog.lineWrapMode() == QTextEdit.LineWrapMode.NoWrap


class TestMethodsBandPresence:
    def test_button_min_size(self, qapp):
        win = _make()
        for b in (win.btnMethodSingle, win.btnMethodDouble,
                  win.btnMethodTriple, win.btnMethodCleaning):
            assert b.minimumHeight() >= 28
            assert b.minimumWidth() >= 70

    def test_buttons_share_width_evenly(self, qapp):
        """Trailing stretch is gone — each button has a stretch factor
        of 1, so the row fills the column without dead space."""
        win = _make()
        layout = win.grpMethods.layout()
        # all four button items must have stretch 1; no extra
        # QSpacerItem at the tail.
        for i in range(4):
            assert layout.stretch(i) == 1
        # any 5th item, if present, must NOT be a spacer that pushes
        # the buttons left.
        if layout.count() > 4:
            spacer = layout.itemAt(4)
            assert spacer.widget() is not None or spacer.spacerItem() is None


class TestNoFunctionalRegression:
    def test_three_columns_intact(self, qapp):
        win = _make()
        assert win._splitter_main.count() == 3

    def test_widgets_still_there(self, qapp):
        win = _make()
        for name in ("canvas", "ax", "txtLog", "grpMethods",
                     "btnMethodSingle", "btnK2000Connect"):
            assert hasattr(win, name), name
