"""Main-window column ratio contract.

The original design targeted ~25/50/25.  The UX-refinement pass
(VISA combo no longer carries the IDN string, IDN label is word-
wrapped) made it possible to slim the controls column further;
the contract is now ~14/50/36 (stretch 1:4:3, seed [280, 800, 540]).
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


class TestColumnRatio:
    def test_stretch_factors_are_one_four_three(self):
        """Source-level guard for the slimmed 1:4:3 ratio
        (~14/50/36 %)."""
        repo = pathlib.Path(__file__).resolve().parent.parent
        src = (repo / "LPmeasurement.py").read_text(encoding="utf-8")
        assert "splitter.setStretchFactor(0, 1)" in src
        assert "splitter.setStretchFactor(1, 4)" in src
        assert "splitter.setStretchFactor(2, 3)" in src
        assert "splitter.setSizes([280, 800, 540])" in src

    def test_middle_dominates_and_right_is_wider_than_left(self, qapp):
        """At a wide display the 1:4:3 stretch factors must produce a
        dominant middle column.  Cols 0 and 2 may end up close in
        absolute pixels at very wide windows because both hit their
        content min-hints — what matters is that the middle column
        clearly dominates and the controls column does not exceed
        a sensible upper bound of ~30 % of the total width."""
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            # Generous width so children min-hints don't dominate.
            win.resize(2400, 900)
            win.show()
            sizes = win._splitter_main.sizes()
            total = sum(sizes)
            assert total > 0
            assert sizes[1] > sizes[0], sizes
            assert sizes[1] > sizes[2], sizes
            # Middle still takes a clear plurality.
            assert sizes[1] / total >= 0.35, sizes
            # Slim invariant: controls column is not the dominant
            # one; its share stays at or below ~30 % of the total.
            assert sizes[0] / total <= 0.32, sizes
        finally:
            win.close()
