"""Layout/scroll-hardening contract tests for the DLP dialogs.

Each dialog must be wrapped in a QScrollArea with both scrollbar
policies set to AsNeeded, and the QDialogButtonBox must live OUTSIDE
the scroll area so OK/Cancel stays reachable on small displays.
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


def _scroll_area_of(dialog):
    from PySide6.QtWidgets import QScrollArea
    return dialog.findChild(QScrollArea)


def _button_box_of(dialog):
    from PySide6.QtWidgets import QDialogButtonBox
    return dialog.findChild(QDialogButtonBox)


def _assert_scrolled(dialog):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialogButtonBox, QScrollArea
    scroll = _scroll_area_of(dialog)
    assert scroll is not None, "dialog must contain a QScrollArea"
    assert scroll.widgetResizable() is True
    assert scroll.horizontalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.verticalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    btn_box = _button_box_of(dialog)
    assert btn_box is not None, "dialog must have a QDialogButtonBox"
    # The button box must NOT be a descendant of the scroll area's
    # widget — otherwise OK/Cancel scroll out of view on small screens.
    inner = scroll.widget()
    assert inner is not None
    parent = btn_box
    while parent is not None:
        if parent is inner:
            raise AssertionError(
                "QDialogButtonBox is inside the QScrollArea — it must be "
                "pinned outside so OK/Cancel stays visible.")
        parent = parent.parent()


# ---------------------------------------------------------------------------
class TestInstrumentDialogScroll:
    def test_wrapped(self, qapp):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog()
        _assert_scrolled(dlg)


class TestInstrumentDialogTwoColumn:
    def test_two_column_band_exists(self, qapp):
        """The Instrument dialog distributes its groups across two
        columns to stay compact on small displays."""
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog()
        assert hasattr(dlg, "_col_left")
        assert hasattr(dlg, "_col_right")
        # Each column must hold at least one widget — otherwise the
        # split is cosmetic only.
        def _widget_count(col):
            return sum(1 for i in range(col.count())
                       if col.itemAt(i).widget() is not None)
        assert _widget_count(dlg._col_left) >= 2
        assert _widget_count(dlg._col_right) >= 2

    def test_known_groups_in_expected_columns(self, qapp):
        """Spot-check the column placement so a future refactor that
        accidentally collapses everything back into one column fails."""
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog()

        def _titles(col):
            out = []
            for i in range(col.count()):
                w = col.itemAt(i).widget()
                if w is not None and hasattr(w, "title"):
                    out.append(w.title())
            return out

        left = _titles(dlg._col_left)
        right = _titles(dlg._col_right)
        assert "Measurement" in left
        assert "Current Range" in left
        assert "Protection" in left
        assert "Advanced" in right
        # Timing & Filter — Qt collapses '&&' to '&' in the title.
        assert any("Timing" in t for t in right)


class TestProbeDialogScroll:
    def test_wrapped(self, qapp):
        from dlp_probe_dialog import ProbeParameterDialog
        dlg = ProbeParameterDialog()
        _assert_scrolled(dlg)


class TestSimDialogScroll:
    def test_wrapped(self, qapp):
        from dlp_sim_dialog import SimulationOptionsDialog
        dlg = SimulationOptionsDialog()
        _assert_scrolled(dlg)


class TestExperimentDialogScroll:
    def test_wrapped(self, qapp):
        from dlp_experiment_dialog import ExperimentParameterDialog
        dlg = ExperimentParameterDialog()
        _assert_scrolled(dlg)


class TestFitModelDialogScroll:
    def test_wrapped(self, qapp):
        from dlp_fit_models import FitModelDialog
        dlg = FitModelDialog()
        _assert_scrolled(dlg)


# ---------------------------------------------------------------------------
class TestHelperBehaviour:
    def test_only_one_scroll_area_per_dialog(self, qapp):
        """Construction-time setup must produce exactly one QScrollArea
        — guards against accidental double-wrapping in future refactors."""
        from PySide6.QtWidgets import QScrollArea
        from dlp_probe_dialog import ProbeParameterDialog
        dlg = ProbeParameterDialog()
        scrolls = dlg.findChildren(QScrollArea)
        assert len(scrolls) == 1

    def test_inner_widget_is_resizable(self, qapp):
        """Inner widget must grow with the scroll viewport so horizontal
        scrolling kicks in only when content actually exceeds width."""
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog()
        scroll = _scroll_area_of(dlg)
        assert scroll.widget() is not None
        assert scroll.widgetResizable() is True

    def test_dialog_max_height_capped(self, qapp):
        """maximumHeight should be set (not the Qt default huge value)."""
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog()
        # Qt's default max is 16777215; our cap should be much smaller.
        assert dlg.maximumHeight() < 16777215


# ---------------------------------------------------------------------------
class TestNoFunctionalRegression:
    """The scroll wrapping must not break the public dialog APIs that
    the rest of the app and the existing test suite rely on."""

    def test_instrument_get_options_still_works(self, qapp):
        from dlp_instrument_dialog import InstrumentOptionsDialog
        dlg = InstrumentOptionsDialog({"remote_sense": True, "beep": True})
        opts = dlg.get_options()
        assert opts["remote_sense"] is True
        assert opts["beep"] is True

    def test_probe_get_params_still_works(self, qapp):
        from dlp_probe_dialog import ProbeParameterDialog
        dlg = ProbeParameterDialog()
        # Just verify the round-trip getter exists and returns a dict.
        params = dlg.get_params()
        assert isinstance(params, dict)
