"""
Double-Langmuir-Probe Analysis v2 – first standalone analysis tool.

Reads CSV files produced by the v2 acquisition program, parses the
metadata header, computes basic I-V curve metrics, and shows a plot.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


# ── CSV parsing ───────────────────────────────────────────────────────


def parse_dlp_csv(path: str | Path) -> tuple[dict, dict]:
    """Parse a DLP v2 CSV file.

    Returns
    -------
    meta : dict
        Metadata from ``# key: value`` header lines.
    data : dict
        Column arrays.  Always contains ``V_soll``, ``V_ist``,
        ``I_mean``, ``I_std``.  May contain ``dir`` and ``compl``.
    """
    meta: dict[str, str] = {}
    col_header: str = ""
    data_lines: list[str] = []

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n\r")
            if line.startswith("# ") and ":" in line and "," not in line:
                key, _, val = line[2:].partition(":")
                meta[key.strip()] = val.strip()
            elif line.startswith("#") and "," in line:
                col_header = line.lstrip("# ").strip()
            elif line and not line.startswith("#"):
                data_lines.append(line)

    cols = [c.strip() for c in col_header.split(",")] if col_header else []
    has_ext = len(cols) >= 6

    data: dict[str, list] = {
        "V_soll": [], "V_ist": [], "I_mean": [], "I_std": [],
    }
    if has_ext:
        data["dir"] = []
        data["compl"] = []

    for row in data_lines:
        parts = row.split(",")
        if len(parts) < 4:
            continue
        data["V_soll"].append(float(parts[0]))
        data["V_ist"].append(float(parts[1]))
        data["I_mean"].append(float(parts[2]))
        data["I_std"].append(float(parts[3]))
        if has_ext and len(parts) >= 6:
            data["dir"].append(parts[4].strip())
            data["compl"].append(bool(int(parts[5])))

    # convert numeric lists to arrays
    for key in ("V_soll", "V_ist", "I_mean", "I_std"):
        data[key] = np.array(data[key])

    return meta, data


# ── metrics ───────────────────────────────────────────────────────────


def compute_metrics(
    V: np.ndarray,
    I: np.ndarray,
    sat_threshold: float = 0.6,
) -> dict:
    """Compute basic I-V curve descriptive metrics.

    Parameters
    ----------
    V, I : arrays of equal length
    sat_threshold : fraction of V-range used to define saturation regions

    Returns
    -------
    dict with keys: i_max, i_min, v_zero, slope_pos, slope_neg,
    asymmetry_ratio.
    """
    result: dict = {}
    result["i_max"] = float(np.max(I))
    result["i_min"] = float(np.min(I))

    # zero-crossing via linear interpolation
    result["v_zero"] = _find_zero_crossing(V, I)

    # saturation-branch slopes
    v_range = V.max() - V.min()
    v_cut = sat_threshold * v_range / 2.0
    v_mid = (V.max() + V.min()) / 2.0

    mask_pos = V >= (v_mid + v_cut)
    mask_neg = V <= (v_mid - v_cut)

    result["slope_pos"] = float(np.polyfit(V[mask_pos], I[mask_pos], 1)[0]) \
        if mask_pos.sum() >= 2 else float("nan")
    result["slope_neg"] = float(np.polyfit(V[mask_neg], I[mask_neg], 1)[0]) \
        if mask_neg.sum() >= 2 else float("nan")

    # asymmetry ratio: |I_max| / |I_min|  (1.0 = symmetric)
    if abs(result["i_min"]) > 0:
        result["asymmetry_ratio"] = abs(result["i_max"]) / abs(result["i_min"])
    else:
        result["asymmetry_ratio"] = float("inf")

    return result


def _find_zero_crossing(V: np.ndarray, I: np.ndarray) -> float:
    """Estimate V where I crosses zero via linear interpolation."""
    for j in range(len(I) - 1):
        if I[j] * I[j + 1] <= 0 and I[j] != I[j + 1]:
            return float(V[j] - I[j] * (V[j + 1] - V[j]) / (I[j + 1] - I[j]))
    return float("nan")


# ── saturation-branch analysis ────────────────────────────────────────


def fit_saturation_branches(
    V: np.ndarray,
    I: np.ndarray,
    *,
    v_pos_min: float | None = None,
    v_neg_max: float | None = None,
    sat_fraction: float = 0.2,
) -> dict:
    """Fit linear models to the positive and negative saturation branches.

    Parameters
    ----------
    V, I : voltage and current arrays (same length)
    v_pos_min : lower voltage bound for the positive saturation region.
        If *None*, uses ``V_max - sat_fraction * V_range``.
    v_neg_max : upper voltage bound for the negative saturation region.
        If *None*, uses ``V_min + sat_fraction * V_range``.
    sat_fraction : fraction of the total V range used for each outer
        branch when auto-determining boundaries (default 0.2 = 20 %).

    Returns
    -------
    dict with: slope_pos, intercept_pos, slope_neg, intercept_neg,
    slope_avg, v_pos_min, v_neg_max, i_sat_pos, i_sat_neg, n_pos, n_neg.
    """
    v_range = float(V.max() - V.min())
    if v_pos_min is None:
        v_pos_min = float(V.max()) - sat_fraction * v_range
    if v_neg_max is None:
        v_neg_max = float(V.min()) + sat_fraction * v_range

    mask_pos = V >= v_pos_min
    mask_neg = V <= v_neg_max
    n_pos = int(mask_pos.sum())
    n_neg = int(mask_neg.sum())

    if n_pos < 2 or n_neg < 2:
        raise ValueError(
            f"Not enough points in saturation regions "
            f"(pos: {n_pos}, neg: {n_neg}).  Adjust boundaries or "
            f"sat_fraction.")

    slope_pos, intercept_pos = np.polyfit(V[mask_pos], I[mask_pos], 1)
    slope_neg, intercept_neg = np.polyfit(V[mask_neg], I[mask_neg], 1)
    slope_avg = (slope_pos + slope_neg) / 2.0

    return {
        "slope_pos": float(slope_pos),
        "intercept_pos": float(intercept_pos),
        "slope_neg": float(slope_neg),
        "intercept_neg": float(intercept_neg),
        "slope_avg": float(slope_avg),
        "v_pos_min": float(v_pos_min),
        "v_neg_max": float(v_neg_max),
        "i_sat_pos": float(intercept_pos),
        "i_sat_neg": float(intercept_neg),
        "n_pos": n_pos,
        "n_neg": n_neg,
    }


def correct_iv_curve(
    V: np.ndarray,
    I: np.ndarray,
    fit: dict,
) -> np.ndarray:
    """Subtract the average sheath-expansion slope from the I-V data.

    Returns the corrected current array ``I - slope_avg * V``.
    The result should approximate a sheath-free double-probe curve
    bounded by ±I_sat.
    """
    return I - fit["slope_avg"] * V


# ── plasma-parameter estimation ──────────────────────────────────────

_E_CHARGE = 1.602176634e-19   # C
_K_BOLTZ  = 1.380649e-23      # J/K
_EV_TO_K  = 11604.52          # K/eV


def _tanh_model(v, i_sat, w):
    """I = I_sat * tanh(V / W);  W = 2*T_e."""
    return i_sat * np.tanh(v / w)


def compute_plasma_params(
    V: np.ndarray,
    I_corrected: np.ndarray,
    fit: dict,
    probe_area_m2: float,
    ion_mass_kg: float | None = None,
) -> dict:
    """Estimate T_e and optionally n_i from a sheath-corrected DLP curve.

    Model
    -----
    I_corr(V) = I_sat * tanh(V / W),  W = 2·T_e [V].

    Returns fit parameters, uncertainties (from covariance matrix),
    goodness-of-fit (R², RMSE), and the fit curve for plotting.
    """
    from scipy.optimize import curve_fit

    i_sat_guess = abs(fit.get("i_sat_pos", 1e-3))
    nan_result = {
        "Te_eV": float("nan"), "Te_err_eV": float("nan"),
        "I_sat_fit_A": float("nan"), "W_fit_V": float("nan"),
        "R2": float("nan"), "RMSE": float("nan"),
        "fit_V": np.array([]), "fit_I": np.array([]),
        "v_Bohm_ms": float("nan"), "n_i_m3": float("nan"),
    }
    try:
        popt, pcov = curve_fit(
            _tanh_model, V, I_corrected,
            p0=[i_sat_guess, 6.0],
            bounds=([0, 0.1], [1.0, 200.0]),
            maxfev=2000,
        )
    except Exception:
        return nan_result

    i_sat_fit = float(popt[0])
    w_fit = float(popt[1])
    te_eV = w_fit / 2.0

    # uncertainty from covariance matrix
    if pcov is not None and np.isfinite(pcov[1, 1]):
        sigma_w = float(np.sqrt(pcov[1, 1]))
        sigma_te = sigma_w / 2.0
    else:
        sigma_w = float("nan")
        sigma_te = float("nan")

    # goodness of fit
    I_fit = _tanh_model(V, *popt)
    residuals = I_corrected - I_fit
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((I_corrected - np.mean(I_corrected))**2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    rmse = float(np.sqrt(np.mean(residuals**2)))

    # smooth fit curve for plotting
    v_plot = np.linspace(float(V.min()), float(V.max()), 200)
    i_plot = _tanh_model(v_plot, *popt)

    result = {
        "Te_eV": te_eV,
        "Te_err_eV": sigma_te,
        "I_sat_fit_A": i_sat_fit,
        "W_fit_V": w_fit,
        "R2": r2,
        "RMSE": rmse,
        "fit_V": v_plot,
        "fit_I": i_plot,
    }

    if ion_mass_kg and ion_mass_kg > 0 and probe_area_m2 > 0:
        te_K = te_eV * _EV_TO_K
        v_bohm = ((_K_BOLTZ * te_K) / ion_mass_kg) ** 0.5
        n_i = i_sat_fit / (_E_CHARGE * probe_area_m2 * v_bohm)
        result["v_Bohm_ms"] = float(v_bohm)
        result["n_i_m3"] = float(n_i)
    else:
        result["v_Bohm_ms"] = float("nan")
        result["n_i_m3"] = float("nan")

    return result


# ── main (CLI) ────────────────────────────────────────────────────────


def main():
    """Load a DLP CSV, analyse saturation branches, show plot."""
    if len(sys.argv) < 2:
        print("Usage: python DoubleLangmuirAnalysis_v2.py <csv_file>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    meta, data = parse_dlp_csv(path)
    V = data["V_ist"]
    I = data["I_mean"]

    print(f"File:   {path.name}")
    print(f"Points: {len(V)}")
    for k in ("Run_Status", "Date", "Instrument", "Bidirectional"):
        if k in meta:
            print(f"{k}: {meta[k]}")

    # basic metrics
    metrics = compute_metrics(V, I)
    print(f"\n--- Basic Metrics ---")
    for k, v in metrics.items():
        print(f"  {k}: {v:.6g}")
    if "compl" in data:
        print(f"  compliance_points: {sum(data['compl'])}/{len(V)}")

    # saturation fit
    fit = fit_saturation_branches(V, I)
    I_corr = correct_iv_curve(V, I, fit)
    print(f"\n--- Saturation Fit ---")
    print(f"  Positive branch:  slope = {fit['slope_pos']:.4e} A/V,  "
          f"I_sat = {fit['i_sat_pos']:.4e} A  "
          f"({fit['n_pos']} pts, V >= {fit['v_pos_min']:.1f} V)")
    print(f"  Negative branch:  slope = {fit['slope_neg']:.4e} A/V,  "
          f"I_sat = {fit['i_sat_neg']:.4e} A  "
          f"({fit['n_neg']} pts, V <= {fit['v_neg_max']:.1f} V)")
    print(f"  Average slope:    {fit['slope_avg']:.4e} A/V")
    print(f"  |I_sat+|/|I_sat-|: "
          f"{abs(fit['i_sat_pos']/fit['i_sat_neg']):.4f}"
          if fit["i_sat_neg"] != 0 else "  I_sat- = 0")

    # plot
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 5.5))

    # 1) raw data (always visible, solid)
    ax.plot(V, I, "o-", ms=3, color="#4f8ef7", label="Rohdaten", zorder=3)

    # 2) fit lines in saturation regions
    v_fit_pos = np.array([fit["v_pos_min"], V.max()])
    ax.plot(v_fit_pos,
            fit["slope_pos"] * v_fit_pos + fit["intercept_pos"],
            "-", color="#e74c3c", lw=1.5, alpha=0.7,
            label=f"Fit pos (slope={fit['slope_pos']:.2e})")
    v_fit_neg = np.array([V.min(), fit["v_neg_max"]])
    ax.plot(v_fit_neg,
            fit["slope_neg"] * v_fit_neg + fit["intercept_neg"],
            "-", color="#e67e22", lw=1.5, alpha=0.7,
            label=f"Fit neg (slope={fit['slope_neg']:.2e})")

    # 3) corrected curve (dashed, distinct colour)
    ax.plot(V, I_corr, "--", color="#2ecc71", lw=1.8,
            label="Korrigiert (sheath entfernt)", zorder=2)

    # 4) horizontal lines for ±I_sat
    ax.axhline(fit["i_sat_pos"], ls=":", color="#888", lw=0.8)
    ax.axhline(fit["i_sat_neg"], ls=":", color="#888", lw=0.8)

    ax.set_xlabel("Voltage (V)")
    ax.set_ylabel("Current (A)")
    ax.set_title(f"{path.stem} – Saturation Correction")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(True, alpha=0.3)
    from utils import apply_clean_axis_format
    apply_clean_axis_format(ax)
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
