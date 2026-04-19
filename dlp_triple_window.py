"""Thin compatibility shim.

The Langmuir-Probe sub-window has moved to :mod:`dlp_lp_window`.
This module re-exports the new names under the old aliases so
``from dlp_triple_window import TripleProbeWindow`` and the
existing test patches keep working unchanged.
"""
from __future__ import annotations

from dlp_lp_window import LPMeasurementWindow, show_or_raise

# Legacy alias.  Prefer :class:`dlp_lp_window.LPMeasurementWindow`
# in new code.
TripleProbeWindow = LPMeasurementWindow

__all__ = ["TripleProbeWindow", "LPMeasurementWindow", "show_or_raise"]
