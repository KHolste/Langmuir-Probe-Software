"""Layout contract for the V3 main window's three-column structure.

Column 1 = left controls (Output is the last visible group).
Column 2 = plot canvas alone.
Column 3 = K2000 group on top, log view below (vertical splitter).
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


class TestThreeColumns:
    def test_main_splitter_has_three_columns(self, qapp):
        win = _make()
        assert win._splitter_main.count() == 3

    def test_column_one_ends_with_output_group(self, qapp):
        from PySide6.QtWidgets import QGroupBox
        win = _make()
        left = win._splitter_main.widget(0)
        groups = [g for g in left.findChildren(QGroupBox)]
        assert groups, "left column has no group boxes"
        # Output group exists and the folder label lives in it.
        titles = [g.title() for g in groups]
        assert "Output" in titles
        # And the K2000 group is NOT in the left column anymore.
        assert "Multimeter (Keithley 2000)" not in titles

    def test_column_two_holds_the_plot_canvas(self, qapp):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        win = _make()
        plot_widget = win._splitter_main.widget(1)
        assert plot_widget is not None
        canvases = plot_widget.findChildren(FigureCanvasQTAgg)
        assert len(canvases) == 1

    def test_column_three_has_k2000_on_top_and_log_below(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGroupBox, QSplitter, QTextEdit
        win = _make()
        third = win._splitter_main.widget(2)
        assert isinstance(third, QSplitter)
        assert third.orientation() == Qt.Orientation.Vertical
        assert third.count() == 2
        top, bottom = third.widget(0), third.widget(1)
        assert isinstance(top, QGroupBox)
        assert top.title() == "Multimeter (Keithley 2000)"
        # Bottom is now a small wrapper that holds a 'Log' heading
        # plus the actual log widget.
        assert win.txtLog in bottom.findChildren(QTextEdit)

    def test_third_column_handle_is_attribute(self, qapp):
        win = _make()
        assert getattr(win, "_splitter_third", None) is not None


class TestNoFunctionalRegression:
    def test_k2000_widgets_still_accessible(self, qapp):
        win = _make()
        for name in ("cmbK2000Transport", "stackK2000Transport",
                     "editK2000Visa", "editK2000Port", "cmbK2000Baud",
                     "btnK2000Connect", "ledK2000", "btnK2000Read",
                     "lblK2000Value"):
            assert hasattr(win, name), name

    def test_plot_log_and_controls_still_present(self, qapp):
        win = _make()
        for name in ("canvas", "ax", "txtLog",
                     "cmbVisa", "btnConnect", "btnStart", "btnStop"):
            assert hasattr(win, name), name

    def test_old_right_splitter_is_retired(self, qapp):
        win = _make()
        # _splitter_right was the old vertical plot+log splitter and
        # is gone after the three-column rebuild.
        assert getattr(win, "_splitter_right", None) is None

    def test_sim_connect_and_read_still_work(self, qapp):
        win = _make()
        win.chkK2000Sim.setChecked(True)
        win._toggle_k2000_connect()
        try:
            assert win.k2000 is not None
            win._read_k2000_voltage()
            txt = win.lblK2000Value.text()
            assert txt.endswith("V")
        finally:
            win._toggle_k2000_connect()
