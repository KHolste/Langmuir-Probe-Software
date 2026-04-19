"""Layout contract for the 'Langmuir Probe Methods' selector
that sits below the plot in V3.

Iteration scope: structure only — buttons exist, are arranged in
the documented left-to-right order, and carry no logic yet.
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


class TestMethodsGroupExists:
    def test_groupbox_attribute_and_title(self, qapp):
        win = _make()
        assert hasattr(win, "grpMethods")
        assert win.grpMethods.title() == "Langmuir Probe Methods"

    def test_buttons_exist_as_attributes(self, qapp):
        win = _make()
        for name in ("btnMethodSingle", "btnMethodDouble",
                     "btnMethodTriple", "btnMethodCleaning"):
            assert hasattr(win, name), name


class TestMethodsGroupOrder:
    def test_buttons_are_in_documented_left_to_right_order(self, qapp):
        win = _make()
        layout = win.grpMethods.layout()
        # extract the button labels in the order Qt sees them
        labels = []
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is not None and hasattr(w, "text"):
                labels.append(w.text())
        assert labels[:4] == ["Single", "Double", "Triple", "Cleaning"]

    def test_methods_group_sits_below_the_plot(self, qapp):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        win = _make()
        # column 2 is the wrapper widget that holds plot + methods
        col2 = win._splitter_main.widget(1)
        plot_canvases = col2.findChildren(FigureCanvasQTAgg)
        assert len(plot_canvases) == 1
        # the methods group must be a child of the same column wrapper
        assert win.grpMethods.parentWidget() is col2

    def test_buttons_have_no_logic_attached_yet(self, qapp):
        """Iteration scope guard: buttons must NOT yet trigger any
        measurement logic — the connection list stays empty."""
        win = _make()
        # PySide6 exposes receivers() as a private API; instead we
        # simply confirm clicked() does not raise and does not change
        # any observable state on the window.  Snapshot before/after.
        before = (win.spnVstart.value(), win.spnVstop.value(),
                  win.spnSettle.value())
        for b in (win.btnMethodSingle, win.btnMethodDouble,
                  win.btnMethodTriple, win.btnMethodCleaning):
            b.click()
        after = (win.spnVstart.value(), win.spnVstop.value(),
                 win.spnSettle.value())
        assert before == after


class TestNoFunctionalRegression:
    def test_three_columns_still_in_place(self, qapp):
        win = _make()
        assert win._splitter_main.count() == 3

    def test_plot_log_k2000_still_present(self, qapp):
        win = _make()
        for name in ("canvas", "ax", "txtLog",
                     "btnK2000Connect", "btnK2000Read"):
            assert hasattr(win, name), name
