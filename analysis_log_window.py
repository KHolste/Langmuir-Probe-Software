"""
Separate analysis-log window.

A small top-level Qt widget that shows *only* analysis results – kept
apart from the main acquisition log so atypical or noisy measurement
traffic does not drown out the analysis output.

Conventions
-----------
* New entries are inserted at the **top** of the view (newest first).
* On startup the window replays the persisted history
  (:mod:`analysis_history`) so the user sees previous analyses even
  before running a new one.
* The window is intended to be used as a **singleton** per main window –
  ``show_or_raise()`` creates it lazily and reuses it afterwards.
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from analysis_history import AnalysisRecord, load_records


class AnalysisLogWindow(QWidget):
    """Dedicated window that lists analysis entries newest-first."""

    def __init__(self, parent: QWidget | None = None,
                 *, history_path: str | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Analysis Log")
        self.resize(640, 480)
        self._history_path = history_path

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Analysis history</b> – newest on top"))
        header.addStretch(1)
        btn_reload = QPushButton("Reload file")
        btn_reload.setToolTip("Reload the persistent analysis history from disk.")
        btn_reload.clicked.connect(self.reload_history)
        header.addWidget(btn_reload)
        btn_clear = QPushButton("Clear view")
        btn_clear.setToolTip("Clear the view (does NOT delete the file).")
        btn_clear.clicked.connect(self.clear_view)
        header.addWidget(btn_clear)
        outer.addLayout(header)

        self.txt = QTextEdit()
        self.txt.setObjectName("txtAnalysisLog")
        self.txt.setReadOnly(True)
        self.txt.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self.txt, 1)

        self.reload_history()

    # ---- API ---------------------------------------------------------

    def prepend_html(self, html: str) -> None:
        """Insert an HTML block at the top of the view."""
        cursor = self.txt.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertHtml(html)
        cursor.insertBlock()
        # scroll to top so the new entry is visible
        self.txt.moveCursor(QTextCursor.MoveOperation.Start)

    def prepend_record(self, record: AnalysisRecord) -> None:
        """Insert a plain-text record at the top of the view."""
        header = (f'<div style="color:#7799cc;font-weight:600;">'
                  f'=== {record.timestamp} ===</div>')
        body_html = "<pre>{}</pre>".format(
            record.body.replace("<", "&lt;").replace(">", "&gt;"))
        self.prepend_html(header + body_html)

    def reload_history(self) -> None:
        """Rebuild the view from the persisted history file."""
        self.txt.clear()
        records = load_records(self._history_path)
        self.set_records(records)

    def set_records(self, records: Iterable[AnalysisRecord]) -> None:
        """Display *records* in the view.  Expects newest-first order."""
        self.txt.clear()
        items = list(records)
        # We append in forward order but the input is already newest-first
        # so the first item lands at the top.  Using append keeps the view
        # consistent with prepend_html semantics (top = newest).
        for rec in items:
            self._append_plain_record(rec)
        self.txt.moveCursor(QTextCursor.MoveOperation.Start)

    def clear_view(self) -> None:
        """Empty the view – does NOT touch the persistent file."""
        self.txt.clear()

    # ---- internal ----------------------------------------------------

    def _append_plain_record(self, record: AnalysisRecord) -> None:
        header = (f'<div style="color:#7799cc;font-weight:600;">'
                  f'=== {record.timestamp} ===</div>')
        body_html = "<pre>{}</pre>".format(
            record.body.replace("<", "&lt;").replace(">", "&gt;"))
        self.txt.append(header + body_html)


def show_or_raise(host, *, history_path: str | None = None) -> AnalysisLogWindow:
    """Singleton helper: reuse an existing window or create a new one.

    ``host`` is the main window – the attribute ``_analysis_window`` is
    used as a cache so repeated calls do not spawn multiple dialogs.
    """
    win: AnalysisLogWindow | None = getattr(host, "_analysis_window", None)
    if win is None:
        win = AnalysisLogWindow(host, history_path=history_path)
        host._analysis_window = win
    win.show()
    win.raise_()
    win.activateWindow()
    return win
