"""Triple-Langmuir-probe analysis — pure-math, device-free.

Phase 1 of the DLP-V3 triple-probe iteration: the four scientifically
load-bearing routines from ``Langmuir_measure_2_0.py`` are ported into
a stand-alone module.  No Qt, no instrument access, no GUI — every
function is a pure transformation of numeric inputs to numeric outputs
and is directly testable with pytest.

Ported / kept:
* Eq-11 closed form for Te (cold-ion limit, V_d12 → ∞).
* Exact Eq-10 numerical solution for Te via bisection.
* Triple-probe validity guard (V_d12 > 2·V_d13).
* Density formula with Bohm velocity and Sheath correction.
* Gas-/mass-mix helpers.

NOT ported (legacy, setup-specific or GUI-bound):
* the Pin-3 leakage / loading diagnostic (built around an external
  resistor that does not exist in the new SMU + K2000 setup),
* the ProbeBox / HCP driver class,
* the Eq-10 / Eq-11 radio-button selection UI (replaced here by the
  ``prefer_eq10`` keyword argument).

A later iteration will build a Qt window and a measurement worker on
top of this module — both will *only* call the public API below.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Physics constants
# ---------------------------------------------------------------------------
LN2: float = 0.6931471805599453
E_CHARGE: float = 1.6022e-19           # C
K_B: float = 1.381e-23                 # J/K
EV_TO_K: float = 11600.0               # K per eV
U_AMU: float = 1.66053906660e-27       # kg per atomic mass unit

#: Probe geometry default (cylindrical, ø 0.1 mm × 5 mm) — provided
#: only as a sane fallback for callers that do not yet pass an area.
DEFAULT_AREA_M2: float = 9.7075e-6

# ---------------------------------------------------------------------------
# Gas table
# ---------------------------------------------------------------------------
SPECIES_AMU: dict[str, float] = {
    "Argon (Ar)":    39.948,
    "Krypton (Kr)":  83.798,
    "Nitrogen (N2)": 28.0134,
    "Oxygen (O2)":   31.998,
    "Xenon (Xe)":    131.293,
}

#: Default species used when the gas mix is empty / unspecified.
DEFAULT_SPECIES: str = "Argon (Ar)"


def mi_from_species(species_name: str) -> float:
    """Ion mass [kg] for a known species (falls back to Argon AMU)."""
    amu = SPECIES_AMU.get(species_name, SPECIES_AMU[DEFAULT_SPECIES])
    return amu * U_AMU


def sccm_to_mgs(sccm: float, species_name: str) -> float:
    """Convert volumetric flow [sccm] to mass flow [mg/s]."""
    amu = SPECIES_AMU.get(species_name, SPECIES_AMU[DEFAULT_SPECIES])
    return sccm / 60.0 / 22414.0 * amu * 1000.0


def mgs_to_sccm(mgs: float, species_name: str) -> float:
    """Convert mass flow [mg/s] to volumetric flow [sccm]."""
    amu = SPECIES_AMU.get(species_name, SPECIES_AMU[DEFAULT_SPECIES])
    if amu <= 0:
        return 0.0
    return mgs / 1000.0 * 22414.0 / amu * 60.0


def mean_mass_kg(species_sccm: Iterable[tuple[str, float]]) -> float:
    """Flow-weighted mean ion mass [kg].

    ``species_sccm`` is an iterable of ``(species_name, sccm)`` tuples.

    Three cases:
      1. Empty input → fall back to Argon (sane default).
      2. Single entry → that species verbatim, regardless of flow.
      3. Multiple entries → flow-weighted mean over species with
         ``sccm > 0``; if all flows are zero, equal-weight mean.
    """
    items = list(species_sccm)
    if not items:
        return mi_from_species(DEFAULT_SPECIES)
    if len(items) == 1:
        return mi_from_species(items[0][0])

    with_flow = [(name, flow) for name, flow in items if flow > 0]
    if with_flow:
        total = sum(flow for _, flow in with_flow)
        m_mean = sum(SPECIES_AMU.get(name, SPECIES_AMU[DEFAULT_SPECIES])
                     * flow / total for name, flow in with_flow)
    else:
        m_mean = sum(SPECIES_AMU.get(name, SPECIES_AMU[DEFAULT_SPECIES])
                     for name, _ in items) / len(items)
    return m_mean * U_AMU


# ---------------------------------------------------------------------------
# Triple-probe validity
# ---------------------------------------------------------------------------
def triple_probe_valid(v_d12: float, v_d13: float) -> bool:
    """The standard cold-ion triple-probe model requires
    ``V_d12 > 2 · V_d13`` and finite, non-negative inputs."""
    if not (math.isfinite(v_d12) and math.isfinite(v_d13)):
        return False
    if v_d13 < 0 or v_d12 <= 0:
        return False
    if v_d12 <= 2.0 * v_d13:
        return False
    return True


# ---------------------------------------------------------------------------
# Te
# ---------------------------------------------------------------------------
def te_eq11(v_d13: float) -> float:
    """Closed-form Te from V_d13 in the cold-ion / V_d12 → ∞ limit.

    ``Te [eV] = V_d13 / ln(2)``.

    Returns NaN for negative or non-finite inputs, 0.0 for V_d13 = 0.
    """
    if not math.isfinite(v_d13):
        return float("nan")
    if v_d13 < 0:
        return float("nan")
    if v_d13 == 0:
        return 0.0
    return v_d13 / LN2


def te_eq10(v_d12: float, v_d13: float) -> float:
    """Exact Te [eV] from the implicit Eq-10 ::

        2·exp(-V_d13 / Te) = 1 + exp(-V_d12 / Te)

    Solved by bisection.  Returns NaN if the input is outside the
    physically meaningful range or if no sign-change is found within
    a generous bracket.
    """
    if not (math.isfinite(v_d12) and math.isfinite(v_d13)):
        return float("nan")
    if v_d12 <= 0:
        return float("nan")
    if v_d13 < 0:
        return float("nan")
    if v_d13 == 0:
        return 0.0
    if v_d12 <= 2.0 * v_d13:
        return float("nan")

    def f(te: float) -> float:
        return (2.0 * math.exp(-v_d13 / te)
                - (1.0 + math.exp(-v_d12 / te)))

    lo, hi = 1e-6, max(v_d12, v_d13) * 10.0
    flo, fhi = f(lo), f(hi)
    # Expand the upper bracket until f(hi) changes sign.
    iters = 0
    while (math.isfinite(fhi) and fhi <= 0.0) and iters < 60 and hi < 1e6:
        hi *= 2.0
        fhi = f(hi)
        iters += 1
    if not (math.isfinite(flo) and math.isfinite(fhi)):
        return float("nan")
    if flo == 0.0:
        return lo
    if fhi == 0.0:
        return hi
    if flo * fhi > 0:
        return float("nan")

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if not math.isfinite(fm):
            return float("nan")
        if abs(fm) < 1e-10:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def compute_te_ev(v_d12: float, v_d13: float,
                  *, prefer_eq10: bool = True) -> float:
    """Return Te [eV] using the preferred model with a graceful fallback.

    When ``prefer_eq10`` is True (default), the exact bisection is
    attempted first and Eq-11 is used as a fallback if Eq-10 yields
    a non-finite or non-positive result.  Setting the flag to False
    forces the fast closed-form Eq-11.
    """
    if not triple_probe_valid(v_d12, v_d13):
        return float("nan")
    if prefer_eq10:
        te = te_eq10(v_d12, v_d13)
        if math.isfinite(te) and te > 0:
            return te
        return te_eq11(v_d13)
    return te_eq11(v_d13)


# ---------------------------------------------------------------------------
# n_e
# ---------------------------------------------------------------------------
def compute_ne_m3(
    i_a: float,
    te_ev: float,
    v_d13: float,
    area_m2: float,
    mi_kg: float,
) -> float:
    """Electron density [1/m³] from saturated triple-probe current.

    ::

        n_e = -I · (e^{-x} / (1 - e^{-x})) / (0.61 · A · q · v_Bohm)
        with x = V_d13 / Te,  v_Bohm = sqrt(k_B · Te[K] / m_i).

    The negative sign in front of ``i_a`` mirrors the sign convention
    of the legacy code (current into the probe is negative).  Returns
    0.0 for any pathological input rather than NaN — keeps the time
    series rendering clean.
    """
    if area_m2 <= 0 or not math.isfinite(area_m2):
        return 0.0
    if not math.isfinite(te_ev) or te_ev <= 0:
        return 0.0
    if not math.isfinite(v_d13):
        return 0.0
    if not math.isfinite(i_a):
        return 0.0
    if mi_kg <= 0:
        return 0.0
    v_bohm = math.sqrt((K_B * (te_ev * EV_TO_K)) / mi_kg)
    denom = 0.61 * area_m2 * E_CHARGE * v_bohm
    if denom == 0:
        return 0.0
    x = v_d13 / te_ev
    ex = math.exp(-x)
    if abs(1.0 - ex) < 1e-12:
        return 0.0
    corr = ex / (1.0 - ex)
    return (-i_a) * corr / denom


def compute_ne_ci_m3(
    n_e_m3: float,
    mi_rel_unc: float,
) -> dict:
    """Mass-only 95 % CI for the Triple-probe density.

    Triple has no fit residual for Te per tick (Te is an algebraic
    solve of V_d12 / V_d13), so the only ingredient available for
    a per-tick n_e uncertainty is the ion-mass rel-unc coming from
    the ion-composition context.  Since ``n_e ∝ 1/√m_i``,
    ``(σ_n/n)² = ¼ · (σ_m/m)²`` and the CI is ``n_e ± 1.96·σ_n``.

    Returns a dict with keys that mirror Single / Double naming:
    ``ne_ci95_lo_m3``, ``ne_ci95_hi_m3``, ``ne_ci_method``,
    ``ne_ci_note``, ``ne_ci_m_i_rel_unc``.  When ``mi_rel_unc`` is
    zero / non-finite, or when ``n_e`` itself is not finite / ≤ 0,
    the CI is marked ``unavailable`` and the bounds are ``None`` —
    a false-tight silence would mislead the operator.
    """
    try:
        rel = max(0.0, float(mi_rel_unc))
    except (TypeError, ValueError):
        rel = 0.0
    if not math.isfinite(rel):
        rel = 0.0

    out: dict = {
        "ne_ci95_lo_m3": None,
        "ne_ci95_hi_m3": None,
        "ne_ci_method": "unavailable",
        "ne_ci_note": "fit_only",
        "ne_ci_m_i_rel_unc": rel,
    }
    if rel <= 0.0:
        return out
    if not math.isfinite(n_e_m3) or n_e_m3 <= 0.0:
        return out

    # (σ_n/n)² = ¼·(σ_m/m)² — see module docstring.
    sigma_n = n_e_m3 * 0.5 * rel
    out["ne_ci95_lo_m3"] = float(n_e_m3 - 1.96 * sigma_n)
    out["ne_ci95_hi_m3"] = float(n_e_m3 + 1.96 * sigma_n)
    out["ne_ci_method"] = "covariance"
    # Triple has no fit-based σ_T per tick, so the honest scope is
    # "ion_mix" only — not "fit+ion_mix" like Single / Double.
    out["ne_ci_note"] = "ion_mix"
    return out


# ---------------------------------------------------------------------------
# Convenience wrapper for a single triple-probe sample
# ---------------------------------------------------------------------------
def analyze_sample(
    *,
    v_d12: float,
    v_d13: float,
    i_measure_a: float,
    area_m2: float = DEFAULT_AREA_M2,
    mi_kg: Optional[float] = None,
    species_name: str = DEFAULT_SPECIES,
    prefer_eq10: bool = True,
    mi_rel_unc: float = 0.0,
) -> dict:
    """Reduce one triple-probe sample to ``{Te_eV, n_e_m3, …CI}``.

    A Triple-Probe worker calls this once per tick and feeds the
    result straight into a time-series plot or CSV row.  ``mi_kg``
    overrides the species lookup when given (use this for gas mixes).

    When ``mi_rel_unc > 0`` the result dict also carries the
    mass-only 95 % CI on ``n_e`` — same key names Single and Double
    use.  When ``mi_rel_unc == 0`` the CI is ``unavailable`` and
    the bounds are ``None`` (never a false-tight zero).
    """
    if mi_kg is None:
        mi_kg = mi_from_species(species_name)
    te = compute_te_ev(v_d12, v_d13, prefer_eq10=prefer_eq10)
    ne = compute_ne_m3(i_measure_a, te, v_d13, area_m2, mi_kg)
    result: dict = {"Te_eV": te, "n_e_m3": ne}
    result.update(compute_ne_ci_m3(ne, mi_rel_unc))
    return result
