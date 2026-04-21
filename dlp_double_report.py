"""HTML report templates for the Double-probe analysis.

Extracted from :mod:`LPmeasurement` so presentation logic no longer
lives alongside the main window.  The public entry point is
:func:`format_compact_double`; :mod:`LPmeasurement` keeps
``_format_compact_double`` as a thin alias for existing tests and
any third-party callers that already imported the private name.
"""
from __future__ import annotations

import html as _html


def _is_nan(x) -> bool:
    """True when ``x`` is a NaN float — no numpy dependency."""
    try:
        return isinstance(x, float) and x != x
    except Exception:
        return False


def _fit_status_row(fit_dict) -> str:
    """Return the coloured Status row when the fit is non-OK, else ''.

    A non-OK status is surfaced as a prominent row with a coloured
    label so the operator cannot mistake a failed fit for a merely
    poor one.  OK fits keep the original, compact layout.
    """
    from dlp_fit_models import FitStatus, FAILURE_STATUSES
    fit_status = fit_dict.get("fit_status", FitStatus.OK)
    if fit_status == FitStatus.OK:
        return ""
    reason = (fit_dict.get("fit_error_reason")
              or fit_dict.get("fit_warning_reason"))
    if fit_status in FAILURE_STATUSES:
        banner_color = "#f06060"  # red — fit untrustworthy
        banner_label = "Fit failed"
    else:  # POOR / WARNING — converged, flag only
        banner_color = "#e0b050"
        banner_label = "Fit warning"
    cell = (f"<span style='color:{banner_color};"
            f" font-weight:600;'>{banner_label}: "
            f"{_html.escape(str(fit_status))}</span>")
    if reason:
        cell += (f"<br/><span style='color:#aab;"
                 f" font-size:11px;'>"
                 f"{_html.escape(str(reason))}</span>")
    return f"<tr><td><b>Status</b></td><td>{cell}</td></tr>"


def _te_row(fit_dict) -> str:
    te = fit_dict.get("Te_eV")
    te_err = fit_dict.get("Te_err_eV")
    if te is not None and not _is_nan(te) and te_err is not None \
            and not _is_nan(te_err):
        te_html = f"{te:.3f} &#177; {te_err:.3f} eV"
    elif te is not None and not _is_nan(te):
        te_html = f"{te:.3f} eV"
    else:
        te_html = "<span style='color:#aa6'>n/a</span>"
    # Attach the 95 % CI under the T_e line so the number is never
    # presented without uncertainty context.  "covariance" is the
    # always-on asymptotic form; "bootstrap" is the stronger opt-in.
    ci_lo = fit_dict.get("Te_ci95_lo_eV")
    ci_hi = fit_dict.get("Te_ci95_hi_eV")
    ci_method = fit_dict.get("Te_ci_method", "unavailable")
    if (ci_lo is not None and ci_hi is not None
            and not _is_nan(ci_lo) and not _is_nan(ci_hi)):
        label = ("bootstrap 95% CI"
                 if ci_method == "bootstrap"
                 else "95% CI (\u00b1z\u00b7\u03c3)")
        te_html += (f"<br/><span style='color:#889;font-size:10px;'>"
                    f"{label}: [{ci_lo:.3f}, {ci_hi:.3f}] eV</span>")
    elif ci_method == "unavailable" and te is not None and not _is_nan(te):
        te_html += ("<br/><span style='color:#988;font-size:10px;'>"
                    "95% CI: unavailable</span>")
    return f"<tr><td><b>T_e</b></td><td>{te_html}</td></tr>"


def _isat_row(fit_dict) -> str:
    isat = fit_dict.get("I_sat_fit_A")
    if isat is not None and not _is_nan(isat):
        isat_html = f"{isat * 1e3:.3f} mA"
    else:
        isat_html = "<span style='color:#aa6'>n/a</span>"
    # I_sat 95 % CI from the fit covariance — same honest conventions
    # as the T_e CI row above.
    isat_lo = fit_dict.get("I_sat_ci95_lo_A")
    isat_hi = fit_dict.get("I_sat_ci95_hi_A")
    isat_method = fit_dict.get("I_sat_ci_method", "unavailable")
    if (isat_lo is not None and isat_hi is not None
            and not _is_nan(isat_lo) and not _is_nan(isat_hi)):
        isat_html += (f"<br/><span style='color:#889;font-size:10px;'>"
                      f"95% CI: [{isat_lo * 1e3:.3f}, "
                      f"{isat_hi * 1e3:.3f}] mA</span>")
    elif isat_method == "unavailable" and isat is not None \
            and not _is_nan(isat):
        isat_html += ("<br/><span style='color:#988;font-size:10px;'>"
                      "95% CI: unavailable</span>")
    return f"<tr><td><b>I_sat</b></td><td>{isat_html}</td></tr>"


def _fit_summary_row(fit_dict) -> str:
    r2 = fit_dict.get("R2")
    nrmse = fit_dict.get("NRMSE")
    grade = fit_dict.get("grade", "?")
    grade_color = fit_dict.get("grade_color", "#888")
    fit_bits = []
    if r2 is not None and not _is_nan(r2):
        fit_bits.append(f"R&#178;={r2:.3f}")
    if nrmse is not None and not _is_nan(nrmse):
        fit_bits.append(f"NRMSE={nrmse:.1%}")
    fit_bits.append(f"<span style='color:{grade_color}'>[{grade}]</span>")
    return f"<tr><td><b>Fit</b></td><td>{', '.join(fit_bits)}</td></tr>"


def _ni_row(plasma_dict) -> str:
    if not plasma_dict:
        return ""
    n_i = plasma_dict.get("n_i_m3")
    gas = plasma_dict.get("ion_label", "")
    if n_i is None or _is_nan(n_i):
        return ""
    tag = (f"<span style='color:#888'>(Bohm"
           f"{', ' + gas if gas else ''})</span>")
    n_i_html = f"{n_i:.3e} m^-3 {tag}"
    # n_i 95 % CI — label honestly by scope.  The note is
    # "fit_only" / "fit+area" / "fit+mass" / "fit+area+mass"
    # depending on which uncertainty inputs the operator
    # supplied in the Double options dialog.  Probe area
    # and ion mass are treated as exact *only* when the
    # label literally says "fit_only".
    n_lo = plasma_dict.get("n_i_ci95_lo_m3")
    n_hi = plasma_dict.get("n_i_ci95_hi_m3")
    n_method = plasma_dict.get("n_i_ci_method", "unavailable")
    n_note = plasma_dict.get("n_i_ci_note", "fit_only")
    n_label = n_note.replace("_", "-")
    if (n_method != "unavailable"
            and n_lo is not None and n_hi is not None
            and not _is_nan(n_lo) and not _is_nan(n_hi)):
        n_i_html += (
            f"<br/><span style='color:#889;font-size:10px;'>"
            f"95% CI ({n_label}): "
            f"[{n_lo:.3e}, {n_hi:.3e}] m^-3</span>")
    elif n_method == "unavailable":
        n_i_html += ("<br/><span style='color:#988;"
                     "font-size:10px;'>n_i 95% CI: unavailable"
                     "</span>")
    return f"<tr><td><b>n_i</b></td><td>{n_i_html}</td></tr>"


def _compliance_row(compliance_info) -> str:
    """Compliance / clipping provenance — rendered when any point was
    flagged so the displayed T_e / I_sat carry their data-quality
    context on the same screen."""
    if not compliance_info:
        return ""
    if int(compliance_info.get("n_flagged", 0)) <= 0:
        return ""
    n_fl = int(compliance_info["n_flagged"])
    n_to = int(compliance_info.get("n_total", 0))
    frac = float(compliance_info.get("clipped_fraction", 0.0))
    action = compliance_info.get("action", "n/a")
    source = compliance_info.get("source", "operator_provided")
    suspected = source == "heuristic_suspected"
    label = "suspected clipping" if suspected else "clipped"
    if action == "excluded_from_fit":
        comp_color = "#bb8"
        comp_text = (f"{n_fl}/{n_to} {label} point(s) excluded "
                     f"from fit ({frac:.1%})")
    elif action == "retained_in_fit":
        comp_color = "#e0b050"
        comp_text = (f"{n_fl}/{n_to} {label} point(s) retained "
                     f"in fit ({frac:.1%}) — may bias T_e")
    else:
        comp_color = "#888"
        comp_text = f"{n_fl}/{n_to} {label} point(s) ({frac:.1%})"
    if suspected:
        comp_text += (" <span style='color:#888;font-size:10px;'>"
                      "(legacy heuristic)</span>")
    return (f"<tr><td><b>Compliance</b></td><td>"
            f"<span style='color:{comp_color}'>{comp_text}"
            f"</span></td></tr>")


def _model_comparison_block(fit_dict, comparison_list) -> str:
    """One-line-per-model footer, emitted only when more than one
    model was actually compared.  The active model gets a triangle
    marker so the reader can see which row produced the headline
    numbers."""
    if not comparison_list or len(comparison_list) <= 1:
        return ""
    active_key = fit_dict.get("model_key")
    cmp_rows = []
    for entry in comparison_list:
        marker = ("&#9654;" if entry.get("model_key") == active_key
                  else "&nbsp;&nbsp;")
        label = entry.get("label", entry.get("model_key", "?"))
        te_e = entry.get("Te_eV")
        r2_e = entry.get("R2")
        te_str = (f"{te_e:.2f} eV" if te_e is not None
                  and not _is_nan(te_e) else "&#8212;")
        r2_str = (f"R&#178;={r2_e:.3f}" if r2_e is not None
                  and not _is_nan(r2_e) else "")
        color = entry.get("grade_color", "#888")
        cmp_rows.append(
            f"<tr><td>{marker}</td><td>{label}</td>"
            f"<td>{te_str}</td>"
            f"<td><span style='color:{color}'>{r2_str}</span></td></tr>")
    return (f"<div style='font-family:Consolas, monospace; "
            f"font-size:10px; margin-top:6px; color:#aac;'>"
            f"<b>Models:</b><table>"
            f"{''.join(cmp_rows)}</table></div>")


def format_compact_double(fit_dict, plasma_dict, comparison_list,
                          *, compliance_info: dict | None = None) -> str:
    """Compact one-block summary of the Double-probe analysis.

    Replaces V2's ~25-line verbose block + 8-line model comparison
    with a single ~6-line table plus a one-line-per-model footer
    (only emitted if more than one model was actually compared).
    Preserves T_e, I_sat, model name, fit grade, and n_i/Bohm —
    the wider parameter-detail dump is intentionally dropped.

    When ``fit_dict`` carries a non-``OK`` ``fit_status`` (see
    :class:`dlp_fit_models.FitStatus`) the block is topped with a
    prominent "Status" row so the operator cannot confuse a failed
    fit with a merely weak one.  ``compliance_info`` — when present
    and flagged — renders an additional "Compliance" row below the
    numbers, carrying the provenance of any clipped-point handling.
    When a 95 % CI was computed, a second-line CI hint is appended
    under the T_e row so the number is never shown without its
    uncertainty context.
    """
    if fit_dict is None:
        return ""
    model = fit_dict.get("label", fit_dict.get("model_key", "?"))
    rows = [
        _fit_status_row(fit_dict),
        _te_row(fit_dict),
        _isat_row(fit_dict),
        f"<tr><td><b>Model</b></td><td>{model}</td></tr>",
        _fit_summary_row(fit_dict),
        _ni_row(plasma_dict),
        _compliance_row(compliance_info),
    ]
    cmp_html = _model_comparison_block(fit_dict, comparison_list)
    return (f"<div style='border:1px solid #58a; padding:8px; "
            f"margin:8px 0; background:#222;'>"
            f"<h3 style='color:#58a; margin:0 0 6px 0;'>"
            f"Double-Probe Analysis</h3>"
            f"<table style='font-family:Consolas, monospace;'>"
            f"{''.join(r for r in rows if r)}</table>"
            f"{cmp_html}</div>")
