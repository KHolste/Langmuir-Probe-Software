"""Pure-function Double-Langmuir-probe analysis pipeline.

Bundles V2's analysis math into a single explicit-input function so
callers (LP-Hauptfenster, tests, future re-analysis paths) can run
the analysis without touching GUI-owned mutable buffers.

The result dict mirrors the shape that V2 historically stored on
``self._last_*`` so downstream consumers (HTML formatters, history
file, CSV-meta export) keep working unchanged.

This module does **not** change scientific behavior.  It is a
clean re-orchestration around the existing V2 helpers
(:mod:`DoubleLangmuirAnalysis_v2`, :mod:`dlp_fit_models`,
:mod:`dlp_probe_dialog`, :mod:`dlp_experiment_dialog`).
"""
from __future__ import annotations

from typing import Optional

import numpy as np


#: Fraction of clipped points above which, even after exclusion, the
#: remaining data is treated as likely under-sampled in saturation —
#: the fit status is degraded to POOR with an explicit reason.
CLIPPING_DEGRADE_THRESHOLD = 0.25

#: Fraction above which *advisory* annotation is attached to the
#: warning reason even in exclude_clipped mode.
CLIPPING_ADVISORY_THRESHOLD = 0.05

#: In include-all mode the same fraction triggers degrade to POOR,
#: because clipped points physically cannot belong to the ion branch.
INCLUDE_ALL_DEGRADE_THRESHOLD = 0.10


def compute_double_analysis(V, I, *,
                              fit_model: str,
                              sat_fraction: float = 0.20,
                              probe_params: Optional[dict] = None,
                              gases: Optional[list] = None,
                              compliance: Optional[list] = None,
                              exclude_clipped: bool = True,
                              bootstrap_enabled: bool = False,
                              bootstrap_n_iters: int = 200,
                              bootstrap_seed: int = 0,
                              probe_area_rel_unc: float = 0.0,
                              ion_mass_rel_unc: float = 0.0,
                              ion_mix_rel_unc: float = 0.0,
                              ion_composition_mode: str = "molecular",
                              ion_x_atomic: float = 0.0,
                              ion_x_atomic_unc: float = 0.0,
                              ) -> dict:
    """Run V2's saturation-branch + model + comparison pipeline on
    explicit ``V`` / ``I`` arrays and return a result dict.

    Returns:
        ``{"fit": ..., "model_fit": ..., "plasma": ...,
           "comparison": ..., "ion_label": ..., "ok": bool,
           "warnings": list[str], "compliance_info": {...}}``

    Compliance / clipping handling
    ------------------------------
    When ``compliance`` (a bool list/array aligned with ``V``) is
    provided, the pipeline:

    * summarises it as ``compliance_info`` — ``source``, ``n_total``,
      ``n_flagged``, ``clipped_fraction``, ``action``;
    * if ``exclude_clipped`` is True, drops the flagged points before
      the saturation and model fits — matching the legacy LP
      behaviour, but now the decision lives in one place;
    * layers clipping-aware warnings / POOR-grade on the fit result
      via the existing ``fit_status`` / ``fit_warning_reason`` schema
      so a fit contaminated by clipping never looks "merely poor".

    Bootstrap CI
    ------------
    When ``bootstrap_enabled=True``, a residual-resampling 95 % CI
    for T_e is computed on top of the model fit.  The result is
    folded into ``model_fit`` as ``Te_ci95_lo_eV`` /
    ``Te_ci95_hi_eV`` and ``Te_ci_method == "bootstrap"``.  On
    failure the CI degrades to ``Te_ci_method == "unavailable"``
    (never a silent Gaussian fallback under a "bootstrap" label).
    """
    V = np.asarray(V, dtype=float)
    I = np.asarray(I, dtype=float)
    out: dict = {
        "fit": None, "model_fit": None, "plasma": None,
        "comparison": None, "ion_label": "",
        "ok": False, "warnings": [],
        "compliance_info": {"source": "none", "n_total": int(V.size),
                              "n_flagged": 0, "clipped_fraction": 0.0,
                              "action": "n/a"},
    }
    if len(V) < 10:
        out["warnings"].append("less than 10 data points -- cannot analyse")
        return out

    # ── Compliance summary + optional exclusion ──────────────────
    V_fit = V
    I_fit = I
    comp_info = out["compliance_info"]
    # Decide which source of truth to use.  Operator-provided
    # compliance always wins; the heuristic is a *fallback* for
    # legacy datasets that lack a compliance column.  This makes the
    # precedence obvious at one glance and keeps heuristic output
    # clearly labelled downstream (source="heuristic_suspected").
    have_real_compliance = False
    if compliance is not None:
        comp_arr = np.asarray(compliance, dtype=bool)
        if comp_arr.shape == V.shape:
            have_real_compliance = True
        else:
            out["warnings"].append(
                f"compliance length {comp_arr.shape} != V length "
                f"{V.shape} — clipping guard will use heuristic")

    if have_real_compliance:
        n_flagged = int(comp_arr.sum())
        frac = n_flagged / max(int(V.size), 1)
        comp_info.update({
            "source": "operator_provided",
            "n_flagged": n_flagged,
            "clipped_fraction": float(frac),
            "action": "n/a",
        })
        if n_flagged > 0 and exclude_clipped:
            keep = ~comp_arr
            V_fit = V[keep]
            I_fit = I[keep]
            comp_info["action"] = "excluded_from_fit"
        elif n_flagged > 0 and not exclude_clipped:
            comp_info["action"] = "retained_in_fit"
    else:
        # Legacy-safe fallback: try the plateau heuristic.  It is
        # conservative by design — detects flat runs at sweep edges
        # that look like a clamped current limit, never the interior
        # saturation branches of a well-behaved Double probe.  The
        # ``source`` tag "heuristic_suspected" cascades all the way
        # through sidecar + history + HTML so the operator sees a
        # "(suspected)" qualifier rather than "confirmed compliance".
        from clipping_heuristic import detect_suspected_clipping
        heur = detect_suspected_clipping(V, I)
        if int(heur.get("n_flagged", 0)) > 0:
            comp_info.update({
                "source": "heuristic_suspected",
                "n_flagged": int(heur["n_flagged"]),
                "clipped_fraction": float(heur["clipped_fraction"]),
                "action": "n/a",
            })
            if exclude_clipped:
                flags = np.asarray(heur["flags"], dtype=bool)
                keep = ~flags
                V_fit = V[keep]
                I_fit = I[keep]
                comp_info["action"] = "excluded_from_fit"
            else:
                comp_info["action"] = "retained_in_fit"

    # Re-check minimum after exclusion.
    if len(V_fit) < 10:
        out["warnings"].append(
            "less than 10 data points after compliance exclusion "
            "— cannot analyse")
        return out

    # Saturation-branch fit (V2's first stage).
    from DoubleLangmuirAnalysis_v2 import fit_saturation_branches
    try:
        fit = fit_saturation_branches(V_fit, I_fit,
                                        sat_fraction=sat_fraction)
    except Exception as exc:
        out["warnings"].append(f"saturation fit failed: {exc}")
        return out
    out["fit"] = fit

    # Model fit (T_e from tanh-family).
    from dlp_fit_models import (
        fit_dlp_model, compare_all_models, bootstrap_te_ci_double,
    )
    i_sat_guess = abs(fit.get("i_sat_pos", 2e-3))
    mfit = fit_dlp_model(V_fit, I_fit, fit_model, sat_fit=fit,
                          i_sat_guess=i_sat_guess)

    # ── Clipping-aware trust adjustment (layered on top of the
    #    fit-status taxonomy — never overrides a deeper failure).
    _apply_clipping_guard(mfit, comp_info, exclude_clipped)

    # ── Optional bootstrap 95 % CI.  Runs only when fit is OK and
    #    bootstrap was enabled.  Never invents confidence: a failed
    #    bootstrap reports "unavailable", not a fallback Gaussian.
    if bootstrap_enabled:
        from dlp_fit_models import FitStatus
        if mfit.get("fit_status") == FitStatus.OK:
            lo, hi, _n = bootstrap_te_ci_double(
                V_fit, I_fit, fit_model, sat_fit=fit,
                i_sat_guess=i_sat_guess,
                n_iters=int(bootstrap_n_iters),
                seed=int(bootstrap_seed),
            )
            if lo is not None and hi is not None:
                mfit["Te_ci95_lo_eV"] = lo
                mfit["Te_ci95_hi_eV"] = hi
                mfit["Te_ci_method"] = "bootstrap"
            else:
                # Preserve the covariance CI (already set by
                # fit_dlp_model) as the fallback, but mark the
                # method so the operator knows the bootstrap was
                # requested and could not be computed.
                mfit["Te_ci_method"] = "unavailable"
        else:
            mfit["Te_ci_method"] = "unavailable"

    out["model_fit"] = mfit

    # Density (Bohm) — same constants V2 uses.
    area_m2 = _resolve_area_m2(probe_params)
    # Resolve the effective ion mass under the operator-selected
    # composition mode ("molecular" / "atomic" / "unknown").  When
    # the caller leaves ion_mix_rel_unc at 0 AND supplies no gases,
    # falls back to the legacy helper so existing Ar-only tests
    # keep producing byte-identical numbers.
    mi, _mi_mix_rel = _resolve_mi_kg_with_unc(
        gases, ion_composition_mode,
        x_atomic=ion_x_atomic,
        x_atomic_unc=ion_x_atomic_unc)
    # If the caller has not explicitly provided ion_mix_rel_unc
    # (default 0) but the mass resolver returned one from
    # ``unknown`` mode, use that value.  An explicit non-zero
    # caller-supplied value wins — this lets callers such as tests
    # inject a custom rel-unc without going through the gas table.
    if ion_mix_rel_unc == 0.0 and _mi_mix_rel > 0.0:
        ion_mix_rel_unc = _mi_mix_rel
    pp = dict(mfit)
    if (mi and mi > 0 and area_m2 > 0
            and not np.isnan(mfit.get("Te_eV", float("nan")))):
        from DoubleLangmuirAnalysis_v2 import (_E_CHARGE, _K_BOLTZ,
                                                 _EV_TO_K)
        te_K = mfit["Te_eV"] * _EV_TO_K
        v_bohm = ((_K_BOLTZ * te_K) / mi) ** 0.5
        pp["v_Bohm_ms"] = float(v_bohm)
        n_i = float(
            mfit["I_sat_fit_A"] / (_E_CHARGE * area_m2 * v_bohm))
        pp["n_i_m3"] = n_i
        # ── n_i uncertainty budget.
        #    n_i = I_sat / (e · A · v_Bohm),  v_Bohm ∝ sqrt(T_e).
        #    Under independence of the contributing quantities:
        #        (σ_n / n)² = (σ_I / I)² + ¼ · (σ_T / T)²
        #                   + (σ_A / A)² + ¼ · (σ_m / m)²
        #    Fit terms come from the least-squares covariance.
        #    Area and ion-mass terms are optional operator inputs —
        #    when zero they collapse to the historical fit-only CI
        #    and the ``n_i_ci_note`` label remains "fit_only".  The
        #    label grows to "fit+area", "fit+mass", or
        #    "fit+area+mass" when the respective inputs are used,
        #    so the number is always truthfully labelled by its
        #    scope.
        _te = mfit.get("Te_eV")
        _te_err = mfit.get("Te_err_eV")
        _isat = mfit.get("I_sat_fit_A")
        _isat_err = mfit.get("param_errors", [None])[0] \
                     if mfit.get("param_errors") else None
        _rel_var = None
        if (_te and _te > 0 and _te_err is not None
                and np.isfinite(_te_err)
                and _isat and abs(_isat) > 0
                and _isat_err is not None and np.isfinite(_isat_err)):
            _rel_var = (float(_isat_err) / abs(float(_isat))) ** 2 \
                       + 0.25 * (float(_te_err) / float(_te)) ** 2
        # Add the optional user-supplied contributions (already
        # relative; clamp to [0, 1] so an erroneously-typed 500 %
        # does not dominate).  Track which components are included
        # in the scope note so the label stays honest.
        _area_rel = max(0.0, min(1.0, float(probe_area_rel_unc)))
        _mass_rel = max(0.0, min(1.0, float(ion_mass_rel_unc)))
        # Ion-composition ambiguity is a separate, physically
        # independent uncertainty source (molecular vs atomic
        # positive ion in dissociating gases like O₂).  It feeds
        # the SAME mass-term structure as the user-supplied σ_m
        # (both enter as ¼·(σ/m)² via v_Bohm ∝ √(T_e/m_i)) but we
        # track it under its own "ion_mix" scope tag so the
        # operator can tell the two sources apart in the n_i_ci_note
        # label and in the sidecar.
        _mix_rel = max(0.0, min(1.0, float(ion_mix_rel_unc)))
        _scope = ["fit"]
        if _rel_var is not None:
            if _area_rel > 0.0:
                _rel_var = _rel_var + _area_rel ** 2
                _scope.append("area")
            if _mass_rel > 0.0:
                _rel_var = _rel_var + 0.25 * _mass_rel ** 2
                _scope.append("mass")
            if _mix_rel > 0.0:
                _rel_var = _rel_var + 0.25 * _mix_rel ** 2
                _scope.append("ion_mix")
        # Publish the final note even when the CI itself degrades
        # so sidecar / history reflect the operator's inputs.
        pp["n_i_ci_note"] = (
            "fit_only" if _scope == ["fit"] else "+".join(_scope))
        pp["n_i_ci_area_rel_unc"] = float(_area_rel)
        pp["n_i_ci_mass_rel_unc"] = float(_mass_rel)
        pp["n_i_ci_ion_mix_rel_unc"] = float(_mix_rel)
        if _rel_var is not None and _rel_var >= 0.0:
            sigma_n = n_i * (float(_rel_var) ** 0.5)
            pp["n_i_ci95_lo_m3"] = float(n_i - 1.96 * sigma_n)
            pp["n_i_ci95_hi_m3"] = float(n_i + 1.96 * sigma_n)
            pp["n_i_ci_method"] = "covariance"
        else:
            pp["n_i_ci95_lo_m3"] = float("nan")
            pp["n_i_ci95_hi_m3"] = float("nan")
            pp["n_i_ci_method"] = "unavailable"
    else:
        pp["v_Bohm_ms"] = float("nan")
        pp["n_i_m3"] = float("nan")
        pp["n_i_ci95_lo_m3"] = float("nan")
        pp["n_i_ci95_hi_m3"] = float("nan")
        pp["n_i_ci_method"] = "unavailable"
        pp["n_i_ci_note"] = "fit_only"
        pp["n_i_ci_area_rel_unc"] = float(max(
            0.0, min(1.0, float(probe_area_rel_unc))))
        pp["n_i_ci_mass_rel_unc"] = float(max(
            0.0, min(1.0, float(ion_mass_rel_unc))))
        pp["n_i_ci_ion_mix_rel_unc"] = float(max(
            0.0, min(1.0, float(ion_mix_rel_unc))))
    # Carry the compliance summary into the plasma dict so the V2
    # HTML renderer — which already takes pp as its only data source
    # — can render the Compliance row without a back-channel.
    pp["compliance_info"] = dict(out["compliance_info"])
    out["plasma"] = pp

    # Model comparison — run against the same (possibly
    # compliance-filtered) arrays the primary fit saw, so per-row
    # numbers are directly comparable.
    try:
        cmp = compare_all_models(V_fit, I_fit, sat_fit=fit,
                                  i_sat_guess=i_sat_guess)
    except Exception as exc:  # pragma: no cover -- defensive
        cmp = []
        out["warnings"].append(f"model comparison failed: {exc}")
    out["comparison"] = cmp

    # Ion label for HTML — V2 uses the first configured gas.
    if gases:
        first = gases[0] if isinstance(gases[0], dict) else {}
        out["ion_label"] = first.get("gas", "")

    out["ok"] = True
    return out


def _resolve_area_m2(probe_params: Optional[dict]) -> float:
    """Match V2's area-resolution: explicit area_mm2 wins, else
    geometric area from probe geometry helpers; default fallback
    is 1 mm^2."""
    if not probe_params:
        return 1.0e-6
    if probe_params.get("electrode_area_mm2") is not None:
        try:
            return float(probe_params["electrode_area_mm2"]) * 1e-6
        except (TypeError, ValueError):
            pass
    try:
        from dlp_probe_dialog import compute_electrode_area
        mm2 = compute_electrode_area(
            probe_params.get("geometry", "cylindrical"),
            float(probe_params.get("electrode_length_mm", 5)),
            float(probe_params.get("electrode_radius_mm", 0.1)),
        )
        if mm2 > 0:
            return mm2 * 1e-6
    except Exception:
        pass
    return 1.0e-6


def _resolve_mi_kg(gases: Optional[list]) -> Optional[float]:
    """Legacy single-return resolver — used only by callers that do
    not care about ion-composition uncertainty (e.g. older tests).
    New code should prefer :func:`_resolve_mi_kg_with_unc`.
    """
    if not gases:
        return None
    try:
        from dlp_experiment_dialog import effective_ion_mass_kg
        return effective_ion_mass_kg(gases)
    except Exception:
        return None


def _resolve_mi_kg_with_unc(
    gases: Optional[list], mode: str,
    *,
    x_atomic: float = 0.0,
    x_atomic_unc: float = 0.0,
) -> tuple[Optional[float], float]:
    """Resolve ``(m_i_kg, ion_mix_rel_unc)`` under ``mode``.

    Wraps :func:`dlp_experiment_dialog.effective_ion_mass_kg_with_unc`
    so the analysis layer never imports Qt symbols — the helper
    module is Qt-free.  Returns ``(None, 0.0)`` when no gases are
    available or the helper fails (matches the legacy resolver's
    "quietly unavailable" contract).

    The ``x_atomic`` / ``x_atomic_unc`` kwargs are forwarded
    verbatim; they only have an effect when ``mode == "mixed"``.
    """
    if not gases:
        return None, 0.0
    try:
        from dlp_experiment_dialog import (
            effective_ion_mass_kg_with_unc,
        )
        return effective_ion_mass_kg_with_unc(
            gases, mode=mode,
            x_atomic=x_atomic, x_atomic_unc=x_atomic_unc)
    except Exception:
        return None, 0.0


def _apply_clipping_guard(mfit: dict, comp_info: dict,
                            exclude_clipped: bool) -> None:
    """Fold clipping severity into the existing fit-status schema.

    Rules (deliberately conservative so a clipping-contaminated fit
    never passes as "merely poor"):

    * Never overwrites a procedural failure from the fit layer —
      ``NON_CONVERGED`` / ``BOUNDS_ERROR`` / ``NUMERICAL_ERROR`` etc.
      remain as-is.
    * In ``exclude_clipped`` mode: at or above
      :data:`CLIPPING_DEGRADE_THRESHOLD` (25 %) the fit is degraded
      to :attr:`FitStatus.POOR` with a reason naming the fraction,
      because what's left may be under-sampled in the saturation
      region.  Between :data:`CLIPPING_ADVISORY_THRESHOLD` (5 %) and
      that level, an advisory is appended to ``fit_warning_reason``
      but the status is not downgraded.
    * In ``retained_in_fit`` mode (include_all): at or above
      :data:`INCLUDE_ALL_DEGRADE_THRESHOLD` (10 %) the fit is
      degraded to POOR; below that, an advisory is attached.

    Mutates ``mfit`` in place.  No return value.
    """
    from dlp_fit_models import FitStatus, FAILURE_STATUSES

    n_flagged = int(comp_info.get("n_flagged", 0))
    if n_flagged <= 0:
        return
    existing_status = mfit.get("fit_status", FitStatus.OK)
    if existing_status in FAILURE_STATUSES:
        return  # A deeper failure already dominates; don't mask it.

    frac = float(comp_info.get("clipped_fraction", 0.0))
    n_total = int(comp_info.get("n_total", 0))
    action = comp_info.get("action", "n/a")
    source = comp_info.get("source", "operator_provided")
    # Distinct language for the heuristic path so the operator never
    # reads "compliance" where we only mean "suspected clipping".
    label = ("suspected clipping"
             if source == "heuristic_suspected"
             else "compliance-flagged")

    def _append_warn(new_warn: str) -> None:
        prev = mfit.get("fit_warning_reason") or ""
        combined = (prev + "; " + new_warn).strip("; ")
        mfit["fit_warning_reason"] = combined

    if action == "excluded_from_fit":
        if frac >= CLIPPING_DEGRADE_THRESHOLD:
            mfit["fit_status"] = FitStatus.POOR
            _append_warn(
                f"{n_flagged} of {n_total} points excluded ({label}, "
                f"{frac:.1%}); remaining data may be under-sampled in "
                "saturation — T_e likely unreliable")
        elif frac >= CLIPPING_ADVISORY_THRESHOLD:
            _append_warn(
                f"{n_flagged} {label} point(s) excluded from fit "
                f"({frac:.1%})")
    elif action == "retained_in_fit":
        if frac >= INCLUDE_ALL_DEGRADE_THRESHOLD:
            mfit["fit_status"] = FitStatus.POOR
            _append_warn(
                f"{n_flagged} of {n_total} {label} points retained in "
                f"fit ({frac:.1%}); clipped plateau biases T_e and "
                "I_sat — switch to exclude_clipped or re-acquire with "
                "higher compliance")
        else:
            _append_warn(
                f"{n_flagged} {label} point(s) retained in fit "
                f"({frac:.1%})")
