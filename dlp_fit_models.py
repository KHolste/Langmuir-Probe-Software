"""
Fit-model definitions for Double-Langmuir-Probe I-V analysis.

Provides a small family of tanh-based models with increasing complexity,
a generic fitting function, and a selection dialog.

Fit-status reporting
--------------------
Every result dict returned by :func:`fit_dlp_model` carries three
explicit status fields so downstream UI / history can tell failure
modes apart from a merely weak fit:

* ``fit_status`` — one of :class:`FitStatus` (see docstring).
* ``fit_error_reason`` — human-readable one-liner when the fit did not
  produce trustworthy numbers (``None`` on success / poor-but-computed).
* ``fit_warning_reason`` — optional human-readable concern when the
  fit ran but a quality boundary was crossed (``None`` otherwise).

The scientific numbers themselves (Te_eV, R², NRMSE, ...) keep their
existing shape and meaning.  ``grade`` (excellent / good / fair / poor
/ n/a) stays orthogonal to ``fit_status`` so the UI can say
"the fit ran, but grade is poor" vs. "the fit procedure itself failed".
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox,
    QLabel, QGroupBox, QDialogButtonBox,
)


# ── fit-status constants ─────────────────────────────────────────────


class FitStatus:
    """Procedural outcome of a DLP model fit.

    Distinct from :func:`grade_fit_quality`, which grades how well a
    *converged* fit matches the data.  ``fit_status`` answers "did the
    fit procedure produce trustworthy numbers at all?" — the UI should
    never display plasma parameters from a non-``OK`` / non-``POOR``
    status without a prominent failure banner.
    """
    OK                = "ok"                  # converged + grade >= fair
    POOR              = "poor"                # converged + grade == "poor"
    WARNING           = "warning"             # converged + a flagged concern
    INSUFFICIENT_DATA = "insufficient_data"   # too few points / degenerate input
    BAD_INPUT         = "bad_input"           # non-finite samples, shape mismatch
    NON_CONVERGED     = "non_converged"       # curve_fit RuntimeError
    BOUNDS_ERROR      = "bounds_error"        # curve_fit ValueError (bounds/domain)
    NUMERICAL_ERROR   = "numerical_error"     # any other exception (captured)


#: Statuses that mean the fit did NOT produce trustworthy numbers.
#: UI helpers use this to pick warn/error styling vs. a simple "poor"
#: annotation.
FAILURE_STATUSES = frozenset({
    FitStatus.INSUFFICIENT_DATA,
    FitStatus.BAD_INPUT,
    FitStatus.NON_CONVERGED,
    FitStatus.BOUNDS_ERROR,
    FitStatus.NUMERICAL_ERROR,
})

# ── model functions (must be module-level for curve_fit) ─────────────


def _model_simple(v, i_sat, w):
    return i_sat * np.tanh(v / w)


def _model_slope(v, i_sat, w, g):
    return i_sat * np.tanh(v / w) + g * v


def _model_asym(v, i_sat, w, g, a):
    th = np.tanh(v / w)
    return i_sat * th * (1.0 + a * th) + g * v


# ── model registry ───────────────────────────────────────────────────

MODELS: dict[str, dict] = {
    "simple_tanh": {
        "label": "Simple tanh",
        "formula": "I = I_sat \u00b7 tanh(V/W)",
        "param_names": ["I_sat", "W"],
        "param_units": ["A", "V"],
        "func": _model_simple,
        "p0_func": lambda isat: [isat, 6.0],
        "bounds": ([0, 0.1], [1.0, 200.0]),
        "on_corrected": True,
    },
    "tanh_slope": {
        "label": "tanh + slope",
        "formula": "I = I_sat \u00b7 tanh(V/W) + g\u00b7V",
        "param_names": ["I_sat", "W", "g"],
        "param_units": ["A", "V", "A/V"],
        "func": _model_slope,
        "p0_func": lambda isat: [isat, 6.0, 5e-5],
        "bounds": ([0, 0.1, -1e-2], [1.0, 200.0, 1e-2]),
        "on_corrected": False,
    },
    "tanh_slope_asym": {
        "label": "tanh + slope + asymmetry",
        "formula": "I = I_sat \u00b7 tanh(V/W) \u00b7 (1+a\u00b7tanh) + g\u00b7V",
        "param_names": ["I_sat", "W", "g", "a"],
        "param_units": ["A", "V", "A/V", ""],
        "func": _model_asym,
        "p0_func": lambda isat: [isat, 6.0, 5e-5, 0.0],
        "bounds": ([0, 0.1, -1e-2, -0.5], [1.0, 200.0, 1e-2, 0.5]),
        "on_corrected": False,
    },
}

MODEL_KEYS = list(MODELS.keys())
DEFAULT_MODEL = "tanh_slope"


# ── generic fit function ─────────────────────────────────────────────


def fit_dlp_model(
    V: np.ndarray,
    I_raw: np.ndarray,
    model_key: str = DEFAULT_MODEL,
    sat_fit: dict | None = None,
    i_sat_guess: float = 2e-3,
) -> dict:
    """Fit a DLP model to I-V data and return results.

    Parameters
    ----------
    V, I_raw : voltage and raw current arrays
    model_key : key into MODELS registry
    sat_fit : saturation branch fit dict (for slope correction if needed)
    i_sat_guess : initial I_sat estimate

    Returns dict with: model_key, label, param_names, param_values,
    param_errors, Te_eV, Te_err_eV, R2, RMSE, fit_V, fit_I.
    """
    md = MODELS[model_key]
    func = md["func"]

    V = np.asarray(V, dtype=float)
    I_raw = np.asarray(I_raw, dtype=float)

    # choose input data
    if md["on_corrected"] and sat_fit:
        I_use = I_raw - sat_fit.get("slope_avg", 0) * V
    else:
        I_use = I_raw

    n_params = len(md["param_names"])

    # ── Input validation (runs BEFORE curve_fit so failures here
    #    surface as an explicit INSUFFICIENT_DATA / BAD_INPUT rather
    #    than as a generic "fit didn't converge").
    status, reason = _validate_fit_inputs(V, I_use, n_params)
    if status is not None:
        return _nan_result(model_key, md, status=status,
                            fit_error_reason=reason)

    p0 = md["p0_func"](i_sat_guess)

    try:
        popt, pcov = curve_fit(
            func, V, I_use, p0=p0,
            bounds=md["bounds"], maxfev=3000,
        )
    except RuntimeError as exc:
        # curve_fit raises RuntimeError when maxfev is reached or the
        # optimiser cannot improve from p0 — classic non-convergence.
        return _nan_result(model_key, md,
                            status=FitStatus.NON_CONVERGED,
                            fit_error_reason=
                            f"curve_fit did not converge: {exc}")
    except ValueError as exc:
        # Bad bounds, inf residuals, shape inconsistencies.
        return _nan_result(model_key, md,
                            status=FitStatus.BOUNDS_ERROR,
                            fit_error_reason=
                            f"curve_fit rejected input or bounds: {exc}")
    except Exception as exc:
        # Unexpected exception.  We do NOT silently swallow it into an
        # indistinguishable NaN result — the status + reason capture
        # the type and message so the operator can triage.
        return _nan_result(model_key, md,
                            status=FitStatus.NUMERICAL_ERROR,
                            fit_error_reason=
                            f"unexpected {type(exc).__name__}: {exc}")

    # parameter values and errors
    param_values = [float(p) for p in popt]
    if pcov is not None and np.all(np.isfinite(np.diag(pcov))):
        param_errors = [float(np.sqrt(pcov[i, i])) for i in range(len(popt))]
    else:
        param_errors = [float("nan")] * len(popt)

    # T_e always from W (index 1)
    w = param_values[1]
    te = w / 2.0
    te_err = param_errors[1] / 2.0

    # goodness
    I_fit = func(V, *popt)
    res = I_use - I_fit
    ss_res = float(np.sum(res**2))
    ss_tot = float(np.sum((I_use - np.mean(I_use))**2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(np.mean(res**2)))

    # plot curve
    v_plot = np.linspace(float(V.min()), float(V.max()), 200)
    i_plot = func(v_plot, *popt)

    # normalized RMSE (relative to I_sat)
    nrmse = rmse / abs(param_values[0]) if abs(param_values[0]) > 0 else float("nan")
    grade, grade_color = grade_fit_quality(r2, nrmse)
    fit_data = "corrected" if (md["on_corrected"] and sat_fit) else "raw"

    # ── Covariance-based T_e 95 % CI — cheap and always available
    #    when the fit returned a finite 1-sigma.  "unavailable" when
    #    Te_err is NaN, so downstream code never reports false
    #    confidence.  A bootstrap path (see bootstrap_te_ci_double)
    #    can later overwrite this with a sharper interval.
    if te_err is not None and np.isfinite(te_err):
        te_ci95_lo_eV = float(te - 1.96 * te_err)
        te_ci95_hi_eV = float(te + 1.96 * te_err)
        te_ci_method = "covariance"
    else:
        te_ci95_lo_eV = float("nan")
        te_ci95_hi_eV = float("nan")
        te_ci_method = "unavailable"

    # ── Covariance-based I_sat 95 % CI — parameter 0 is I_sat for
    #    every registered model.  param_errors[0] is its asymptotic
    #    1-sigma from pcov; the CI stays "unavailable" when that is
    #    NaN so downstream never reports false confidence.
    i_sat_err = param_errors[0]
    if i_sat_err is not None and np.isfinite(i_sat_err):
        i_sat_ci95_lo_A = float(param_values[0] - 1.96 * i_sat_err)
        i_sat_ci95_hi_A = float(param_values[0] + 1.96 * i_sat_err)
        i_sat_ci_method = "covariance"
    else:
        i_sat_ci95_lo_A = float("nan")
        i_sat_ci95_hi_A = float("nan")
        i_sat_ci_method = "unavailable"

    # ── Derive fit_status from converged output.  NaN goodness-of-fit
    #    (ss_tot==0 → zero-variance current, or zero I_sat) means the
    #    numbers came back but cannot be trusted; mark NUMERICAL_ERROR.
    #    Otherwise map grade to OK / POOR — the two converged outcomes
    #    the operator is allowed to act on, with or without a caveat.
    if np.isnan(r2) or np.isnan(nrmse):
        fit_status = FitStatus.NUMERICAL_ERROR
        fit_error_reason = (
            "converged but goodness-of-fit is non-finite "
            f"(R2={r2!r}, NRMSE={nrmse!r}) — data likely has zero "
            "variance or the fit returned I_sat≈0")
        fit_warning_reason = None
    elif grade == "poor":
        fit_status = FitStatus.POOR
        fit_error_reason = None
        fit_warning_reason = (
            f"fit graded 'poor' (R²={r2:.3f}, NRMSE={nrmse:.1%}) — "
            "check for compliance clipping, noise, or model mismatch")
    else:
        fit_status = FitStatus.OK
        fit_error_reason = None
        fit_warning_reason = None

    return {
        "model_key": model_key,
        "label": md["label"],
        "formula": md["formula"],
        "fit_data": fit_data,
        "param_names": md["param_names"],
        "param_units": md["param_units"],
        "param_values": param_values,
        "param_errors": param_errors,
        "Te_eV": te,
        "Te_err_eV": te_err,
        "I_sat_fit_A": param_values[0],
        "W_fit_V": w,
        "R2": r2,
        "RMSE": rmse,
        "NRMSE": nrmse,
        "grade": grade,
        "grade_color": grade_color,
        "fit_V": v_plot,
        "fit_I": i_plot,
        "fit_status": fit_status,
        "fit_error_reason": fit_error_reason,
        "fit_warning_reason": fit_warning_reason,
        "Te_ci95_lo_eV": te_ci95_lo_eV,
        "Te_ci95_hi_eV": te_ci95_hi_eV,
        "Te_ci_method": te_ci_method,
        "I_sat_ci95_lo_A": i_sat_ci95_lo_A,
        "I_sat_ci95_hi_A": i_sat_ci95_hi_A,
        "I_sat_ci_method": i_sat_ci_method,
    }


def _validate_fit_inputs(V: np.ndarray, I_use: np.ndarray,
                          n_params: int) -> tuple[str | None, str | None]:
    """Return ``(status, reason)`` for disqualifying inputs, else
    ``(None, None)`` when the data passes sanity checks.

    Separated out so the reason strings are easy to extend and the
    main fit function stays readable.  Degrees-of-freedom guard is
    ``len(V) >= max(n_params + 2, 4)`` — two extra points keep the
    residual variance defined, four is the minimum that yields a
    meaningful R² / NRMSE on the simplest two-parameter model.
    """
    if V.shape != I_use.shape:
        return (FitStatus.BAD_INPUT,
                f"shape mismatch: V={V.shape} vs I={I_use.shape}")
    n = int(V.size)
    min_pts = max(n_params + 2, 4)
    if n < min_pts:
        return (FitStatus.INSUFFICIENT_DATA,
                f"only {n} sample(s) for {n_params}-parameter fit "
                f"(need >= {min_pts})")
    if not np.all(np.isfinite(V)) or not np.all(np.isfinite(I_use)):
        return (FitStatus.BAD_INPUT,
                "non-finite values in V or I arrays")
    if float(np.ptp(V)) == 0.0:
        return (FitStatus.INSUFFICIENT_DATA,
                "voltage sweep has zero range (all V samples equal)")
    return (None, None)


def grade_fit_quality(r2: float, nrmse: float = 0.0) -> tuple[str, str]:
    """Return (grade, color) based on R² **and** NRMSE combined.

    Both criteria must be met for a given grade:

        excellent : R² ≥ 0.999  AND  NRMSE ≤ 1 %
        good      : R² ≥ 0.99   AND  NRMSE ≤ 5 %
        fair      : R² ≥ 0.95   AND  NRMSE ≤ 10 %
        poor      : anything else

    This prevents a high-R² fit with large systematic residuals
    from being falsely rated *excellent*.
    """
    if np.isnan(r2):
        return ("n/a", "#888888")
    if np.isnan(nrmse):
        nrmse = 1.0  # treat unknown NRMSE as worst case
    if r2 >= 0.999 and nrmse <= 0.01:
        return ("excellent", "#5ccf8a")
    if r2 >= 0.99 and nrmse <= 0.05:
        return ("good", "#8bc34a")
    if r2 >= 0.95 and nrmse <= 0.10:
        return ("fair", "#e0b050")
    return ("poor", "#f06060")


def compare_all_models(
    V: np.ndarray, I_raw: np.ndarray,
    sat_fit: dict | None = None,
    i_sat_guess: float = 2e-3,
) -> list[dict]:
    """Fit all registered models and return compact comparison list.

    Each model is fitted on its designated data basis (raw or corrected).
    Note: R²/NRMSE values across different data bases are not directly
    comparable — they are shown for transparency, not for cross-basis ranking.
    """
    results = []
    for key in MODEL_KEYS:
        r = fit_dlp_model(V, I_raw, key, sat_fit=sat_fit,
                           i_sat_guess=i_sat_guess)
        results.append({
            "model_key": key,
            "label": r["label"],
            "fit_data": r.get("fit_data", "raw"),
            "R2": r["R2"],
            "RMSE": r["RMSE"],
            "NRMSE": r.get("NRMSE", float("nan")),
            "Te_eV": r["Te_eV"],
            "grade": r.get("grade", "n/a"),
            # Carry the procedural status through the comparison so
            # the renderer can annotate a per-row failure instead of
            # just showing "n/a" / "poor" with no reason.
            "fit_status": r.get("fit_status", FitStatus.OK),
            "fit_error_reason": r.get("fit_error_reason"),
        })
    return results


def _nan_result(key, md, *,
                 status: str = FitStatus.NUMERICAL_ERROR,
                 fit_error_reason: str | None = None,
                 fit_warning_reason: str | None = None) -> dict:
    """Return the canonical empty result dict annotated with an
    explicit :class:`FitStatus`.

    ``status`` defaults to :attr:`FitStatus.NUMERICAL_ERROR` so any
    caller that forgets to pass one still produces a dict flagged as
    untrustworthy — never an implicit "ok" with NaNs inside.
    """
    n = len(md["param_names"])
    return {
        "model_key": key, "label": md["label"], "formula": md["formula"],
        "fit_data": "corrected" if md.get("on_corrected") else "raw",
        "param_names": md["param_names"], "param_units": md["param_units"],
        "param_values": [float("nan")] * n,
        "param_errors": [float("nan")] * n,
        "Te_eV": float("nan"), "Te_err_eV": float("nan"),
        "I_sat_fit_A": float("nan"), "W_fit_V": float("nan"),
        "R2": float("nan"), "RMSE": float("nan"),
        "NRMSE": float("nan"), "grade": "n/a", "grade_color": "#888888",
        "fit_V": np.array([]), "fit_I": np.array([]),
        "fit_status": status,
        "fit_error_reason": fit_error_reason,
        "fit_warning_reason": fit_warning_reason,
        # CI shape parity with the success path — "unavailable" so
        # readers never have to special-case the missing keys.
        "Te_ci95_lo_eV": float("nan"),
        "Te_ci95_hi_eV": float("nan"),
        "Te_ci_method": "unavailable",
        "I_sat_ci95_lo_A": float("nan"),
        "I_sat_ci95_hi_A": float("nan"),
        "I_sat_ci_method": "unavailable",
    }


# ── Double-probe T_e bootstrap CI ────────────────────────────────────
def bootstrap_te_ci_double(V, I_raw, model_key, *,
                            sat_fit=None, i_sat_guess: float = 2e-3,
                            n_iters: int = 200, seed: int = 0,
                            alpha: float = 0.05,
                            ) -> tuple[float | None, float | None, int]:
    """Residual-resampling 100·(1-alpha)% bootstrap CI for T_e from
    :func:`fit_dlp_model`.

    Parameters
    ----------
    V, I_raw : arrays passed through to the fit.
    model_key : key into :data:`MODELS`.
    sat_fit / i_sat_guess : forwarded unchanged.
    n_iters : number of bootstrap iterations (default 200 — about
        one second on a 60-point sweep).
    seed : deterministic RNG seed for reproducibility.
    alpha : 1 - confidence (default 0.05 → 95 % CI).

    Returns
    -------
    (lo, hi, n_successful) where ``lo``/``hi`` are in eV, or
    ``(None, None, n)`` when fewer than half of the replicas produced
    a finite T_e (the honest "unavailable" signal).  Never raises —
    a bootstrap that can't stand on its own feet must not be a
    source of runtime errors.
    """
    V = np.asarray(V, dtype=float)
    I_raw = np.asarray(I_raw, dtype=float)
    base = fit_dlp_model(V, I_raw, model_key,
                          sat_fit=sat_fit, i_sat_guess=i_sat_guess)
    if base.get("fit_status") != FitStatus.OK:
        # Bootstrap is meaningful only around a successful base fit.
        return None, None, 0
    te_base = base.get("Te_eV")
    if te_base is None or not np.isfinite(te_base):
        return None, None, 0
    func = MODELS[model_key]["func"]
    popt = base["param_values"]
    try:
        I_fit_base = func(V, *popt)
    except Exception:
        return None, None, 0
    # Residuals to resample.  For "on_corrected" models the base fit
    # was performed on corrected current, but the residuals still
    # compare raw-vs-model consistently via fit_dlp_model's own
    # correction path when we re-fit, so we resample raw-space
    # residuals.
    if MODELS[model_key].get("on_corrected") and sat_fit:
        I_use_base = I_raw - sat_fit.get("slope_avg", 0.0) * V
    else:
        I_use_base = I_raw
    residuals = I_use_base - I_fit_base
    if not np.all(np.isfinite(residuals)) or residuals.size == 0:
        return None, None, 0

    rng = np.random.default_rng(int(seed))
    estimates: list[float] = []
    for _ in range(int(n_iters)):
        resampled = rng.choice(residuals, size=residuals.size,
                                replace=True)
        if MODELS[model_key].get("on_corrected") and sat_fit:
            I_synth = I_fit_base + resampled \
                      + sat_fit.get("slope_avg", 0.0) * V
        else:
            I_synth = I_fit_base + resampled
        r = fit_dlp_model(V, I_synth, model_key,
                           sat_fit=sat_fit, i_sat_guess=i_sat_guess)
        if r.get("fit_status") == FitStatus.OK:
            te = r.get("Te_eV")
            if te is not None and np.isfinite(te):
                estimates.append(float(te))

    if len(estimates) < 0.5 * int(n_iters):
        return None, None, len(estimates)
    lo = float(np.percentile(estimates, 100.0 * alpha / 2.0))
    hi = float(np.percentile(estimates, 100.0 * (1.0 - alpha / 2.0)))
    return lo, hi, len(estimates)


# ── dialog ───────────────────────────────────────────────────────────


class FitModelDialog(QDialog):
    """Dialog for selecting the DLP fit model."""

    def __init__(self, current_key: str = DEFAULT_MODEL, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fit Model")
        self.setMinimumWidth(340)

        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)
        grp = QGroupBox("Model Selection")
        form = QFormLayout(grp)

        self.cmbModel = QComboBox()
        for key in MODEL_KEYS:
            self.cmbModel.addItem(MODELS[key]["label"], key)
        idx = MODEL_KEYS.index(current_key) if current_key in MODEL_KEYS else 0
        self.cmbModel.setCurrentIndex(idx)
        self.cmbModel.currentIndexChanged.connect(self._update_info)
        form.addRow("Model:", self.cmbModel)

        self.lblFormula = QLabel()
        self.lblFormula.setWordWrap(True)
        form.addRow("Formula:", self.lblFormula)
        self.lblParams = QLabel()
        form.addRow("Parameters:", self.lblParams)
        self.lblNote = QLabel()
        self.lblNote.setWordWrap(True)
        self.lblNote.setStyleSheet("color: #8890a0; font-size: 11px;")
        form.addRow("", self.lblNote)

        layout.addWidget(grp)
        self._update_info()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        _scroll_top.addWidget(btns)

    def _update_info(self):
        key = self.cmbModel.currentData()
        md = MODELS.get(key, {})
        self.lblFormula.setText(md.get("formula", ""))
        pnames = md.get("param_names", [])
        punits = md.get("param_units", [])
        self.lblParams.setText(
            ", ".join(f"{n} [{u}]" if u else n
                      for n, u in zip(pnames, punits)))
        if md.get("on_corrected"):
            self.lblNote.setText("Fits sheath-corrected data.")
        else:
            self.lblNote.setText("Fits raw data (includes slope in model).")

    def get_model_key(self) -> str:
        return self.cmbModel.currentData()
