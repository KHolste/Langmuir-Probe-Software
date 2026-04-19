"""Pure single-Langmuir-probe IV analysis pipeline.

All functions take numpy arrays and return numbers / dicts.  No Qt,
no IO.  See :func:`analyze_single_iv` for the orchestrator.

Pipeline (each step records a status; downstream steps that depend
on a failed step are marked None and a warning is added):

  1. Floating potential V_f       -- zero crossing, linearly interpolated
  2. Initial T_e estimate         -- coarse semilog slope above V_f
  3. Ion saturation I_i,sat        -- linear fit on V < V_f - 3 * T_e
  4. Refined T_e                  -- semilog fit on (V_f, V_f + 3 * T_e]
                                     using I_e = I_total + I_i,sat
  5. Plasma potential V_p         -- intersection of retarding semilog
                                     line and electron-sat linear extrapolation
  6. Electron saturation I_e,sat   -- linear fit on V > V_p + 2 * T_e
  7. Electron density n_e          -- Bohm flux from I_i,sat (gas-aware)

V_p is intentionally treated as lower-confidence than V_f / T_e and
is reported with a confidence tag.

Sign convention used throughout: probe current I is negative on the
ion-saturation side (V << V_f), zero at V_f, positive in the
electron branch.  ``i_ion_sat_A`` in the result is the *magnitude*
of the negative plateau, i.e. always >= 0.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

# Physical constants -- SI.
E_CHARGE = 1.602176634e-19         # C
M_E_KG = 9.1093837015e-31          # kg
M_AR_KG = 6.6335209e-26            # kg, Argon ion (default fallback)


# ---------------------------------------------------------------------------
# Pre-processing helpers (compliance + hysteresis)
# ---------------------------------------------------------------------------
def _monotonize_iv(V, I) -> tuple[np.ndarray, np.ndarray, int]:
    """Return ``(V_sorted_unique, I_avg, n_merged)`` — a sorted,
    strictly-increasing voltage axis obtained by averaging the
    current at duplicate voltages.

    Motivation
    ----------
    Bidirectional sweeps concatenate forward and reverse legs into a
    single ``(V, I)`` pair, which means:

      * the voltage axis is NOT monotonic
        (``V_start … V_stop, V_stop-dv … V_start``);
      * every interior voltage appears twice.

    Derivative-based estimators (``estimate_v_plasma_derivative``,
    ``savgol_filter``) require a single-valued monotonic domain.
    The naïve fix — ``np.linspace(V[0], V[-1], …)`` on non-monotonic
    input — collapses to a near-zero grid spacing and makes
    ``savgol_filter(..., delta=dv)`` raise ``ZeroDivisionError``.

    Strategy (conservative, scientifically honest)
    ----------------------------------------------
      1. Sort by V.
      2. Group consecutive equal-V samples.  Two samples are
         considered "equal" when ``abs(ΔV) <= eps * max(1, |V_max|)``
         with ``eps = 1e-9`` — this tolerates floating-point
         quantisation without collapsing genuinely distinct
         neighbours.
      3. Within each group, average the currents.  If the forward
         and reverse traces coincide the average equals either
         trace; if they disagree (plasma drift), averaging is the
         honest statistical aggregate and the orchestrator flags
         the disagreement via :func:`detect_hysteresis`
         separately.

    Returns an ``n_merged`` count so callers can record how many
    duplicate pairs were collapsed — useful for operator-visible
    warnings.
    """
    V = np.asarray(V, dtype=float).copy()
    I = np.asarray(I, dtype=float).copy()
    if V.size == 0:
        return V, I, 0
    if V.shape != I.shape:
        return V, I, 0
    order = np.argsort(V, kind="mergesort")
    V = V[order]
    I = I[order]
    if V.size < 2:
        return V, I, 0
    scale = max(1.0, float(np.max(np.abs(V))))
    atol = 1e-9 * scale
    # Boundaries of equal-V groups: True where the next sample
    # opens a new group.
    step_is_new = np.concatenate(
        ([True], np.abs(np.diff(V)) > atol))
    group_ids = np.cumsum(step_is_new) - 1
    n_groups = int(group_ids[-1]) + 1
    if n_groups == V.size:
        return V, I, 0  # no duplicates, no averaging needed
    V_out = np.empty(n_groups, dtype=float)
    I_out = np.empty(n_groups, dtype=float)
    for g in range(n_groups):
        mask = group_ids == g
        V_out[g] = float(np.mean(V[mask]))
        I_out[g] = float(np.mean(I[mask]))
    n_merged = int(V.size - n_groups)
    return V_out, I_out, n_merged


def drop_compliance_points(V, I, compliance):
    """Return ``(V, I, n_dropped)`` with compliance-flagged points
    removed.  ``compliance`` may be ``None`` (no-op), a bool array, or
    a list of truthy values.  Mismatched length is treated as no-op
    so callers don't have to enforce strict length equality."""
    V = np.asarray(V, dtype=float)
    I = np.asarray(I, dtype=float)
    if compliance is None:
        return V, I, 0
    comp = np.asarray(compliance, dtype=bool)
    if comp.shape != V.shape:
        return V, I, 0
    keep = ~comp
    return V[keep], I[keep], int(comp.sum())


def _fit_branch_te_vf(V_branch, I_branch, *, robust=True
                       ) -> tuple:
    """Return ``(v_float, te_eV, r2, n_points, status)`` for a
    single-direction slice of an IV curve.

    Scope is narrow on purpose: we want **exactly** the two most
    operator-relevant branch numbers (V_f and T_e), computed via
    the same primitives the main pipeline uses, so operators can
    judge fwd/rev drift quantitatively.  Every output is a scalar
    or ``None`` — no plotting data, no sidecar noise.

    Inputs need not be sorted — the helper sorts internally so
    ``initial_te_estimate`` (which now also sorts, but defensively)
    and the semilog fit window are well-defined.  Any stage that
    cannot run returns ``(v_f, None, None, 0, reason)`` so the
    caller can label the branch as "unfittable" without crashing.
    """
    V = np.asarray(V_branch, dtype=float)
    I = np.asarray(I_branch, dtype=float)
    if V.size < 10 or V.shape != I.shape:
        return None, None, None, 0, "too few branch points"
    order = np.argsort(V, kind="mergesort")
    V = V[order]
    I = I[order]
    v_f, st_vf = find_v_float(V, I)
    if v_f is None:
        return None, None, None, 0, f"V_f: {st_vf}"
    te_seed = initial_te_estimate(V, I, v_f)
    if te_seed is None:
        return v_f, None, None, 0, "no T_e seed"
    i_ion, _, _, _ = fit_ion_saturation(V, I, v_f, te_seed)
    te, te_err, r2, nrmse, window, n_pts, st_te = fit_te_semilog(
        V, I, v_f, i_ion or 0.0, te_seed, robust=robust)
    if te is None:
        return v_f, None, None, int(n_pts or 0), f"T_e: {st_te}"
    return v_f, float(te), (float(r2) if r2 is not None else None), \
           int(n_pts or 0), "ok"


def detect_hysteresis(V, I, directions, *, threshold_pct=5.0):
    """Compare forward- and reverse-sweep branches in their common
    voltage range.  Returns a dict with keys:

      * ``flagged``         (bool) — branches differ above threshold
      * ``max_abs_diff_A``  (float | None)
      * ``rms_diff_A``      (float | None)
      * ``max_diff_pct``    (float | None) — relative to max |I|
      * ``threshold_pct``   (float)
      * ``reason``          (str)

    ``directions`` may be ``None``, a list of ``"fwd"``/``"rev"``
    strings, or any sequence of equivalent strings.  Length mismatch,
    mono-directional sweeps, and non-overlapping branches all return
    ``flagged=False`` with an explanatory ``reason`` and ``None``
    metrics — never raises.
    """
    V = np.asarray(V, dtype=float)
    I = np.asarray(I, dtype=float)
    out = {"flagged": False, "max_abs_diff_A": None,
           "rms_diff_A": None, "max_diff_pct": None,
           "threshold_pct": float(threshold_pct), "reason": ""}
    if directions is None or len(directions) != len(V):
        out["reason"] = "no direction info"
        return out
    dirs = np.array([str(d).lower().strip() for d in directions])
    fwd = dirs == "fwd"
    rev = dirs == "rev"
    if fwd.sum() < 5 or rev.sum() < 5:
        out["reason"] = "no bidirectional data"
        return out
    V_f, I_f = V[fwd], I[fwd]
    V_r, I_r = V[rev], I[rev]
    o_f = np.argsort(V_f); V_f, I_f = V_f[o_f], I_f[o_f]
    o_r = np.argsort(V_r); V_r, I_r = V_r[o_r], I_r[o_r]
    v_lo = max(float(V_f.min()), float(V_r.min()))
    v_hi = min(float(V_f.max()), float(V_r.max()))
    if v_hi - v_lo < 0.5:
        out["reason"] = "branches do not overlap"
        return out
    common = (V_f >= v_lo) & (V_f <= v_hi)
    if common.sum() < 5:
        out["reason"] = "too few common points"
        return out
    V_eval = V_f[common]
    I_f_eval = I_f[common]
    I_r_eval = np.interp(V_eval, V_r, I_r)
    diff = I_f_eval - I_r_eval
    max_abs = float(np.max(np.abs(diff)))
    rms = float(np.sqrt(np.mean(diff ** 2)))
    i_scale = float(np.max(np.abs(I))) or 1.0
    pct = max_abs / i_scale * 100.0
    out.update({"max_abs_diff_A": max_abs, "rms_diff_A": rms,
                "max_diff_pct": pct,
                "flagged": pct > threshold_pct,
                "reason": ("plasma drift between branches"
                           if pct > threshold_pct else "ok")})
    return out


# ---------------------------------------------------------------------------
# Stage 1 -- Floating potential
# ---------------------------------------------------------------------------
def find_v_float(V, I) -> tuple[Optional[float], str]:
    """Return the first zero crossing of the IV curve, interpolated
    linearly between the two bracketing samples.  ``status`` is "ok"
    on success or a short reason string on failure."""
    V = np.asarray(V, dtype=float)
    I = np.asarray(I, dtype=float)
    if len(V) < 4:
        return None, "too few points"
    sign = np.sign(I)
    for j in range(1, len(I)):
        if sign[j-1] != sign[j] and sign[j-1] != 0 and sign[j] != 0:
            v0, v1 = V[j-1], V[j]
            i0, i1 = I[j-1], I[j]
            if i1 == i0:
                return float(v0), "ok"
            return float(v0 - i0 * (v1 - v0) / (i1 - i0)), "ok"
    # Allow zero-touching as a transition (rare but possible).
    for j in range(1, len(I)):
        if sign[j-1] < 0 and I[j] >= 0 and (I[j] - I[j-1]) != 0:
            return float(V[j-1] + (V[j] - V[j-1])
                         * (-I[j-1]) / (I[j] - I[j-1])), "ok"
    return None, "no zero crossing in data"


# ---------------------------------------------------------------------------
# Stage 2 -- Coarse T_e estimate (for window seeding)
# ---------------------------------------------------------------------------
def initial_te_estimate(V, I, v_float) -> Optional[float]:
    """Coarse T_e from the semilog slope above V_f.  Used only to
    seed the ion-sat and refined T_e fit windows.

    Hardening (bidirectional-safe)
    ------------------------------
    The helper historically took the *first half* of the masked
    samples via ``v_above[:n]`` — a position slice.  That assumes
    the input V is sorted ascending.  On a bidirectional sweep the
    buffer is ``V_start..V_stop, V_stop-dv..V_start`` which is NOT
    sorted, and "first half" in array order spans the entire
    forward leg from V_f through saturation.  The electron branch
    flattens there, so the resulting slope is tiny and the returned
    T_e is wildly inflated (the operator-observed T_e ≈ 21 eV on
    3 eV synthetic data stemmed from this exact slip).

    We now sort by V before slicing, so the "lower half" is truly
    the retarding region `(V_f, V_f + ~half of the available span]`
    regardless of array order.  This is a no-op on monotonic data
    and restores the correct physics on bidirectional data.
    """
    V = np.asarray(V, dtype=float)
    I = np.asarray(I, dtype=float)
    mask = (V > v_float) & (I > 0)
    if mask.sum() < 4:
        return None
    v_above = V[mask]
    i_above = I[mask]
    order = np.argsort(v_above, kind="mergesort")
    v_sorted = v_above[order]
    i_sorted = i_above[order]
    n = max(4, int(0.5 * len(v_sorted)))
    log_i = np.log(i_sorted[:n])
    slope, _ = np.polyfit(v_sorted[:n], log_i, 1)
    if slope <= 0:
        return None
    te = 1.0 / float(slope)
    if not math.isfinite(te) or te <= 0:
        return None
    return te


# ---------------------------------------------------------------------------
# Stage 3 -- Ion saturation
# ---------------------------------------------------------------------------
def fit_ion_saturation(V, I, v_float, te_estimate):
    """Fit the negative-V plateau.  Returns the magnitude of the
    constant component (positive number)."""
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    if te_estimate is not None and te_estimate > 0:
        threshold = v_float - 3.0 * te_estimate
    else:
        threshold = float(np.percentile(V, 25))
    mask = V < threshold
    if mask.sum() < 3:
        threshold = float(np.percentile(V, 25))
        mask = V < threshold
    n_pts = int(mask.sum())
    if n_pts < 2:
        return None, None, "too few points in ion-sat region", n_pts
    slope, _ = np.polyfit(V[mask], I[mask], 1)
    i_const = float(np.mean(I[mask]))
    return abs(i_const), float(slope), "ok", n_pts


# ---------------------------------------------------------------------------
# Stage 4 -- Refined T_e (semilog fit on subtracted I_e)
# ---------------------------------------------------------------------------
def _semilog_linear_fit(v_fit, log_i, *, robust=True):
    """Return ``(slope, intercept)`` from a linear fit of ``log_i``
    against ``v_fit``.  When ``robust=True`` and SciPy is available,
    uses Huber-loss least squares (outlier-resistant); otherwise
    falls back to OLS via ``numpy.polyfit``."""
    if robust:
        try:
            from scipy.optimize import least_squares
        except Exception:
            slope, intercept = np.polyfit(v_fit, log_i, 1)
            return float(slope), float(intercept)
        # Seed from OLS for stability.
        slope0, intercept0 = np.polyfit(v_fit, log_i, 1)
        # Use the median-absolute-deviation of the OLS residuals as
        # the Huber transition scale so outliers above ~3*MAD are
        # downweighted but the bulk fit is OLS-equivalent.
        residuals = log_i - (slope0 * v_fit + intercept0)
        mad = float(np.median(np.abs(residuals - np.median(residuals))))
        f_scale = max(mad * 1.4826, 1e-3)  # MAD->sigma scaling
        try:
            res = least_squares(
                lambda p: p[0] * v_fit + p[1] - log_i,
                x0=[slope0, intercept0],
                loss="huber", f_scale=f_scale, max_nfev=200)
            return float(res.x[0]), float(res.x[1])
        except Exception:
            return float(slope0), float(intercept0)
    slope, intercept = np.polyfit(v_fit, log_i, 1)
    return float(slope), float(intercept)


def fit_te_semilog(V, I, v_float, i_ion_sat, te_seed, *, robust=True):
    """Refined T_e from semilog of I_e in (V_f, V_f + 3 * T_e_seed].

    ``robust=True`` (default) selects Huber-loss linear regression
    via SciPy when available — outlier-resistant against single
    clipped or noisy points in the retarding window — and falls
    back to OLS otherwise.  Pass ``robust=False`` to force OLS.

    Returns ``(te, te_err, R2, NRMSE, window, n_points, status)``.
    The standard error on T_e is propagated from the residuals via
    ``sigma_T_e = T_e^2 * sigma_slope``.
    """
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    if te_seed is None or te_seed <= 0:
        return None, None, None, None, (v_float, v_float), 0, "no T_e seed"
    v_high = v_float + 3.0 * te_seed
    mask = (V > v_float) & (V <= v_high)
    if mask.sum() < 4:
        v_high = v_float + 5.0 * te_seed
        mask = (V > v_float) & (V <= v_high)
        if mask.sum() < 4:
            return None, None, None, None, (v_float, v_high), int(mask.sum()), "too few retarding points"
    v_fit = V[mask]
    i_e = I[mask] + (i_ion_sat or 0.0)
    valid = i_e > 0
    if valid.sum() < 4:
        return None, None, None, None, (v_float, v_high), int(valid.sum()), "non-positive I_e in window"
    v_fit = v_fit[valid]
    i_e = i_e[valid]
    log_i = np.log(i_e)
    slope, intercept = _semilog_linear_fit(v_fit, log_i, robust=robust)
    if slope <= 0:
        return None, None, None, None, (v_float, v_high), len(v_fit), "non-positive semilog slope"
    te = float(1.0 / slope)
    pred = slope * v_fit + intercept
    ss_res = float(np.sum((log_i - pred) ** 2))
    ss_tot = float(np.sum((log_i - np.mean(log_i)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    n = len(v_fit)
    range_log = float(np.max(log_i) - np.min(log_i))
    nrmse = math.sqrt(ss_res / n) / range_log if range_log > 0 else 0.0
    var_x = float(np.var(v_fit))
    if n > 2 and var_x > 0:
        sigma_slope = math.sqrt(ss_res / (n - 2)) / math.sqrt(n * var_x)
        te_err = te * te * sigma_slope
    else:
        te_err = None
    return te, te_err, float(r2), float(nrmse), (float(v_float), float(v_high)), int(n), "ok"


def bootstrap_te_ci(V, I, v_float, i_ion_sat, te_seed,
                     *, n_iters=200, seed=0, alpha=0.05, robust=True):
    """Non-parametric bootstrap 100*(1-alpha)% confidence interval
    for T_e from the semilog fit.  Resamples the points inside the
    same fit window with replacement, refits, and returns the
    ``(lo, hi)`` percentile bounds.

    Returns ``(None, None)`` when the underlying fit fails for
    >= 75% of the bootstrap replicas.  Lightweight — ~50 ms for
    200 iterations on a 200-point sweep.  Provided as a pure-
    function hook for a future UI toggle; not invoked by the
    standard analyze_single_iv pipeline yet."""
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    if te_seed is None or te_seed <= 0 or v_float is None:
        return None, None
    v_high = v_float + 3.0 * te_seed
    mask = (V > v_float) & (V <= v_high)
    if mask.sum() < 6:
        return None, None
    v_fit = V[mask]; i_e_full = I[mask] + (i_ion_sat or 0.0)
    valid = i_e_full > 0
    v_fit = v_fit[valid]; i_e_full = i_e_full[valid]
    if len(v_fit) < 6:
        return None, None
    log_i_full = np.log(i_e_full)
    rng = np.random.default_rng(seed)
    estimates = []
    n = len(v_fit)
    for _ in range(int(n_iters)):
        idx = rng.integers(0, n, size=n)
        try:
            slope, _ = _semilog_linear_fit(
                v_fit[idx], log_i_full[idx], robust=robust)
            if slope > 0:
                estimates.append(1.0 / slope)
        except Exception:
            continue
    if len(estimates) < 0.25 * n_iters:
        return None, None
    lo = float(np.percentile(estimates, 100.0 * alpha / 2))
    hi = float(np.percentile(estimates, 100.0 * (1 - alpha / 2)))
    return lo, hi


# ---------------------------------------------------------------------------
# Stage 5a -- Plasma potential, derivative-based (preferred when clean)
# ---------------------------------------------------------------------------
def estimate_v_plasma_derivative(V, I, te, v_float):
    """Derivative-based V_p estimator.

    Theory: for a Maxwellian plasma the I-V characteristic has its
    inflection point at V = V_p — i.e. the smoothed dI/dV reaches its
    maximum there.  This is a more direct estimator than the
    log-linear / electron-sat intersection used by
    :func:`estimate_v_plasma`, but it is also more sensitive to noise
    and to non-uniform sweep spacing.

    Uses Savitzky-Golay smoothing (already a project dependency via
    :mod:`scipy.signal`) to compute a noise-tolerant first derivative
    in one pass.  Window and order are kept conservative so genuine
    knee structure is preserved.

    Returns ``(v_p, confidence, status, diag)`` where:
        ``v_p``        : float | None
        ``confidence`` : "high" | "medium" | "low" | "n/a"
        ``status``     : human-readable reason
        ``diag``       : dict with ``peak_didv``, ``peak_idx``,
                         ``baseline_didv``, ``prominence`` for
                         downstream inspection / tests.

    Confidence policy (intentionally conservative):
      * "high"   – peak well inside (V_f+0.5*Te, V_max) AND at least
                   3 samples from either window edge AND prominence
                   ratio (peak / median(|dI/dV|)) >= 3.
      * "medium" – peak inside the bracket but weak prominence
                   (>= 1.5x baseline) or close to the edge.
      * "low"    – peak found but at the boundary or barely above
                   baseline; caller should prefer the intersection
                   method as a sanity cross-check.
      * "n/a"    – not enough points / SG fit failed / no plausible
                   peak.  Returns ``None`` for v_p.
    """
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    diag = {"peak_didv": None, "peak_idx": None,
            "baseline_didv": None, "prominence": None,
            "bidirectional_merged": 0}
    if (te is None or te <= 0 or v_float is None
            or len(V) < 25):
        return None, "n/a", "missing T_e/V_f or fewer than 25 points", diag

    # ── Bidirectional / duplicate-voltage preprocessing ─────────
    # savgol_filter + np.interp require a strictly-monotonic,
    # single-valued voltage axis.  On bidirectional sweeps the raw
    # V buffer is V_start..V_stop,V_stop-dv..V_start which is
    # neither.  Merging duplicates by averaging I preserves the
    # science on coincident branches (true drift is flagged
    # elsewhere by detect_hysteresis) AND makes the derivative
    # path robust — without this, Vw[0] ≈ Vw[-1] → dv ≈ 0 →
    # ``savgol_filter(..., delta=dv)`` raised ZeroDivisionError.
    V, I, n_merged = _monotonize_iv(V, I)
    diag["bidirectional_merged"] = int(n_merged)
    if V.size < 25:
        return None, "n/a", "fewer than 25 points after merging "\
                             "duplicate voltages", diag

    # Restrict to the physically meaningful window.  We never look at
    # V < V_f + 0.5*Te (still in the retarding regime, derivative
    # rises monotonically here) and we want to keep the upper limit
    # at V_max so the electron-saturation roll-over is included.
    lo_v = v_float + 0.5 * te
    mask = V >= lo_v
    if mask.sum() < 15:
        return None, "n/a", "too few points above V_f + 0.5*Te", diag
    Vw = V[mask]; Iw = I[mask]

    # After monotonisation Vw is already ascending, so the linspace
    # and interp below both operate on a well-defined domain and dv
    # cannot collapse to zero on bidirectional input.
    n_grid = max(64, len(Vw))
    v_grid = np.linspace(Vw[0], Vw[-1], n_grid)
    i_grid = np.interp(v_grid, Vw, Iw)
    dv = float(v_grid[1] - v_grid[0])
    # Defensive second guard: even after monotonisation a pathological
    # input (all points at one voltage, <2 unique V in the window)
    # would leave dv at zero.  Bail out with an honest "n/a" rather
    # than tripping the ZeroDivisionError that motivated this helper.
    if not np.isfinite(dv) or dv <= 0.0:
        return None, "n/a", "degenerate voltage spacing in window", diag

    # Conservative SG window: ~10 % of the grid, at least 11, must be
    # odd, polyorder 3.  Keeps real knee structure intact while
    # rejecting per-sample noise.
    win = max(11, (n_grid // 10) | 1)
    if win % 2 == 0:
        win += 1
    win = min(win, n_grid - (1 - n_grid % 2))
    polyorder = 3
    if win <= polyorder:
        return None, "n/a", "SG window too small for polyorder", diag

    try:
        from scipy.signal import savgol_filter
        didv = savgol_filter(i_grid, win, polyorder, deriv=1, delta=dv)
    except Exception as exc:
        return None, "n/a", f"savgol_filter failed: {exc!r}", diag

    if not np.all(np.isfinite(didv)):
        return None, "n/a", "non-finite derivative", diag

    # Sanity gate: the smoothed current itself must carry a real
    # electron branch in the window.  A flat / pure-ion-saturation
    # profile has effectively zero range, in which case any
    # "peak" in didv is just floating-point noise and must not be
    # interpreted as a knee.  Threshold: 1 % of |I|_max.
    i_range = float(np.ptp(i_grid))
    i_scale = max(float(np.max(np.abs(i_grid))), 1e-15)
    if i_range < 0.01 * i_scale:
        return None, "n/a", "no electron branch (current ~ flat)", diag

    # Locate the dI/dV peak.  Look only at non-negative dI/dV — a
    # Maxwellian peak is positive; a negative-only profile means the
    # sweep does not cross V_p.
    pos_didv = np.where(didv > 0, didv, 0.0)
    if pos_didv.max() <= 0:
        return None, "n/a", "no positive dI/dV in window", diag
    peak_idx = int(np.argmax(pos_didv))
    v_p = float(v_grid[peak_idx])
    diag["peak_didv"] = float(pos_didv[peak_idx])
    diag["peak_idx"] = peak_idx
    median_didv = float(np.median(np.abs(didv[didv != 0])))
    diag["baseline_didv"] = median_didv
    if median_didv > 0:
        prom = diag["peak_didv"] / median_didv
    else:
        prom = float("inf")
    diag["prominence"] = prom

    # Edge-distance check: a peak at the very first or last sample is
    # almost always an artefact of the sweep boundary, not a knee.
    edge_dist = min(peak_idx, len(v_grid) - 1 - peak_idx)
    inside_strict_bracket = v_p > lo_v and v_p < float(v_grid[-1])

    if not inside_strict_bracket:
        return v_p, "low", "peak at boundary", diag
    if edge_dist < 3:
        return v_p, "low", "peak too close to sweep edge", diag
    if prom >= 3.0:
        return v_p, "high", "ok", diag
    if prom >= 1.5:
        return v_p, "medium", "weak prominence", diag
    return v_p, "low", "barely-prominent peak", diag


# ---------------------------------------------------------------------------
# Stage 5b -- Plasma potential, intersection-based (legacy fallback)
# ---------------------------------------------------------------------------
def estimate_v_plasma(V, I, te, v_float, i_ion_sat):
    """Intersection of the log-linear retarding line with the linear
    electron-sat extrapolation.  Returns ``(v_p, confidence, status)``;
    confidence is "medium" on a clean bracket, "low" on fallbacks."""
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    if te is None or te <= 0 or v_float is None:
        return None, "n/a", "missing T_e or V_f"
    v_lo_r = v_float + 0.5 * te
    v_hi_r = v_float + 3.0 * te
    mask_r = (V >= v_lo_r) & (V <= v_hi_r)
    if mask_r.sum() < 3:
        return None, "n/a", "too few retarding points"
    i_e = I[mask_r] + (i_ion_sat or 0.0)
    if (i_e <= 0).any():
        return None, "low", "non-positive I_e in retarding region"
    s_r, b_r = np.polyfit(V[mask_r], np.log(i_e), 1)
    v_sat_lo = v_float + 5.0 * te
    mask_s = V >= v_sat_lo
    if mask_s.sum() < 3:
        return None, "low", "no electron-saturation region in sweep"
    s_s, b_s = np.polyfit(V[mask_s], I[mask_s], 1)
    if s_s <= 0:
        i_sat_const = float(np.mean(I[mask_s]))
        if i_sat_const <= 0:
            return None, "low", "saturation mean non-positive"
        return float((math.log(i_sat_const) - b_r) / s_r), "medium", "ok (constant-saturation)"
    # Bisect on f(V) = exp(s_r V + b_r) - (s_s V + b_s).
    v_lo, v_hi = v_hi_r, v_sat_lo + 1.0
    f_lo = math.exp(s_r * v_lo + b_r) - (s_s * v_lo + b_s)
    f_hi = math.exp(s_r * v_hi + b_r) - (s_s * v_hi + b_s)
    if f_lo * f_hi > 0:
        i_e_mean = float(np.mean(I[mask_s]))
        if i_e_mean <= 0:
            return None, "low", "saturation mean non-positive"
        return float((math.log(i_e_mean) - b_r) / s_r), "low", "bracket failed; mean-sat fallback"
    for _ in range(80):
        v_mid = 0.5 * (v_lo + v_hi)
        f_mid = math.exp(s_r * v_mid + b_r) - (s_s * v_mid + b_s)
        if f_lo * f_mid < 0:
            v_hi = v_mid; f_hi = f_mid
        else:
            v_lo = v_mid; f_lo = f_mid
        if abs(v_hi - v_lo) < 1e-6:
            break
    return float(0.5 * (v_lo + v_hi)), "medium", "ok"


# ---------------------------------------------------------------------------
# Stage 6 -- Electron saturation
# ---------------------------------------------------------------------------
def fit_electron_saturation(V, I, v_plasma, te):
    """Electron sat fit on V > V_p + 2 * T_e (linear; reported value
    is the line evaluated at V_p, i.e. the extrapolated knee height)."""
    V = np.asarray(V, dtype=float); I = np.asarray(I, dtype=float)
    if v_plasma is None or te is None:
        threshold = float(np.percentile(V, 75))
        eval_v = float(np.max(V))
    else:
        threshold = v_plasma + 2.0 * te
        eval_v = v_plasma
    mask = V > threshold
    if mask.sum() < 3:
        return None, None, int(mask.sum()), "too few points in electron-sat region"
    slope, intercept = np.polyfit(V[mask], I[mask], 1)
    return float(slope * eval_v + intercept), float(slope), int(mask.sum()), "ok"


# ---------------------------------------------------------------------------
# Stage 7 -- Electron density (Bohm)
# ---------------------------------------------------------------------------
def compute_n_e(i_ion_sat, te, area_m2, m_i_kg):
    """Bohm-flux electron density: n_e = I_i,sat / (0.6 e A v_Bohm),
    with v_Bohm = sqrt(kT_e / m_i).  Returns None if any input is
    missing / non-positive."""
    if (i_ion_sat is None or i_ion_sat <= 0
            or te is None or te <= 0
            or area_m2 is None or area_m2 <= 0
            or m_i_kg is None or m_i_kg <= 0):
        return None
    te_J = te * E_CHARGE
    v_bohm = math.sqrt(te_J / m_i_kg)
    return float(i_ion_sat / (0.6 * E_CHARGE * area_m2 * v_bohm))


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------
VALID_VP_METHODS = ("auto", "derivative", "intersection")


def analyze_single_iv(V, I, area_m2=None, m_i_kg=None,
                       gas_label="Argon (Ar)",
                       *,
                       compliance=None, directions=None,
                       robust_te_fit=True,
                       te_window_factor=3.0,
                       hysteresis_threshold_pct=5.0,
                       bootstrap_enabled=False,
                       bootstrap_n_iters=200,
                       bootstrap_seed=0,
                       v_p_method="auto") -> dict:
    """Run the full single-probe pipeline.  Returns a result dict
    with all measured quantities, validity flags, and warnings.

    New optional kwargs:
      * ``compliance`` — per-point bool array; flagged points are
        dropped before any fit.  Mismatched length is silently
        ignored (no-op).
      * ``directions`` — per-point ``"fwd"``/``"rev"`` strings.
        When present and bidirectional, runs hysteresis detection
        and surfaces a warning if the branches diverge.
      * ``robust_te_fit`` — Huber-loss semilog T_e fit (default).
        Pass False to force OLS for backward compat / debugging.

    Does NOT raise on bad data -- failed stages are reported as
    None plus a warning string.
    """
    V_raw = np.asarray(V, dtype=float)
    I_raw = np.asarray(I, dtype=float)
    if m_i_kg is None or m_i_kg <= 0:
        m_i_kg = M_AR_KG
        m_i_fallback = True
    else:
        m_i_fallback = False

    # Hysteresis detection runs on the raw (pre-filter) data so that
    # genuine drift between full forward and reverse branches is
    # visible regardless of compliance hits.
    hyst = detect_hysteresis(V_raw, I_raw, directions,
                              threshold_pct=hysteresis_threshold_pct)

    # Compliance filter — silently drops clipped points so they
    # cannot poison the fits.  Count is reported in the result.
    V_arr, I_arr, n_dropped = drop_compliance_points(
        V_raw, I_raw, compliance)

    result: dict = {
        "ok": False, "warnings": [],
        "v_float_V": None, "v_plasma_V": None,
        "v_plasma_confidence": "n/a",
        "te_eV": None, "te_err_eV": None,
        "i_ion_sat_A": None, "i_electron_sat_A": None,
        "n_e_m3": None, "n_e_basis": "bohm-from-i_ion_sat",
        "fit_R2_te": None, "fit_NRMSE_te": None,
        "fit_window_te_V": None, "fit_n_points_te": 0,
        "gas_label": gas_label, "m_i_kg": float(m_i_kg),
        "m_i_is_fallback": bool(m_i_fallback),
        "area_m2": float(area_m2) if area_m2 else None,
        "n_compliance_dropped": int(n_dropped),
        "hysteresis": hyst,
        "te_fit_method": "huber" if robust_te_fit else "ols",
        # Bootstrap CI: populated only when bootstrap_enabled=True.
        # Schema:
        #   te_ci_eV : (lo, hi) tuple of floats, OR None when not run.
        #   te_ci_method:
        #     "disabled"      bootstrap toggle off (default)
        #     "bootstrap"     CI computed successfully
        #     "unavailable"   bootstrap requested but failed (too few
        #                     points, fit divergence, …) — UI should
        #                     show "n/a" rather than imply certainty.
        "te_ci_eV": None,
        "te_ci_method": "disabled",
        "te_ci_n_iters": int(bootstrap_n_iters) if bootstrap_enabled else 0,
        # V_p method/diagnostics: populated below.  ``v_p_method``
        # records which estimator's value ended up in v_plasma_V.
        # ``v_plasma_V_derivative`` / ``v_plasma_V_intersection`` keep
        # both candidates side-by-side so operators / tests can see
        # the agreement (or lack thereof).
        "v_p_method": "n/a",
        "v_p_method_requested": str(v_p_method),
        "v_plasma_V_derivative": None,
        "v_plasma_V_intersection": None,
        "v_p_derivative_confidence": "n/a",
        "v_p_intersection_confidence": "n/a",
        "v_p_methods_disagree_V": None,
        # ── Per-branch reporting (bidirectional only) ─────────────
        # Populated when the orchestrator detects a bidirectional
        # sweep and the fwd / rev branches each carry enough
        # points.  The merged values above remain the primary
        # reported result; these fields are an operator-visible
        # *diagnostic* so fwd/rev drift can be quantified beyond
        # the binary hysteresis flag.  ``None`` means branch fit
        # was not attempted (e.g. monodirectional input); a
        # non-None scalar with a status "ok" means the branch was
        # fit successfully.
        "te_eV_fwd": None, "te_eV_rev": None,
        "v_float_V_fwd": None, "v_float_V_rev": None,
        "branch_fit_R2_fwd": None, "branch_fit_R2_rev": None,
        "branch_n_points_fwd": 0, "branch_n_points_rev": 0,
        "branch_fit_status_fwd": None,
        "branch_fit_status_rev": None,
        # Relative Te delta between branches, as a percent of the
        # larger |Te|.  Operator-friendly scalar for a live readout.
        "branch_delta_pct_te": None,
        "branch_analysis_status": "skipped",
    }

    if n_dropped > 0:
        result["warnings"].append(
            f"dropped {n_dropped} compliance-flagged point(s) "
            "before fitting")

    # ── Bidirectional-sweep canonicalisation ─────────────────────
    # Root-cause fix: the entire Single pipeline (V_f, T_e seed,
    # ion-sat, semilog T_e, V_p, electron-sat, n_e) expects a
    # strictly-monotonic, single-valued I(V).  On a bidirectional
    # sweep the raw buffer is
    #     V_start..V_stop, V_stop-dv..V_start
    # which is NOT monotonic AND each interior V appears twice.
    # Without canonicalisation, position-sensitive helpers (notably
    # the semilog T_e seed) mistake the "first half" of the masked
    # array for the retarding region and collapse the slope,
    # producing the Te=21 eV / n=222 / R²=0.53 blow-up the operator
    # observed.
    #
    # We therefore reduce bidirectional data to the canonical
    # monotonic domain ONCE, at the orchestrator, and then run every
    # downstream stage on the merged arrays.  Hysteresis detection
    # stays on the raw branches (above) so drift between fwd/rev is
    # surfaced BEFORE averaging hides it.
    _bidir_detected = False
    _dirs = np.array([str(d).lower().strip() for d in directions]) \
             if directions is not None else None
    if _dirs is not None and "fwd" in _dirs and "rev" in _dirs:
        _bidir_detected = True
    elif V_arr.size >= 2 and not np.all(np.diff(V_arr) >= 0) \
            and not np.all(np.diff(V_arr) <= 0):
        _bidir_detected = True

    result["bidirectional_mode_used"] = bool(_bidir_detected)
    result["n_bidirectional_merged"] = 0
    if _bidir_detected:
        # ── Per-branch fits ─────────────────────────────────────
        # Do the fwd / rev fits BEFORE the merge so the per-branch
        # results reflect the original data, not the averaged
        # single-valued curve.  This is what gives the operator
        # quantitative visibility into fwd/rev drift beyond the
        # binary hysteresis flag.  We need ``directions`` to do
        # this honestly — a non-monotonic V detected without tags
        # could come from any resampled CSV, so skip the branch
        # split in that case and record a status that says so.
        if _dirs is not None and _dirs.size == V_arr.size:
            fwd_mask = _dirs == "fwd"
            rev_mask = _dirs == "rev"
            if fwd_mask.sum() >= 10 and rev_mask.sum() >= 10:
                vf_f, te_f, r2_f, n_f, st_f = _fit_branch_te_vf(
                    V_arr[fwd_mask], I_arr[fwd_mask],
                    robust=robust_te_fit)
                vf_r, te_r, r2_r, n_r, st_r = _fit_branch_te_vf(
                    V_arr[rev_mask], I_arr[rev_mask],
                    robust=robust_te_fit)
                result["te_eV_fwd"] = te_f
                result["te_eV_rev"] = te_r
                result["v_float_V_fwd"] = vf_f
                result["v_float_V_rev"] = vf_r
                result["branch_fit_R2_fwd"] = r2_f
                result["branch_fit_R2_rev"] = r2_r
                result["branch_n_points_fwd"] = int(n_f)
                result["branch_n_points_rev"] = int(n_r)
                result["branch_fit_status_fwd"] = st_f
                result["branch_fit_status_rev"] = st_r
                if (te_f is not None and te_r is not None
                        and te_f > 0 and te_r > 0):
                    denom = max(abs(te_f), abs(te_r))
                    result["branch_delta_pct_te"] = float(
                        100.0 * abs(te_f - te_r) / denom)
                    result["branch_analysis_status"] = "ok"
                elif te_f is not None or te_r is not None:
                    result["branch_analysis_status"] = \
                        "partial"
                else:
                    result["branch_analysis_status"] = \
                        "unfittable"
            else:
                result["branch_analysis_status"] = \
                    "insufficient_points_per_branch"
        else:
            result["branch_analysis_status"] = \
                "no_direction_tags"
        V_arr, I_arr, n_merged = _monotonize_iv(V_arr, I_arr)
        result["n_bidirectional_merged"] = int(n_merged)
        result["warnings"].append(
            f"bidirectional sweep canonicalised: {n_merged} "
            "coincident-voltage sample(s) averaged into a single "
            "monotonic I(V).  The whole Single pipeline (V_f, T_e, "
            "V_p, n_e) runs on the merged curve.  Forward/reverse "
            "drift — when present — is reported separately via the "
            "hysteresis warning.")
        # Operator-friendly summary of the per-branch diagnostic.
        _bd = result.get("branch_delta_pct_te")
        if _bd is not None:
            result["warnings"].append(
                f"per-branch T_e: "
                f"fwd = {result['te_eV_fwd']:.3f} eV, "
                f"rev = {result['te_eV_rev']:.3f} eV "
                f"(|\u0394|/max = {_bd:.1f}%)")
    if hyst.get("flagged"):
        pct = hyst.get("max_diff_pct") or 0.0
        result["warnings"].append(
            f"forward/reverse branches differ by {pct:.1f}% of |I|_max "
            f"(threshold {hyst['threshold_pct']:.0f}%) — possible "
            "plasma drift during sweep")

    # Bootstrap is structurally impossible until Te exists.  Pre-mark
    # the field as "unavailable" up front so any early return below
    # still yields an honest CI status when bootstrap was requested.
    if bootstrap_enabled:
        result["te_ci_method"] = "unavailable"

    if len(V_arr) < 10:
        result["warnings"].append("less than 10 data points -- cannot analyse")
        return result

    v_f, st = find_v_float(V_arr, I_arr)
    if v_f is None:
        result["warnings"].append(f"V_f undetermined: {st}")
        return result
    result["v_float_V"] = v_f

    te_init = initial_te_estimate(V_arr, I_arr, v_f)
    if te_init is None:
        result["warnings"].append("initial T_e estimate failed; ion-sat window heuristic only")

    i_ion, _, st, _ = fit_ion_saturation(V_arr, I_arr, v_f, te_init)
    if i_ion is None:
        result["warnings"].append(f"I_i,sat undetermined: {st}")
    else:
        result["i_ion_sat_A"] = i_ion

    seed = te_init if te_init is not None else 5.0
    # te_window_factor scales the (V_f, V_f + factor*Te_seed] window.
    # We reuse fit_te_semilog by stretching the seed accordingly:
    # the function uses 3.0 * te_seed internally, so a factor of f
    # corresponds to seeding with f/3 of the true Te_seed.
    seed_for_window = seed * (te_window_factor / 3.0)
    te, te_err, r2, nrmse, window, n_te, st = fit_te_semilog(
        V_arr, I_arr, v_f, i_ion, seed_for_window, robust=robust_te_fit)
    if te is None:
        result["warnings"].append(f"T_e fit failed: {st}")
    else:
        result.update({
            "te_eV": te, "te_err_eV": te_err,
            "fit_R2_te": r2, "fit_NRMSE_te": nrmse,
            "fit_window_te_V": window, "fit_n_points_te": n_te,
        })

    # Optional non-parametric T_e bootstrap CI.  Off by default so
    # disabled bootstrap = legacy behaviour bit-for-bit.  Honest
    # degradation: a None return from the helper is reported as
    # "unavailable" rather than silently dropped — surface the fact
    # that we tried but could not deliver a CI.
    if bootstrap_enabled and te is not None:
        try:
            lo, hi = bootstrap_te_ci(
                V_arr, I_arr, v_f, i_ion, seed_for_window,
                n_iters=int(bootstrap_n_iters),
                seed=int(bootstrap_seed),
                robust=robust_te_fit)
        except Exception as exc:
            lo, hi = None, None
            result["warnings"].append(
                f"bootstrap CI failed: {exc!r}")
        if lo is not None and hi is not None:
            result["te_ci_eV"] = (float(lo), float(hi))
            result["te_ci_method"] = "bootstrap"
        else:
            result["te_ci_method"] = "unavailable"
            result["warnings"].append(
                "T_e bootstrap CI unavailable "
                "(too few valid resamples)")
    elif bootstrap_enabled and te is None:
        # Te missing → bootstrap is structurally impossible.
        result["te_ci_method"] = "unavailable"

    # ----- V_p: dual-method computation + auto pick --------------
    # Validate operator request; an unknown value silently falls
    # back to "auto" rather than aborting the whole pipeline.
    method_req = (v_p_method
                  if v_p_method in VALID_VP_METHODS else "auto")
    if v_p_method not in VALID_VP_METHODS:
        result["warnings"].append(
            f"unknown v_p_method={v_p_method!r}; falling back to 'auto'")

    v_p_int, conf_int, st_int = estimate_v_plasma(
        V_arr, I_arr, te, v_f, i_ion)
    v_p_der, conf_der, st_der, _diag_der = estimate_v_plasma_derivative(
        V_arr, I_arr, te, v_f)

    result["v_plasma_V_intersection"] = v_p_int
    result["v_p_intersection_confidence"] = conf_int
    result["v_plasma_V_derivative"] = v_p_der
    result["v_p_derivative_confidence"] = conf_der

    # Auto policy: prefer derivative when it is "high"; otherwise
    # fall back to intersection; if intersection also fails, keep
    # whichever method produced a number.  Operator override wins.
    chosen_v_p = None
    chosen_conf = "n/a"
    chosen_method = "n/a"
    if method_req == "derivative":
        if v_p_der is not None:
            chosen_v_p, chosen_conf = v_p_der, conf_der
            chosen_method = "derivative"
        elif v_p_int is not None:
            chosen_v_p, chosen_conf = v_p_int, conf_int
            chosen_method = "intersection"
            result["warnings"].append(
                "V_p derivative method unavailable; "
                f"fell back to intersection ({st_der})")
    elif method_req == "intersection":
        if v_p_int is not None:
            chosen_v_p, chosen_conf = v_p_int, conf_int
            chosen_method = "intersection"
        elif v_p_der is not None:
            chosen_v_p, chosen_conf = v_p_der, conf_der
            chosen_method = "derivative"
            result["warnings"].append(
                "V_p intersection method unavailable; "
                f"fell back to derivative ({st_int})")
    else:  # auto
        if v_p_der is not None and conf_der == "high":
            chosen_v_p, chosen_conf = v_p_der, conf_der
            chosen_method = "derivative"
        elif v_p_int is not None:
            chosen_v_p, chosen_conf = v_p_int, conf_int
            chosen_method = "intersection"
        elif v_p_der is not None:
            chosen_v_p, chosen_conf = v_p_der, conf_der
            chosen_method = "derivative"

    if chosen_v_p is None:
        # Both methods failed.  Surface BOTH status strings so the
        # operator can diagnose which assumption broke.
        result["warnings"].append(
            f"V_p undetermined: derivative='{st_der}', "
            f"intersection='{st_int}'")
    else:
        result["v_plasma_V"] = chosen_v_p
        result["v_plasma_confidence"] = chosen_conf
        result["v_p_method"] = chosen_method

    # Cross-check agreement when both methods produced a value.
    # |Δ| > T_e signals a structurally suspicious sweep — probably
    # a soft knee or an oblique electron-sat slope; flag it but do
    # not change the chosen value.
    if v_p_int is not None and v_p_der is not None:
        delta = float(abs(v_p_int - v_p_der))
        result["v_p_methods_disagree_V"] = delta
        if te is not None and delta > te:
            result["warnings"].append(
                f"V_p methods disagree by {delta:.2f} V "
                f"(> T_e = {te:.2f} eV) — knee may be soft "
                "or electron-sat slope ill-defined")
    v_p = chosen_v_p

    i_e_sat, _, _, st = fit_electron_saturation(V_arr, I_arr, v_p, te)
    if i_e_sat is not None:
        result["i_electron_sat_A"] = i_e_sat
    else:
        result["warnings"].append(f"I_e,sat undetermined: {st}")

    if i_ion is not None and te is not None and result["area_m2"]:
        n_e = compute_n_e(i_ion, te, result["area_m2"], m_i_kg)
        if n_e is not None:
            result["n_e_m3"] = n_e

    # Sanity / quality flags.
    if i_ion is not None and i_e_sat is not None and i_ion > 0:
        ratio = i_e_sat / i_ion
        if ratio < 5:
            result["warnings"].append(
                f"I_e,sat / I_i,sat = {ratio:.1f} suspiciously low "
                f"(expect >> 10 for a Maxwellian plasma)")
    if te is not None:
        if te < 0.1:
            result["warnings"].append(f"T_e = {te:.3f} eV unphysically small")
        elif te > 100:
            result["warnings"].append(f"T_e = {te:.1f} eV unusually high")
    if r2 is not None and r2 < 0.9:
        result["warnings"].append(
            f"T_e fit R^2 = {r2:.3f} below 0.9 -- semilog window may be poor")
    if m_i_fallback:
        result["warnings"].append(
            "no gas mix configured -- using default Argon ion mass")

    result["ok"] = (result["v_float_V"] is not None
                    and result["te_eV"] is not None)
    return result


# ---------------------------------------------------------------------------
# Result presentation
# ---------------------------------------------------------------------------
def format_single_result_html(result: dict) -> str:
    """HTML block for the txtLog widget, styled in line with the
    Double-analysis output (dark background, monospace numerics)."""
    def fmt(val, unit, spec=".4g"):
        if val is None:
            return "<span style='color:#aa6'>n/a</span>"
        return f"{val:{spec}} {unit}"

    rows = []
    rows.append(f"<tr><td><b>V_f</b></td><td>"
                f"{fmt(result['v_float_V'], 'V', '+.3f')}</td></tr>")
    vp = result['v_plasma_V']
    vp_html = fmt(vp, 'V', '+.3f')
    if vp is not None:
        # Always show which estimator the reported V_p came from so
        # an operator can tell the new derivative knee from the
        # legacy intersection — colour only on reduced confidence.
        vp_method = result.get('v_p_method', 'n/a')
        vp_conf = result['v_plasma_confidence']
        if vp_conf in ('low', 'medium'):
            vp_html += (f"  <span style='color:#bb8'>"
                        f"({vp_method}, {vp_conf} confidence)</span>")
        else:
            vp_html += (f"  <span style='color:#888'>"
                        f"({vp_method})</span>")
    rows.append(f"<tr><td><b>V_p</b></td><td>{vp_html}</td></tr>")
    # Cross-method agreement row — only shown when *both* estimators
    # produced a number.  A small delta is reassuring; a large one is
    # a warning (already surfaced via result["warnings"], here we
    # just give the operator the comparison value at a glance).
    delta_vp = result.get('v_p_methods_disagree_V')
    v_int = result.get('v_plasma_V_intersection')
    v_der = result.get('v_plasma_V_derivative')
    if (delta_vp is not None and v_int is not None
            and v_der is not None):
        te_for_check = result.get('te_eV')
        flag = (te_for_check is not None
                and delta_vp > te_for_check)
        color = "#daa520" if flag else "#888"
        rows.append(
            f"<tr><td><b>V_p check</b></td><td>"
            f"<span style='color:{color}'>"
            f"derivative={v_der:+.3f} V vs intersection="
            f"{v_int:+.3f} V (Δ={delta_vp:.2f} V)"
            f"</span></td></tr>")
    te = result['te_eV']; te_err = result['te_err_eV']
    if te is None:
        te_html = "<span style='color:#aa6'>n/a</span>"
    elif te_err is not None:
        te_html = f"{te:.3f} &#177; {te_err:.3f} eV"
    else:
        te_html = f"{te:.3f} eV"
    rows.append(f"<tr><td><b>T_e</b></td><td>{te_html}</td></tr>")
    # 95% bootstrap CI row — only shown when bootstrap was actually
    # requested (avoids surfacing a "n/a" line on the default path).
    ci_method = result.get('te_ci_method', 'disabled')
    if ci_method != 'disabled':
        ci = result.get('te_ci_eV')
        n_it = result.get('te_ci_n_iters', 0)
        if ci_method == 'bootstrap' and ci is not None:
            lo, hi = ci
            ci_html = (f"[{lo:.3f}, {hi:.3f}] eV  "
                       f"<span style='color:#888'>"
                       f"(95% bootstrap, n={n_it})</span>")
        else:
            ci_html = ("<span style='color:#aa6'>n/a "
                       "(insufficient data for bootstrap)</span>")
        rows.append(f"<tr><td><b>T_e CI</b></td><td>{ci_html}</td></tr>")
    i_ion = result['i_ion_sat_A']; i_e_sat = result['i_electron_sat_A']
    rows.append(f"<tr><td><b>I_i,sat</b></td><td>"
                f"{fmt(i_ion*1e6 if i_ion else None, 'uA', '.3f')}</td></tr>")
    rows.append(f"<tr><td><b>I_e,sat</b></td><td>"
                f"{fmt(i_e_sat*1e3 if i_e_sat else None, 'mA', '.3f')}</td></tr>")
    n_e = result['n_e_m3']
    n_e_html = fmt(n_e, 'm^-3', '.3e')
    if n_e is not None:
        n_e_html += "  <span style='color:#888'>(Bohm)</span>"
    rows.append(f"<tr><td><b>n_e</b></td><td>{n_e_html}</td></tr>")
    if result['fit_R2_te'] is not None:
        q = (f"R^2={result['fit_R2_te']:.3f}, "
             f"NRMSE={result['fit_NRMSE_te']:.1%}, "
             f"n={result['fit_n_points_te']}")
        rows.append(f"<tr><td><b>T_e fit</b></td><td>{q}</td></tr>")
    gas = result['gas_label']
    if result['m_i_is_fallback']:
        gas += "  <span style='color:#bb8'>(default Argon mass)</span>"
    rows.append(f"<tr><td><b>Gas</b></td><td>{gas}</td></tr>")
    if result['area_m2']:
        rows.append(f"<tr><td><b>Probe area</b></td><td>"
                    f"{result['area_m2']*1e6:.4f} mm^2</td></tr>")
    n_drop = result.get('n_compliance_dropped', 0)
    if n_drop:
        rows.append(f"<tr><td><b>Compliance</b></td><td>"
                    f"<span style='color:#bb8'>{n_drop} clipped "
                    f"point(s) excluded</span></td></tr>")
    hyst = result.get('hysteresis') or {}
    if hyst.get('flagged'):
        pct = hyst.get('max_diff_pct') or 0.0
        rows.append(f"<tr><td><b>Hysteresis</b></td><td>"
                    f"<span style='color:#daa520'>fwd/rev "
                    f"diverge by {pct:.1f}% of |I|_max</span>"
                    f"</td></tr>")

    # Per-branch (bidirectional) diagnostics — always shown when
    # the orchestrator attempted a split.  The merged T_e above is
    # primary; this block is quantitative drift visibility.  When
    # only one branch could be fit we still surface what we have
    # with an honest label.
    _ba_status = result.get("branch_analysis_status", "skipped")
    if _ba_status not in ("skipped", "no_direction_tags"):
        te_f = result.get("te_eV_fwd")
        te_r = result.get("te_eV_rev")
        if te_f is not None and te_r is not None:
            d = result.get("branch_delta_pct_te") or 0.0
            # Yellow when branches diverge by more than 10 % of Te.
            color = "#daa520" if d >= 10.0 else "#888"
            rows.append(
                f"<tr><td><b>T_e fwd/rev</b></td><td>"
                f"<span style='color:{color}'>"
                f"{te_f:.3f} / {te_r:.3f} eV "
                f"(&Delta;/max = {d:.1f}%)</span></td></tr>")
        elif te_f is not None:
            rows.append(
                f"<tr><td><b>T_e fwd/rev</b></td><td>"
                f"<span style='color:#bb8'>"
                f"fwd = {te_f:.3f} eV, rev: unfittable</span>"
                f"</td></tr>")
        elif te_r is not None:
            rows.append(
                f"<tr><td><b>T_e fwd/rev</b></td><td>"
                f"<span style='color:#bb8'>"
                f"fwd: unfittable, rev = {te_r:.3f} eV</span>"
                f"</td></tr>")
        else:
            rows.append(
                f"<tr><td><b>T_e fwd/rev</b></td><td>"
                f"<span style='color:#bb8'>"
                f"both branches unfittable ({_ba_status})</span>"
                f"</td></tr>")

    warning_html = ""
    if result['warnings']:
        items = "".join(f"<li>{w}</li>" for w in result['warnings'])
        warning_html = (f"<div style='color:#daa520; margin-top:6px;'>"
                        f"<b>Warnings:</b><ul>{items}</ul></div>")
    color = "#5a8" if result['ok'] else "#a55"
    return (f"<div style='border:1px solid {color}; padding:8px; "
            f"margin:8px 0; background:#222;'>"
            f"<h3 style='color:{color}; margin:0 0 6px 0;'>"
            f"Single-Probe Analysis</h3>"
            f"<table style='font-family:Consolas, monospace;'>"
            f"{''.join(rows)}</table>{warning_html}</div>")
