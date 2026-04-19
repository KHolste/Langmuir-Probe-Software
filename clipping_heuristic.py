"""Conservative heuristic for suspected compliance clipping.

Modern measurements record a per-point compliance flag.  Legacy
CSVs (and re-imported third-party data) do not.  Without that flag
the analysis layer cannot tell whether the I-V curve hit the SMU's
current limit during acquisition — and silently fitting a
clipped plateau as if it were the true saturation current produces
a physically plausible but wrong ``T_e``.

This module provides a small heuristic that looks for the
characteristic signature of compliance clipping: a run of
essentially-constant current at the largest |V| of the sweep.  A
plateau near the sweep edges is suspicious; a flat stretch in the
middle is NOT, because a saturated I-V is supposed to plateau near
the saturation levels.  The heuristic's output is deliberately
conservative:

* it never returns flags for clean monotone data;
* its output is always labelled ``source="heuristic_suspected"`` so
  downstream renderers can mark the numbers as "suspected", not
  "confirmed compliance";
* precedence order is enforced at the call site —
  :func:`compute_double_analysis` only invokes this when no real
  compliance array is available.

No GUI dependency.  Pure-function, trivially testable.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: Relative tolerance for "same plateau" — deliberately very tight
#: (1 ppm of the per-sweep I-span) because a natural Double-probe
#: saturation branch is also flat and cannot be distinguished from
#: compliance by its shape alone.  The discriminating feature we
#: actually use is that a clamped current produces *nearly
#: bit-identical* consecutive samples, while even a smooth tanh
#: saturation still has tiny residual slope.  Real-hardware
#: measurements with ADC / amplifier noise will therefore NOT
#: trigger the heuristic on genuine saturation — but an operator
#: inspecting a legacy CSV always sees the output labelled
#: "suspected" rather than "confirmed", so a missed case is a soft
#: failure and a false positive is a visible warning, not a
#: scientific error.
DEFAULT_PLATEAU_TOLERANCE_FRACTION = 1e-6

#: Minimum number of consecutive same-current points required to
#: report suspected clipping.  Three is intentional: two consecutive
#: equal values happen by chance on any discretised sweep; three in a
#: row at the sweep edge is the cheapest signal that robustly
#: distinguishes "compliance-pinned" from "sampling luck".
DEFAULT_MIN_RUN_LENGTH = 3

#: Fraction of the |V| range either end is considered "edge".  Only
#: plateaus whose first point lives inside this edge window are
#: suspected — interior flat stretches are valid saturation behaviour
#: on a well-behaved Langmuir curve.
EDGE_WINDOW_FRACTION = 0.25


def detect_suspected_clipping(
    V,
    I,
    *,
    plateau_tolerance_fraction: float = DEFAULT_PLATEAU_TOLERANCE_FRACTION,
    min_run_len: int = DEFAULT_MIN_RUN_LENGTH,
    edge_window_fraction: float = EDGE_WINDOW_FRACTION,
) -> dict:
    """Return a summary dict describing suspected clipping.

    The return shape mirrors the ``compliance_info`` dict that
    :func:`dlp_double_analysis.compute_double_analysis` produces, so
    the rest of the pipeline can treat both signals identically
    *except* for the ``source`` field — which is
    ``"heuristic_suspected"`` here so GUI code can label the numbers
    honestly.

    Result keys
    -----------
    source : ``"heuristic_suspected"`` or ``"none"``
    n_total, n_flagged, clipped_fraction, action
        numeric summary (``action == "n/a"`` — no filtering is done
        by the heuristic itself; it only flags).
    flags : list[bool]
        per-point boolean array, same length as ``V``, True where a
        sample is suspected.

    Returns ``{"source": "none", "n_flagged": 0, "flags": [...]}``
    for clean data — callers can test ``n_flagged > 0`` uniformly.
    """
    V_arr = np.asarray(V, dtype=float)
    I_arr = np.asarray(I, dtype=float)
    n = int(V_arr.size)
    empty_result = {
        "source": "none",
        "n_total": n,
        "n_flagged": 0,
        "clipped_fraction": 0.0,
        "action": "n/a",
        "flags": [False] * n,
    }
    if n < max(min_run_len * 2, 6):
        return empty_result
    if V_arr.shape != I_arr.shape:
        return empty_result
    if not np.all(np.isfinite(V_arr)) or not np.all(np.isfinite(I_arr)):
        return empty_result

    i_span = float(np.ptp(I_arr))
    if i_span <= 0.0:
        return empty_result
    tolerance = plateau_tolerance_fraction * i_span
    v_range = float(np.ptp(V_arr))
    if v_range <= 0.0:
        return empty_result
    edge_width = edge_window_fraction * v_range
    v_min = float(V_arr.min())
    v_max = float(V_arr.max())
    low_edge_cut = v_min + edge_width
    high_edge_cut = v_max - edge_width

    flags = np.zeros(n, dtype=bool)

    # Walk each contiguous plateau (|I_{k+1}-I_k| <= tolerance).
    # Flag the entire run only if its *first* sample sits inside the
    # edge window AND the plateau's |I| is close to max|I|, which is
    # the signature of a clamped current limit rather than a
    # legitimate saturation branch of a balanced double probe.
    run_start = 0
    max_abs_i = float(np.max(np.abs(I_arr)))
    # "close to max" — within the same tolerance.  For balanced
    # Double probes the ion/electron saturation plateaus ARE near
    # max |I|; that's why we require the plateau to live at the
    # *sweep edge* (low_edge_cut / high_edge_cut) as well, which is
    # where clipping manifests.  A plateau in the interior of the
    # V-range is the normal saturation branch.
    for k in range(1, n):
        if abs(I_arr[k] - I_arr[k - 1]) > tolerance:
            _close_run(flags, run_start, k, V_arr, I_arr,
                        low_edge_cut, high_edge_cut, max_abs_i,
                        tolerance, min_run_len)
            run_start = k
    _close_run(flags, run_start, n, V_arr, I_arr,
                low_edge_cut, high_edge_cut, max_abs_i,
                tolerance, min_run_len)

    n_flagged = int(flags.sum())
    if n_flagged == 0:
        return empty_result
    return {
        "source": "heuristic_suspected",
        "n_total": n,
        "n_flagged": n_flagged,
        "clipped_fraction": float(n_flagged) / float(n),
        "action": "n/a",
        "flags": flags.tolist(),
    }


def _close_run(flags, start: int, end_exclusive: int,
                V, I, low_edge_cut: float, high_edge_cut: float,
                max_abs_i: float, tolerance: float,
                min_run_len: int) -> None:
    """Mark a plateau as suspected clipping if it satisfies every
    conservative criterion.  Mutates ``flags`` in place.  No return.
    """
    length = end_exclusive - start
    if length < min_run_len:
        return
    # Plateau must live at the sweep edge — low or high V tail.
    v_first = float(V[start])
    v_last = float(V[end_exclusive - 1])
    at_low_edge = v_first <= low_edge_cut or v_last <= low_edge_cut
    at_high_edge = (v_first >= high_edge_cut
                    or v_last >= high_edge_cut)
    if not (at_low_edge or at_high_edge):
        return
    # Plateau current magnitude must be close to the sweep |I|max —
    # that's where clipping lives in practice.  "Close" here means
    # within 10 % of max|I|; tolerance-based alone would flag a
    # lightly-noisy middle section on some datasets.
    abs_mean = float(np.mean(np.abs(I[start:end_exclusive])))
    if abs_mean < 0.90 * max_abs_i:
        return
    flags[start:end_exclusive] = True


__all__ = [
    "detect_suspected_clipping",
    "DEFAULT_PLATEAU_TOLERANCE_FRACTION",
    "DEFAULT_MIN_RUN_LENGTH",
    "EDGE_WINDOW_FRACTION",
]
