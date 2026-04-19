"""Project-wide axis-format contract:

* axis labels use ROUND parentheses for units, never square brackets
  (e.g. ``Te (eV)`` not ``Te [eV]``);
* on linear axes the cryptic offset / scientific-notation badge is
  suppressed so values are directly readable.
"""
from __future__ import annotations

import os
import pathlib
import re
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
# Helper-level checks
# ---------------------------------------------------------------------------
class TestApplyCleanAxisFormat:
    def test_disables_offset_and_sci_on_linear_axis(self):
        from matplotlib.figure import Figure
        from utils import apply_clean_axis_format
        fig = Figure(); ax = fig.add_subplot(111)
        # Force values that would normally trigger an offset.
        ax.plot([1.0e6, 1.000001e6, 1.000002e6], [0, 1, 2])
        apply_clean_axis_format(ax)
        fmt_x = ax.xaxis.get_major_formatter()
        # ScalarFormatter exposes get_useOffset(); after our call it
        # must return False on the linear axis.
        if hasattr(fmt_x, "get_useOffset"):
            assert fmt_x.get_useOffset() is False

    def test_skips_log_axis(self):
        from matplotlib.figure import Figure
        from utils import apply_clean_axis_format
        fig = Figure(); ax = fig.add_subplot(111)
        ax.set_yscale("log")
        ax.plot([1, 2, 3], [1e15, 1e16, 1e17])
        # Must not raise.
        apply_clean_axis_format(ax)
        # Y axis stays on log scale.
        assert ax.get_yscale() == "log"


# ---------------------------------------------------------------------------
# LP measurement window
# ---------------------------------------------------------------------------
class TestLPWindowAxisLabels:
    def _make(self, qapp):
        from dlp_lp_window import LPMeasurementWindow
        return LPMeasurementWindow(MagicMock(), MagicMock())

    def test_round_parens_only(self, qapp):
        win = self._make(qapp)
        labels = (
            win._ax_te.get_ylabel(),
            win._ax_ne.get_ylabel(),
            win._ax_ne.get_xlabel(),
        )
        for lbl in labels:
            assert "[" not in lbl, lbl
            assert "]" not in lbl, lbl
            # at least one plot label uses parens for units
        assert "(eV)" in win._ax_te.get_ylabel()
        assert "(m⁻³)" in win._ax_ne.get_ylabel()
        assert "(s)" in win._ax_ne.get_xlabel()


# ---------------------------------------------------------------------------
# V1 main window plot
# ---------------------------------------------------------------------------
class TestDLPMainPlotLabels:
    def test_round_parens_only(self, qapp):
        from DoubleLangmuir_measure import DLPMainWindow
        win = DLPMainWindow()
        for lbl in (win.ax.get_xlabel(), win.ax.get_ylabel()):
            assert "[" not in lbl
            assert "]" not in lbl
            assert lbl.endswith(")")


# ---------------------------------------------------------------------------
# Source-level guard: no [unit] in plot labels anywhere in the DLP path
# ---------------------------------------------------------------------------
class TestNoBracketUnitsInSource:
    DLP_FILES = (
        "DoubleLangmuir_measure.py",
        "DoubleLangmuir_measure_v2.py",
        "LPmeasurement.py",
        "dlp_lp_window.py",
        "dlp_triple_window.py",
        "DoubleLangmuirAnalysis_v2.py",
    )

    LABEL_RE = re.compile(
        r"set_(?:x|y)label\s*\(\s*(?P<q>['\"])(?P<txt>[^'\"]*)(?P=q)")

    def test_no_bracket_units_in_axis_labels(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for name in self.DLP_FILES:
            p = repo / name
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            for m in self.LABEL_RE.finditer(text):
                txt = m.group("txt")
                if "[" in txt or "]" in txt:
                    offenders.append(f"{name}: {txt!r}")
        assert not offenders, (
            "axis labels with square-bracket units found: "
            + "; ".join(offenders))
