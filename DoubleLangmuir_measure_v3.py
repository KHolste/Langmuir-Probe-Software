"""Compatibility shim.

The main Langmuir-Probe-Measurement window has moved to
:mod:`LPmeasurement` (entry point: ``python LPmeasurement.py``).
This module re-exports the new symbols under their old names so
existing test imports such as
``from DoubleLangmuir_measure_v3 import DLPMainWindowV3``
keep working unchanged.
"""
from __future__ import annotations

from LPmeasurement import LPMainWindow, main

# Legacy alias.  Prefer :class:`LPmeasurement.LPMainWindow` in new code.
DLPMainWindowV3 = LPMainWindow

__all__ = ["DLPMainWindowV3", "LPMainWindow", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
