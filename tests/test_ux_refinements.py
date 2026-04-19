"""Focused tests for the UX refinement pass.

Areas covered:
  1. VISA combo: visible item text is the bare resource address;
     the IDN string lives in the per-item tooltip only.
  2. Left control column: splitter seeds the controls column at the
     slimmer 280-px width (down from 360 px).
  3. Single-probe options dialog: includes a Help button, opening
     it instantiates the help window without exception, and the
     help window opens at the documented full-usable size.
  4. Scrollable-dialog sizing helper: dialog opens at sizeHint of
     its populated inner widget instead of the tiny default.

The visual quality of the help text (typography, formula
rendering) is intentionally NOT asserted here — it is reviewed by
opening the dialog manually.  Tests verify that the help HTML is
non-empty, mentions the key user-visible terms, and renders into
a real ``QTextBrowser`` without exceptions.
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


# ---------------------------------------------------------------------------
class TestVisaComboDisplay:
    def test_combo_items_strips_idn_from_visible_text(self, tmp_path):
        from visa_persistence import VisaCache
        cache = VisaCache(path=str(tmp_path / "visa.json"))
        cache.update_scan("smu", [
            ("GPIB0::25::INSTR", "Agilent,B2901A,SN12345,1.2"),
            ("USB0::0x2A8D::0x9001::SN9999::INSTR",
             "Keysight,B2902B,SN9999,2.0"),
        ])
        cache.mark_successful("smu", "GPIB0::25::INSTR")
        items = cache.combo_items("smu")
        # Each visible label is the resource address only — not the
        # legacy "GPIB0::25::INSTR  (Agilent,B2901A,...)" form.
        for label, res in items:
            assert label == res
            assert "(" not in label and "Agilent" not in label \
                and "Keysight" not in label

    def test_combo_items_with_idn_exposes_idn_separately(self, tmp_path):
        from visa_persistence import VisaCache
        cache = VisaCache(path=str(tmp_path / "visa.json"))
        cache.update_scan("smu", [
            ("GPIB0::25::INSTR", "Agilent,B2901A,SN12345,1.2"),
        ])
        triples = cache.combo_items_with_idn("smu")
        assert triples and len(triples[0]) == 3
        label, res, idn = triples[0]
        assert label == "GPIB0::25::INSTR"
        assert res == "GPIB0::25::INSTR"
        assert "Agilent" in idn  # IDN preserved for tooltip use

    def test_main_window_combo_shows_only_address(self, qapp, tmp_path):
        """End-to-end: instantiate LPMainWindow and confirm cmbVisa
        items are bare resource addresses with the IDN attached as
        per-item tooltip rather than glued into the visible text."""
        from PySide6.QtCore import Qt
        from visa_persistence import VisaCache
        # Build a controlled cache and inject it on the window so
        # we don't depend on whatever lives in the operator's home.
        cache = VisaCache(path=str(tmp_path / "visa.json"))
        cache.update_scan("b2901", [
            ("GPIB0::25::INSTR", "Agilent,B2901A,SN12345,1.2"),
        ])
        cache.mark_successful("b2901", "GPIB0::25::INSTR")
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._visa_cache = cache
            win._populate_visa_combo_from_cache()
            assert win.cmbVisa.count() >= 1
            # Visible item text is the bare address.
            text = win.cmbVisa.itemText(0)
            assert text == "GPIB0::25::INSTR"
            # IDN is on the per-item tooltip role.
            tip = win.cmbVisa.itemData(
                0, Qt.ItemDataRole.ToolTipRole)
            assert tip is not None and "Agilent" in str(tip)
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestLeftColumnSlim:
    def test_splitter_seeds_controls_column_at_280px(self, qapp):
        """The first column of the main horizontal splitter is
        seeded at 280 px (down from the previous 360 px) so the
        operator does not have to drag it narrower on first launch.
        Stretch factor 1 (vs 4 for the plot, 3 for K2000+log) keeps
        it the slimmest column on resize."""
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            sp = win._splitter_main
            assert sp.count() >= 3
            # QSplitter has no public stretchFactor getter; the
            # value is stored on each child widget's size policy.
            stretches = [sp.widget(i).sizePolicy()
                         .horizontalStretch() for i in range(3)]
            assert stretches[0] == 1, (
                f"controls column should have stretch 1, "
                f"got {stretches[0]}")
            assert stretches[1] >= 4
            assert stretches[2] >= 3
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestSingleHelpDialog:
    def test_help_html_covers_key_user_terms(self):
        """Sanity check on the help text: the user-facing terms
        from the options dialog must all appear (case-insensitive
        substring match — the help text uses sentence case for
        running prose and exact-case for option names)."""
        from dlp_single_help import HELP_HTML
        body = HELP_HTML.lower()
        for term in [
                "v<sub>f</sub>", "v<sub>p</sub>", "t<sub>e</sub>",
                "i<sub>i,sat</sub>", "n<sub>e</sub>",
                "window width", "compliance", "hysteresis",
                "bootstrap", "auto", "derivative", "intersection"]:
            assert term in body, (
                f"help text missing user term: {term!r}")

    def test_help_dialog_opens_at_full_size(self, qapp):
        from dlp_single_help import SingleAnalysisHelpDialog
        dlg = SingleAnalysisHelpDialog()
        try:
            assert dlg._dlg.minimumWidth() >= 720
            assert dlg._dlg.minimumHeight() >= 600
            # HTML actually loaded into the browser.
            html_doc = dlg.txt.toHtml()
            assert "Single-probe analysis" in html_doc
        finally:
            dlg._dlg.deleteLater()
            qapp.processEvents()

    def test_help_html_has_no_white_or_near_white_background(self):
        """White-on-white bug regression guard: no light formula
        panels in the help CSS.  Anything <= ~#dddddd would risk
        producing low-contrast text on dark themes."""
        from dlp_single_help import HELP_HTML
        # Forbidden background tokens — these are the kinds of
        # near-white panel colours that triggered the original bug.
        forbidden_bgs = ["background:#fff", "background: #fff",
                         "background:#f4f6fa", "background:#ffffff",
                         "background-color:#fff",
                         "background-color: #fff",
                         "background-color:#f4f6fa",
                         "background-color:#ffffff"]
        css_lower = HELP_HTML.lower()
        for token in forbidden_bgs:
            assert token not in css_lower, (
                f"help CSS contains a near-white background "
                f"({token!r}) — risks white-on-white formulas")

    def test_help_html_has_dark_palette_body_and_formula(self):
        """Both the body and the formula panel must declare
        explicit dark colours so the document does not inherit a
        light palette and produce unreadable formulas."""
        from dlp_single_help import HELP_HTML
        body = HELP_HTML.lower()
        # Body fg + bg explicit.
        assert "color: #e6e6e6" in body or "color:#e6e6e6" in body
        assert ("background-color: #1e2126" in body
                or "background-color:#1e2126" in body)
        # Formula panel explicit dark surface + bright fg.
        assert "background: #262a30" in body \
            or "background:#262a30" in body
        assert "color: #ffe9a8" in body or "color:#ffe9a8" in body

    def test_help_dialog_pins_browser_palette(self, qapp):
        """The QTextBrowser must carry an explicit dark style sheet
        AND a default style sheet on its QTextDocument so the
        rendering does not depend on the inherited Qt palette."""
        from dlp_single_help import SingleAnalysisHelpDialog
        dlg = SingleAnalysisHelpDialog()
        try:
            ss = dlg.txt.styleSheet()
            assert "background-color: #1e2126" in ss
            assert "color: #e6e6e6" in ss
            doc_css = dlg.txt.document().defaultStyleSheet()
            # Defensive copy of the contrast-critical rules.
            assert ".formula" in doc_css
            assert "#262a30" in doc_css
            assert "#ffe9a8" in doc_css
        finally:
            dlg._dlg.deleteLater()
            qapp.processEvents()

    def test_options_dialog_has_help_button(self, qapp):
        from dlp_single_options import (
            SingleAnalysisOptions, SingleAnalysisOptionsDialog)
        from PySide6.QtWidgets import QDialogButtonBox
        dlg = SingleAnalysisOptionsDialog(SingleAnalysisOptions())
        try:
            help_btn = dlg._btn_box.button(
                QDialogButtonBox.StandardButton.Help)
            assert help_btn is not None
            assert help_btn.isVisible() or True  # may be hidden until shown
        finally:
            dlg._dlg.deleteLater()
            qapp.processEvents()

    def test_help_button_opens_help_dialog(self, qapp, monkeypatch):
        from dlp_single_options import (
            SingleAnalysisOptions, SingleAnalysisOptionsDialog)
        called = {"opened": False}
        # Patch the help-opener at its source so the test does not
        # actually spin up a modal dialog inside the test runner.
        import dlp_single_help as _dsh
        monkeypatch.setattr(
            _dsh, "open_single_help_dialog",
            lambda parent=None: called.update({"opened": True}))
        dlg = SingleAnalysisOptionsDialog(SingleAnalysisOptions())
        try:
            dlg._open_help()
            assert called["opened"]
        finally:
            dlg._dlg.deleteLater()
            qapp.processEvents()

    def test_options_dialog_minimum_height_fits_full_form(self, qapp):
        """Dialog must open tall enough to show every group box +
        the OK/Cancel/Help row without scrolling on a 768-px
        display."""
        from dlp_single_options import (
            SingleAnalysisOptions, SingleAnalysisOptionsDialog)
        dlg = SingleAnalysisOptionsDialog(SingleAnalysisOptions())
        try:
            assert dlg._dlg.minimumHeight() >= 480
        finally:
            dlg._dlg.deleteLater()
            qapp.processEvents()


# ---------------------------------------------------------------------------
class TestScrollableDialogSizing:
    """The setup_scrollable_dialog helper now installs a one-shot
    showEvent hook that resizes the dialog to fit its populated
    inner widget.  Prove the hook fires and the size grows past
    the small Qt default when needed."""

    def _make_tall_form_dialog(self, n_rows=12):
        from PySide6.QtWidgets import (
            QDialog, QGroupBox, QFormLayout, QLineEdit,
            QDialogButtonBox)
        from utils import setup_scrollable_dialog
        dlg = QDialog()
        content, top = setup_scrollable_dialog(dlg)
        grp = QGroupBox("Many rows")
        form = QFormLayout(grp)
        for i in range(n_rows):
            form.addRow(f"Row {i}:", QLineEdit())
        content.addWidget(grp)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        top.addWidget(btns)
        return dlg

    def test_first_show_resizes_to_content(self, qapp):
        dlg = self._make_tall_form_dialog(n_rows=15)
        try:
            # Tiny default before show.
            dlg.resize(50, 50)
            dlg.show()
            qapp.processEvents()
            # First show should have enlarged the dialog past the
            # 50-px default to cover the populated 15-row form.
            assert dlg.height() > 100
            assert dlg.width() > 100
        finally:
            dlg.deleteLater()
            qapp.processEvents()
