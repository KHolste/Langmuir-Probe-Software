"""Compactness contract for the DLP main window left column.

Guards against regressions that would silently widen the left column
(VISA combo, scan button, IDN line) and break the small-display UX.
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


class TestLeftColumnCompactness:
    def test_visa_combo_min_width_compact(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        assert win.cmbVisa.minimumWidth() <= 120

    def test_scan_button_capped(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        assert win.btnScan.maximumWidth() <= 80

    def test_splitter_left_clearly_narrower_than_right(self, qapp):
        """After show() the right (plot/log) column dominates and the
        left (controls) column is clearly the minor one.  Qt clamps
        the actual sizes to children's minimumSizeHint, so we assert
        the relation rather than an absolute pixel value."""
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        win.resize(1000, 620)
        win.show()
        try:
            sizes = win._splitter_main.sizes()
            assert sizes[0] < sizes[1], sizes
            # And the left column is at most ~50 % of total width —
            # otherwise plot/log on the right become unusable.
            assert sizes[0] <= sum(sizes) * 0.5, sizes
        finally:
            win.close()

    def test_idn_label_on_its_own_row(self, qapp):
        """The IDN label must NOT share a row with the LEDs / Connect
        button anymore — that was the main width driver before."""
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        # The IDN label's parent widget is the Instrument groupbox.
        # Its layout-sibling-row (the connect row) must NOT contain it.
        # We assert via Qt's parent layout introspection: the IDN
        # label and the Connect button must not be inside the same
        # QHBoxLayout.
        from PySide6.QtWidgets import QHBoxLayout
        # walk up the parent chain of lblIdn until we find a layout
        idn_parent_layout = None
        w = win.lblIdn.parent()
        if w is not None:
            idn_parent_layout = w.layout()
        connect_parent_layout = None
        w = win.btnConnect.parent()
        if w is not None:
            connect_parent_layout = w.layout()
        # both share the GroupBox's QVBoxLayout, but they are on
        # SEPARATE rows — so they sit in different sub-layouts.  The
        # connect button is inside a QHBoxLayout (row2); the IDN
        # label is added directly to the parent QVBoxLayout.
        # We assert: lblIdn is NOT a child of the same QHBoxLayout
        # that contains btnConnect.
        for i in range(connect_parent_layout.count()):
            item = connect_parent_layout.itemAt(i)
            sub = item.layout()
            if isinstance(sub, QHBoxLayout):
                widgets = [sub.itemAt(j).widget()
                           for j in range(sub.count())]
                if win.btnConnect in widgets:
                    assert win.lblIdn not in widgets


class TestNoFunctionalRegression:
    def test_main_window_signals_intact(self, qapp):
        """All the named widgets the rest of the app and the test
        suite rely on must still be present after the layout shrink."""
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        for name in ("cmbVisa", "btnScan", "btnConnect", "ledConn",
                     "ledCompl", "lblComplLed", "lblIdn", "chkSim",
                     "spnVstart", "spnVstop", "spnVstep", "spnSettle",
                     "spnAvg", "spnCompl", "chkBidir",
                     "btnStart", "btnStop", "progress", "lblStatus"):
            assert hasattr(win, name), name
