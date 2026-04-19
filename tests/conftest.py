"""Repository-wide pytest hooks.

Iteration "GUI scroll-hardening" introduced QScrollArea-wrapped DLP
dialogs.  When many of them are created and torn down back-to-back in
the offscreen Qt platform (the test runner), the late GC of Python
wrappers around already-deleted Qt widgets can corrupt the Qt heap and
crash subsequent ``QWidget()`` allocations with an access violation.

The teardown hook below forces the Qt event loop to drain pending
deleteLater() calls and runs an explicit Python ``gc.collect()`` after
every test.  This keeps Python and Qt destruction in lock-step and
removed the crash for the full DLP test suite.
"""
from __future__ import annotations

import gc


def pytest_runtest_teardown(item, nextitem):
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
            app.sendPostedEvents(None, 0)  # 0 = DeferredDelete
    except Exception:
        pass
    gc.collect()
