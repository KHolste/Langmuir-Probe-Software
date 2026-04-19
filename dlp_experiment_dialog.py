"""Experiment-parameter dialog for the Langmuir-Probe Monitor.

Two physically distinct concepts live in this module and must
**never** be conflated:

1. *Feed gas composition* — what the operator lets into the
   chamber at the gas inlet, expressed in sccm.  A molecular feed
   gas (O\u2082, N\u2082, H\u2082) is always entered as a flow of
   intact molecules; the sccm→mg/s conversion therefore always
   uses the neutral molecular molar mass from :data:`GAS_DATA`.
   Entering ``1 sccm of O2`` means ``1 sccm of O2 molecules``,
   *regardless* of any ion-composition choice the operator makes
   below.

2. *Plasma-phase positive-ion composition* — what positive ion
   the operator assumes dominates at the probe sheath.
   Low-temperature plasmas dissociate molecular feed gases into
   atoms to varying degrees (O\u2082 \u2192 2\u202fO,
   N\u2082 \u2192 2\u202fN, H\u2082 \u2192 2\u202fH); which
   positive ion (O\u2082\u207a vs O\u207a, N\u2082\u207a vs
   N\u207a, H\u2082\u207a vs H\u207a) dominates at the sheath is
   a plasma-state property depending on power, pressure, and
   residence time.  This module lets the operator express that
   assumption per gas via :data:`ION_COMPOSITION_MODES`.  The
   assumption enters only the Bohm density formula's ion-mass
   term; it does **not** rescale the feed flow.

Effective ion-mass weighting used here is the arithmetic,
flow-weighted mean

    m_i,eff = \u03a3_g f_g \u00b7 m_ion,g / \u03a3_g f_g,

where ``f_g`` is the gas ``g``'s feed flow in sccm and
``m_ion,g`` is the plasma-phase per-gas positive-ion mass implied
by the operator's composition choice.  This is a widely-used
pragmatic approximation: true plasma ion-density ratios are
*not* in general equal to feed-flow ratios (they depend on
ionisation rates and residence time), and a rigorous single-
effective-mass reduction of a multi-ion Bohm flux yields a
harmonic-like form.  The arithmetic-mean choice is preserved
here deliberately so every probe method (Single, Double, Triple)
shares the same assumption; the help dialog
(:mod:`dlp_experiment_help`) surfaces the caveat to the operator.

Gas species selection, flow rates (sccm / mg/s), and per-gas ion
composition for up to 3 feed-gas components.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QDoubleSpinBox, QComboBox, QLabel, QGroupBox, QDialogButtonBox,
    QPushButton, QWidget, QFrame,
)

# ── gas data (M in g/mol = Da for atomic ions) ──────────────────────

GAS_DATA: dict[str, float] = {
    "Ar":  39.948,
    "He":   4.003,
    "Ne":  20.180,
    "Xe": 131.293,
    "Kr":  83.798,
    "N2":  28.014,
    "O2":  31.998,
    "H2":   2.016,
}

#: For every **molecular** feed gas, the positive-ion mass carried
#: by a fully-dissociated atomic ion at the sheath (O\u207a from
#: O\u2082, N\u207a from N\u2082, H\u207a from H\u2082).  Monatomic
#: gases (Ar, He, …) are intentionally absent: there is no
#: molecular\u2194atomic positive-ion ambiguity for them.
#:
#: Entries here describe a **plasma-phase** assumption — which
#: positive ion the operator thinks dominates at the probe sheath —
#: *not* the feed-gas identity.  The feed flow is and always
#: remains a molecular inlet flow for these gases.
#:
#: Values are the standard atomic weights (CODATA) in u.  They feed
#: into :func:`effective_ion_mass_kg_with_unc` when the operator
#: selects the atomic, mixed, or unknown composition mode.
ATOMIC_ION_MASS_U: dict[str, float] = {
    "O2": 15.999,     # O⁺
    "N2": 14.007,     # N⁺
    "H2":  1.008,     # H⁺
}

#: Operator-facing ion-composition modes.  Defaults to ``"molecular"``
#: which reproduces the pre-existing behaviour for every gas.
#:
#: ``"mixed"`` lets the operator specify a best estimate for the
#: atomic-ion fraction ``x_atomic`` plus its half-width uncertainty
#: ``x_atomic_unc``.  It is the practical middle ground between the
#: two endpoints and the deliberately-wide ``"unknown"`` mode.
ION_COMPOSITION_MODES = (
    "molecular", "atomic", "mixed", "unknown",
)

#: Fallback when an unknown / malformed mode string arrives from a
#: reloaded experiment preset or an older config file.
DEFAULT_ION_COMPOSITION_MODE = "molecular"

# STP conversion: 1 sccm → mol/s  (0 °C, 101325 Pa, ideal gas)
SCCM_TO_MOL_S = 101325.0 * (1e-6 / 60.0) / (8.314 * 273.15)  # ≈ 7.436e-7

#: Atomic-mass-unit → kg factor (Avogadro-inverse times g→kg).
_U_TO_KG = 1.6605e-27


def sccm_to_mgs(sccm: float, M_gmol: float) -> float:
    """Convert sccm to mg/s for a gas with molar mass *M_gmol* (g/mol)."""
    return sccm * SCCM_TO_MOL_S * M_gmol * 1000.0  # g→mg


def mgs_to_sccm(mgs: float, M_gmol: float) -> float:
    """Convert mg/s to sccm."""
    if M_gmol <= 0:
        return 0.0
    return mgs / (SCCM_TO_MOL_S * M_gmol * 1000.0)


def _per_gas_ion_mass_u(gas: str, mode: str,
                          x_atomic: float = 0.0,
                          x_atomic_unc: float = 0.0,
                          ) -> tuple[float, float]:
    """Per-gas *positive-ion mass* assumption at the probe sheath.

    Returns ``(m_u, sigma_u)`` in atomic mass units where ``m_u`` is
    the effective **plasma-phase** positive-ion mass for the feed
    gas ``gas`` under the operator-selected ``mode`` and ``sigma_u``
    is its half-width ion-composition uncertainty.

    The feed gas identity itself is **never** rewritten here.  For
    a molecular feed gas (O\u2082, N\u2082, H\u2082) selecting
    ``"atomic"`` expresses the assumption "the plasma dissociates
    enough that the dominant positive ion at my sheath is O\u207a /
    N\u207a / H\u207a"; it does not mean the operator is feeding
    atomic oxygen / nitrogen / hydrogen at the inlet and it does
    not rescale the flow of molecules.  The feed flow stays
    molecular at the top of the dialog regardless of this choice.

    * ``"molecular"`` — m = molecular-ion mass (intact molecule;
      e.g. O\u2082\u207a at ~32\u202fu).  \u03c3 = 0.
    * ``"atomic"`` — m = atomic-ion mass for the plasma-side
      dissociation product (e.g. O\u207a at ~16\u202fu) when the
      feed gas has an :data:`ATOMIC_ION_MASS_U` entry, else the
      neutral mass.  \u03c3 = 0 (the operator explicitly claims
      knowledge of the dominant sheath-side ion).
    * ``"mixed"`` — m = (1\u2212x)\u00b7m_mol + x\u00b7m_atomic;
      \u03c3 = |m_mol \u2212 m_atomic|\u00b7\u0394x.  ``x_atomic``
      is the operator's best-estimate *plasma-phase* atomic-ion
      fraction in ``[0, 1]``; ``x_atomic_unc`` is its half-width
      uncertainty.  For monatomic feed gases (no atomic-ion
      entry) the result collapses to ``molecular`` mode — mixed
      has no meaning there.
    * ``"unknown"`` — m = mid-point between molecular- and atomic-
      ion mass; \u03c3 = half the span.  The 1.96\u00b7\u03c3 CI
      on ``n_i`` spans the full molecular\u2194atomic bracket.
      Monatomic feed gases collapse to ``molecular``.
    """
    M_mol = float(GAS_DATA.get(gas, 0.0))
    if M_mol <= 0.0:
        return 0.0, 0.0
    M_atomic = ATOMIC_ION_MASS_U.get(gas)
    if mode == "atomic":
        return (float(M_atomic) if M_atomic is not None else M_mol), 0.0
    if mode == "mixed" and M_atomic is not None:
        x = max(0.0, min(1.0, float(x_atomic)))
        dx = max(0.0, min(0.5, float(x_atomic_unc)))
        span = abs(M_mol - float(M_atomic))
        mean_u = (1.0 - x) * M_mol + x * float(M_atomic)
        return mean_u, span * dx
    if mode == "unknown" and M_atomic is not None:
        mid = 0.5 * (M_mol + float(M_atomic))
        half = 0.5 * abs(M_mol - float(M_atomic))
        return mid, half
    # "molecular", or any other mode for a monatomic gas → no change.
    return M_mol, 0.0


def _resolve_per_gas_entry(gas: str,
                              per_gas: dict | None,
                              *,
                              default_mode: str,
                              default_x: float,
                              default_dx: float,
                              ) -> tuple[str, float, float]:
    """Return ``(mode, x_atomic, x_atomic_unc)`` for a single gas.

    The per-gas dict — when present and containing a full triple for
    ``gas`` — wins over the ``default_*`` fall-backs.  Missing keys
    inside a partial per-gas entry fall through one at a time, so an
    operator who only overrode the mode for O₂ still picks up the
    global defaults for ``x_atomic`` / ``x_atomic_unc``.

    Monatomic gases (no entry in :data:`ATOMIC_ION_MASS_U`) have no
    molecular↔atomic ambiguity, so the returned mode collapses to
    ``"molecular"`` regardless of what the caller asked for — this is
    the same "silent collapse" the legacy helper already performed at
    the math layer; surfacing it here keeps diagnostics (e.g. the
    dialog summary) honest.
    """
    # Monatomic gases ignore any composition choice.
    if gas not in ATOMIC_ION_MASS_U:
        return DEFAULT_ION_COMPOSITION_MODE, 0.0, 0.0

    entry = (per_gas or {}).get(gas) if isinstance(per_gas, dict) else None
    if not isinstance(entry, dict):
        return default_mode, default_x, default_dx

    mode = str(entry.get("mode", default_mode) or default_mode)
    if mode not in ION_COMPOSITION_MODES:
        mode = default_mode
    try:
        x_at = float(entry.get("x_atomic", default_x))
    except (TypeError, ValueError):
        x_at = default_x
    try:
        dx = float(entry.get("x_atomic_unc", default_dx))
    except (TypeError, ValueError):
        dx = default_dx
    return mode, x_at, dx


def effective_ion_mass_kg_with_unc(
    gases: list[dict], *,
    mode: str = DEFAULT_ION_COMPOSITION_MODE,
    x_atomic: float = 0.0,
    x_atomic_unc: float = 0.0,
    per_gas_composition: dict | None = None,
) -> tuple[float | None, float]:
    """Feed-flow-weighted arithmetic mean plasma-phase ion mass.

    Given the feed-gas mixture ``gases`` (each entry
    ``{"gas", "flow_sccm"}``) and the operator's composition
    choice, returns an effective positive-ion mass
    ``m_i_eff`` and its relative ion-composition uncertainty.
    The feed flow values are taken as-is — **never** rescaled by
    atom count or by any assumed dissociation factor.

    Math:

    ::

        m_i_eff = \u03a3_g f_g \u00b7 m_ion,g / \u03a3_g f_g
        \u03c3_m   = sqrt(\u03a3_g (f_g \u00b7 \u03c3_ion,g)\u00b2) / \u03a3_g f_g
        rel_unc = \u03c3_m / m_i_eff

    where ``f_g`` is ``gas``'s feed flow (sccm) and ``m_ion,g`` is
    its plasma-phase ion-mass assumption produced by
    :func:`_per_gas_ion_mass_u`.  This is a pragmatic first-order
    approximation used throughout the low-temperature Langmuir
    community when a full plasma-chemistry model is not available;
    see :mod:`dlp_experiment_help` for the physical caveats
    (plasma ion-density ratios are NOT in general equal to feed-
    flow ratios; a rigorous multi-ion Bohm reduction yields a
    harmonic-like form; feed-flow weighting is the conservative
    baseline shared across Single / Double / Triple).

    ``mode`` is one of :data:`ION_COMPOSITION_MODES` and applies
    to any gas without a per-gas override.

    * ``m_kg``    — None when the total flow is zero, else the
      flow-weighted mean ion mass in kilograms.
    * ``rel_unc`` — the flow-weighted relative uncertainty from
      ion-composition ambiguity alone.  0.0 for modes "molecular"
      and "atomic".  Non-zero for "unknown" whenever a flowing gas
      has an atomic-ion entry; non-zero for "mixed" whenever
      ``x_atomic_unc`` is > 0 and at least one flowing gas has an
      atomic-ion entry.

    ``x_atomic`` / ``x_atomic_unc`` are only consulted when
    ``mode == "mixed"``.  Both are ignored for the other modes, so
    passing non-zero values does not accidentally widen the CI for
    an operator who is using "molecular" or "atomic".

    ``per_gas_composition`` (optional) lets the caller override the
    composition triple *per gas symbol*.  When present, each
    molecular gas reads ``per_gas_composition[gas]`` and falls back
    to the global ``(mode, x_atomic, x_atomic_unc)`` otherwise.
    This keeps the legacy single-gas workflow 100 % backward-compat
    while enabling explicit O\u2082-only / N\u2082-only / H\u2082-
    only plasma-phase assumptions in a mixed feed.  Unknown per-gas
    keys (e.g. stale entries left over from a deselected gas) are
    simply not consulted.
    """
    # Normalise the global default mode so a stale preset cannot
    # silently switch semantics.  Defaults to "molecular" when
    # malformed.
    if mode not in ION_COMPOSITION_MODES:
        mode = DEFAULT_ION_COMPOSITION_MODE
    total = sum(float(g.get("flow_sccm", 0) or 0.0) for g in gases)
    if total <= 0:
        return None, 0.0
    m_sum_u = 0.0
    # We propagate the per-gas sigmas via a flow-weighted quadrature
    # sum on the numerator; the denominator is the (exact) total
    # flow.  This keeps the relative-uncertainty scale consistent
    # with how the mass is computed.
    var_sum_u2 = 0.0
    for g in gases:
        f = float(g.get("flow_sccm", 0) or 0.0)
        if f <= 0:
            continue
        gas_name = str(g.get("gas", ""))
        g_mode, g_x, g_dx = _resolve_per_gas_entry(
            gas_name, per_gas_composition,
            default_mode=mode, default_x=x_atomic,
            default_dx=x_atomic_unc)
        m_u, sig_u = _per_gas_ion_mass_u(
            gas_name, g_mode,
            x_atomic=g_x, x_atomic_unc=g_dx)
        if m_u <= 0.0:
            continue
        m_sum_u += f * m_u
        var_sum_u2 += (f * sig_u) ** 2
    if m_sum_u <= 0.0:
        return None, 0.0
    mean_u = m_sum_u / total
    sigma_u = (var_sum_u2 ** 0.5) / total
    rel = (sigma_u / mean_u) if mean_u > 0 else 0.0
    return mean_u * _U_TO_KG, float(rel)


def effective_ion_mass_kg(
    gases: list[dict], *,
    mode: str = DEFAULT_ION_COMPOSITION_MODE,
    x_atomic: float = 0.0,
    x_atomic_unc: float = 0.0,
    per_gas_composition: dict | None = None,
) -> float | None:
    """Flow-weighted mean ion mass in kg from a gas list.

    Each entry: {"gas": str, "flow_sccm": float}.  Returns None if
    total flow is zero.

    Kept as a thin backward-compatible wrapper around
    :func:`effective_ion_mass_kg_with_unc`.  Callers that do not pass
    ``mode`` get the pre-existing "neutral molar mass" answer for
    every gas — an Ar-only discharge behaves exactly as before, and
    every existing test keeps passing.
    """
    m_kg, _unc = effective_ion_mass_kg_with_unc(
        gases, mode=mode,
        x_atomic=x_atomic, x_atomic_unc=x_atomic_unc,
        per_gas_composition=per_gas_composition)
    return m_kg


def per_gas_breakdown(
    gases: list[dict], *,
    mode: str = DEFAULT_ION_COMPOSITION_MODE,
    x_atomic: float = 0.0,
    x_atomic_unc: float = 0.0,
    per_gas_composition: dict | None = None,
) -> list[dict]:
    """Return a list of dicts, one per flowing gas, explaining the
    per-gas contribution to the effective ion-mass calculation.

    Each entry carries:

    * ``gas``           — gas symbol (e.g. ``"O2"``).
    * ``flow_sccm``     — the configured flow (> 0).
    * ``flow_fraction`` — flow share of the total (0‥1).
    * ``is_molecular``  — True when the gas has an atomic-ion entry.
    * ``mode`` / ``x_atomic`` / ``x_atomic_unc`` — the composition
      triple that will actually be applied to this row (after
      per-gas-override / global-fallback resolution).  Monatomic
      gases report ``"molecular"`` since the knob has no effect.
    * ``m_ion_u`` / ``sigma_u`` — per-gas central mass + its
      quadrature-contributing sigma, both in atomic mass units.

    Intended for operator diagnostics (summary label, sidecar,
    test assertions) — not for the Bohm formula itself.  The
    Bohm formula consumes only the aggregated flow-weighted mean
    returned by :func:`effective_ion_mass_kg_with_unc`.
    """
    total = sum(float(g.get("flow_sccm", 0) or 0.0) for g in gases)
    out: list[dict] = []
    if total <= 0:
        return out
    for g in gases:
        f = float(g.get("flow_sccm", 0) or 0.0)
        if f <= 0:
            continue
        gas_name = str(g.get("gas", ""))
        if gas_name == "":
            continue
        is_mol = gas_name in ATOMIC_ION_MASS_U
        g_mode, g_x, g_dx = _resolve_per_gas_entry(
            gas_name, per_gas_composition,
            default_mode=mode, default_x=x_atomic,
            default_dx=x_atomic_unc)
        m_u, sig_u = _per_gas_ion_mass_u(
            gas_name, g_mode, x_atomic=g_x, x_atomic_unc=g_dx)
        out.append({
            "gas": gas_name,
            "flow_sccm": f,
            "flow_fraction": f / total,
            "is_molecular": bool(is_mol),
            "mode": g_mode,
            "x_atomic": float(g_x),
            "x_atomic_unc": float(g_dx),
            "m_ion_u": float(m_u),
            "sigma_u": float(sig_u),
        })
    return out


DEFAULT_EXPERIMENT_PARAMS: dict = {
    "gases": [
        {"gas": "Ar", "flow_sccm": 0.1},
        {"gas": "", "flow_sccm": 0.0},
        {"gas": "", "flow_sccm": 0.0},
    ],
    # Legacy global default.  Still respected: any molecular gas
    # without an explicit entry in ``per_gas_composition`` falls
    # back to this triple.  Kept at "molecular" / 0 / 0 so an
    # operator who never opens the ion-composition editor keeps
    # the pre-existing neutral-mass behaviour.
    "ion_composition_mode": DEFAULT_ION_COMPOSITION_MODE,
    # Only consulted when ``ion_composition_mode == "mixed"``.
    # Defaults chosen so a legacy preset without these keys maps
    # cleanly to "no atomic fraction, no uncertainty".
    "x_atomic": 0.0,
    "x_atomic_unc": 0.0,
    # Preset key (see ``ion_composition_presets``).  "custom" means
    # "no preset currently applied, use the (mode, x, Δx) values
    # verbatim".  Persisted in sidecars for reproducibility.
    "ion_composition_preset": "custom",
    # Per-molecular-gas composition overrides.  Map of gas symbol →
    # ``{"mode": str, "x_atomic": float, "x_atomic_unc": float,
    #    "preset": str}``.  Only molecular gases (those in
    # :data:`ATOMIC_ION_MASS_U`) are meaningful here; entries for
    # monatomic gases are silently ignored.  Empty by default so a
    # fresh experiment params dict uses the legacy global triple.
    "per_gas_composition": {},
}


class ExperimentParameterDialog(QDialog):
    """Dialog for gas species and flow-rate entry (up to 3 components)."""

    def __init__(self, params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Experiment Parameters")
        self.setMinimumWidth(420)
        self._params = _deep_copy_params(params or DEFAULT_EXPERIMENT_PARAMS)
        # Guard flag: suppresses the "mark preset as Custom" slots
        # while the code is programmatically filling the composition
        # fields from a preset.
        self._applying_preset = False

        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)
        grp = QGroupBox("Feed gas composition (inlet flows, sccm)")
        grid = QGridLayout(grp)

        # Operator-facing hint: the flows in this group are always
        # taken as molecular feed flows at the inlet.  The
        # ion-composition section below describes a plasma-phase
        # assumption; it never rewrites what is shown here.
        _lbl_feed_note = QLabel(
            "Enter what you let into the chamber \u2014 molecular "
            "gases (O\u2082, N\u2082, H\u2082) are entered as "
            "molecular feed flows.  Any plasma-phase dissociation "
            "assumption is set below, not here.")
        _lbl_feed_note.setWordWrap(True)
        _lbl_feed_note.setStyleSheet(
            "color:#a0b0c8; font-size:10px;")
        grid.addWidget(_lbl_feed_note, 0, 0, 1, 3)

        grid.addWidget(QLabel("Gas"), 1, 0)
        grid.addWidget(QLabel("Flow (sccm)"), 1, 1)
        grid.addWidget(QLabel("Flow (mg/s)"), 1, 2)

        self._gas_combos: list[QComboBox] = []
        self._flow_spins: list[QDoubleSpinBox] = []
        self._mgs_labels: list[QLabel] = []

        gases = self._params.get("gases", DEFAULT_EXPERIMENT_PARAMS["gases"])
        for i in range(3):
            g = gases[i] if i < len(gases) else {"gas": "", "flow_sccm": 0}
            cmb = QComboBox()
            cmb.addItems(["(none)"] + sorted(GAS_DATA.keys()))
            cmb.setMinimumWidth(80)
            cmb.setMaxVisibleItems(len(GAS_DATA) + 1)
            gas_name = g.get("gas", "")
            _set_combo(cmb, gas_name if gas_name else "(none)")
            # Per-gas editors need to rebuild whenever the gas set
            # changes, so route both the legacy flow-update slot
            # and the rebuild through one composite handler.
            cmb.currentTextChanged.connect(self._on_gas_selection_changed)
            # Row 0 holds the feed-flow hint, row 1 the headers;
            # gas rows therefore start at row 2.
            grid.addWidget(cmb, i + 2, 0)

            spn = QDoubleSpinBox()
            spn.setRange(0, 9999)
            spn.setDecimals(1)
            spn.setSuffix(" sccm")
            spn.setValue(g.get("flow_sccm", 0))
            spn.valueChanged.connect(self._update_mgs)
            grid.addWidget(spn, i + 2, 1)

            lbl = QLabel("0.000 mg/s")
            lbl.setMinimumWidth(90)
            grid.addWidget(lbl, i + 2, 2)

            self._gas_combos.append(cmb)
            self._flow_spins.append(spn)
            self._mgs_labels.append(lbl)

        layout.addWidget(grp)

        # ── Ion composition ────────────────────────────────────────
        # Two-tier design:
        #
        # 1. **Per-gas** editors (top) — one compact editor per
        #    molecular gas currently selected above.  Each gas keeps
        #    its own (mode, x, Δx, preset) triple.  Monatomic gases
        #    (Ar, He, Ne, Xe, Kr) are shown as read-only "inert" rows
        #    so the operator can see they are part of the mixture but
        #    have no composition ambiguity.
        # 2. **Default** (bottom) — the legacy global (mode, x, Δx)
        #    triple, explicitly labelled as a fallback.  Molecular
        #    gases without a per-gas entry inherit this default.  For
        #    a single-molecular-gas workflow the two are equivalent;
        #    for a mixed-molecular workflow the per-gas editors take
        #    precedence.
        #
        # See dlp_experiment_help for the operator-facing explanation.
        from ion_composition_presets import (
            ION_COMPOSITION_PRESETS, CUSTOM_PRESET_KEY,
            apply_preset as _apply_preset_impl,
            detect_current_preset,
            presets_for_gas as _presets_for_gas,
        )
        self._apply_preset_impl = _apply_preset_impl
        self._CUSTOM_PRESET_KEY = CUSTOM_PRESET_KEY
        self._presets_for_gas = _presets_for_gas

        grp_ion = QGroupBox(
            "Plasma-phase ion composition "
            "(shared across Single / Double / Triple)")
        ion_v = QVBoxLayout(grp_ion)

        # Seed per-gas state.  Each entry is a plain dict keyed by
        # gas symbol; monatomic gases never appear here (they have no
        # composition ambiguity).  The UI rebuild routine reads this
        # dict and writes back into it on every edit.
        self._per_gas_state: dict[str, dict] = {}
        seeded_pg = self._params.get("per_gas_composition", {}) or {}
        if isinstance(seeded_pg, dict):
            for _g, _entry in seeded_pg.items():
                if _g in ATOMIC_ION_MASS_U and isinstance(_entry, dict):
                    self._per_gas_state[str(_g)] = {
                        "mode": str(_entry.get(
                            "mode", DEFAULT_ION_COMPOSITION_MODE)),
                        "x_atomic": float(_entry.get("x_atomic", 0.0)),
                        "x_atomic_unc": float(
                            _entry.get("x_atomic_unc", 0.0)),
                        "preset": str(_entry.get(
                            "preset", CUSTOM_PRESET_KEY)),
                    }

        # Header / instruction.  Makes the plasma-phase nature of
        # this section explicit so the operator cannot misread
        # "atomic" for O\u2082 as "I am feeding atomic oxygen at
        # the inlet".
        _lbl_hdr = QLabel(
            "<b>Plasma-phase positive-ion assumption</b> \u2014 "
            "used only in the Bohm density formula.  Selecting "
            "<i>atomic</i> for a molecular gas means you assume "
            "the plasma dissociates enough for the atomic ion to "
            "dominate at the probe sheath (e.g. O\u207a from "
            "O\u2082).  It does <b>not</b> rescale the feed flow "
            "above \u2014 molecular feed gases remain molecular "
            "feed gases at the inlet.  Inert gases (Ar, He, Ne, "
            "Kr, Xe) are included in the flow-weighted mixture "
            "with their atomic mass and have no "
            "molecular\u2194atomic ambiguity.")
        _lbl_hdr.setWordWrap(True)
        _lbl_hdr.setStyleSheet("color:#a0b0c8; font-size:10px;")
        ion_v.addWidget(_lbl_hdr)

        # Container for the dynamic per-gas editors.  One child row
        # per currently-selected gas; rebuilt by
        # ``_rebuild_per_gas_editors`` whenever the gas combos change.
        self._per_gas_container = QWidget()
        self._per_gas_layout = QVBoxLayout(self._per_gas_container)
        self._per_gas_layout.setContentsMargins(0, 0, 0, 0)
        self._per_gas_layout.setSpacing(4)
        ion_v.addWidget(self._per_gas_container)

        # Widget bookkeeping per gas so we can tear down and rebuild
        # without leaking or disconnecting the wrong signals.
        self._per_gas_editors: dict[str, dict] = {}

        # Visual separator between per-gas editors and the default.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color:#38404a;")
        ion_v.addWidget(sep)

        # ── Default block (legacy global triple) ───────────────────
        # Kept as the fallback for molecular gases without a per-gas
        # entry.  Labelled explicitly so the operator understands
        # this is a default, not a global override.
        grp_default = QGroupBox(
            "Default for molecular gases without a per-gas setting")
        grid_ion = QGridLayout(grp_default)

        grid_ion.addWidget(QLabel("Preset (gas-agnostic):"), 0, 0)
        self._cmbPreset = QComboBox()
        # Custom comes first so a fresh dialog without a preset
        # saved on disk lands on it.
        self._cmbPreset.addItem("Custom (manual values below)",
                                  CUSTOM_PRESET_KEY)
        for _p in ION_COMPOSITION_PRESETS:
            self._cmbPreset.addItem(_p.label, _p.key)
        self._cmbPreset.setToolTip(
            "Default ion-composition preset \u2014 applied to any "
            "molecular gas that does not have its own per-gas "
            "entry above.\n"
            "For a single-molecular-gas workflow this is the "
            "simplest control.  For a mixed-molecular workflow, "
            "prefer the per-gas editors above so each gas carries "
            "its own regime explicitly.")
        grid_ion.addWidget(self._cmbPreset, 0, 1)

        self._lblPresetDesc = QLabel("")
        self._lblPresetDesc.setStyleSheet(
            "color:#8890a0; font-size:10px;")
        self._lblPresetDesc.setWordWrap(True)
        grid_ion.addWidget(self._lblPresetDesc, 1, 0, 1, 2)

        detected = detect_current_preset(self._params)
        if detected is not None:
            idx_p = self._cmbPreset.findData(detected.key)
            if idx_p >= 0:
                self._cmbPreset.setCurrentIndex(idx_p)
        self._cmbPreset.currentIndexChanged.connect(
            self._on_preset_changed)

        grid_ion.addWidget(QLabel("Positive-ion assumption:"), 2, 0)
        self._cmbIonMode = QComboBox()
        self._cmbIonMode.addItem(
            "Molecular ion  (e.g. O\u2082\u207a) — default", "molecular")
        self._cmbIonMode.addItem(
            "Atomic ion  (e.g. O\u207a)", "atomic")
        self._cmbIonMode.addItem(
            "Mixed — specify x \u00b1 \u0394x", "mixed")
        self._cmbIonMode.addItem(
            "Unknown — widen n_i CI", "unknown")
        self._cmbIonMode.setToolTip(
            "Default mode applied to any molecular gas without a "
            "per-gas entry above.\n"
            "\u2022 Molecular: the positive ion is the intact "
            "molecule (typical for magnetron / moderate-pressure).\n"
            "\u2022 Atomic: the positive ion is the dissociated "
            "atom (typical for high-density / low-pressure ICP / "
            "ECR).\n"
            "\u2022 Mixed: supply x \u00b1 \u0394x.  Effective ion "
            "mass becomes m = (1\u2212x)\u00b7m_mol + "
            "x\u00b7m_atomic; uncertainty widens with \u0394x.\n"
            "\u2022 Unknown: widen the reported CI to span the "
            "full molecular\u2194atomic bracket.")
        current_mode = self._params.get("ion_composition_mode",
                                          DEFAULT_ION_COMPOSITION_MODE)
        idx = self._cmbIonMode.findData(current_mode)
        if idx < 0:
            idx = 0
        self._cmbIonMode.setCurrentIndex(idx)
        self._cmbIonMode.currentIndexChanged.connect(self._update_mgs)
        self._cmbIonMode.currentIndexChanged.connect(
            self._update_ion_mix_enabled)
        self._cmbIonMode.currentIndexChanged.connect(
            self._mark_preset_custom)
        grid_ion.addWidget(self._cmbIonMode, 2, 1)

        grid_ion.addWidget(QLabel("Atomic-ion fraction x:"), 3, 0)
        self._spnXAtomic = QDoubleSpinBox()
        self._spnXAtomic.setRange(0.0, 100.0)
        self._spnXAtomic.setDecimals(1)
        self._spnXAtomic.setSingleStep(5.0)
        self._spnXAtomic.setSuffix(" %")
        self._spnXAtomic.setValue(
            100.0 * float(self._params.get("x_atomic", 0.0)))
        self._spnXAtomic.setToolTip(
            "Default best-estimate atomic-ion fraction. "
            "Enabled only when the default mode is Mixed.")
        self._spnXAtomic.valueChanged.connect(self._update_mgs)
        self._spnXAtomic.valueChanged.connect(self._mark_preset_custom)
        grid_ion.addWidget(self._spnXAtomic, 3, 1)

        grid_ion.addWidget(QLabel("Uncertainty \u00b1 \u0394x:"), 4, 0)
        self._spnXAtomicUnc = QDoubleSpinBox()
        self._spnXAtomicUnc.setRange(0.0, 50.0)
        self._spnXAtomicUnc.setDecimals(1)
        self._spnXAtomicUnc.setSingleStep(5.0)
        self._spnXAtomicUnc.setSuffix(" %")
        self._spnXAtomicUnc.setValue(
            100.0 * float(self._params.get("x_atomic_unc", 0.0)))
        self._spnXAtomicUnc.setToolTip(
            "Half-width uncertainty on the default atomic-ion "
            "fraction.  Enabled only when the default mode is "
            "Mixed.")
        self._spnXAtomicUnc.valueChanged.connect(self._update_mgs)
        self._spnXAtomicUnc.valueChanged.connect(
            self._mark_preset_custom)
        grid_ion.addWidget(self._spnXAtomicUnc, 4, 1)

        ion_v.addWidget(grp_default)
        layout.addWidget(grp_ion)

        self._update_ion_mix_enabled()
        self._update_preset_description()

        # Summary — per-gas breakdown + mixture total.  The label's
        # default auto text format renders the plain ``<br/>``s we
        # emit as line breaks, no explicit Qt.TextFormat import
        # required.
        self._lblSummary = QLabel()
        self._lblSummary.setWordWrap(True)
        layout.addWidget(self._lblSummary)

        # Button row: Help on the left, Ok/Cancel on the right.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Help)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        help_btn = btns.button(QDialogButtonBox.StandardButton.Help)
        if help_btn is not None:
            help_btn.clicked.connect(self._on_help_clicked)
        _scroll_top.addWidget(btns)

        # Initial render.
        self._rebuild_per_gas_editors()
        self._update_mgs()

    def _on_preset_changed(self) -> None:
        """Operator picked a preset in the combo: fill the mode + x
        + Δx widgets from the preset definition, then refresh the
        enabled-state of the mixed-mode controls and the preset
        description caption.  The ``_applying_preset`` guard stops
        the mode/x/Δx signal handlers from immediately demoting the
        combo back to "Custom" while we fill the fields.
        """
        key = self._cmbPreset.currentData()
        if key == self._CUSTOM_PRESET_KEY:
            self._update_preset_description()
            return
        from ion_composition_presets import get_preset
        preset = get_preset(str(key))
        if preset is None:
            self._update_preset_description()
            return
        self._applying_preset = True
        try:
            idx = self._cmbIonMode.findData(preset.mode)
            if idx >= 0:
                self._cmbIonMode.setCurrentIndex(idx)
            self._spnXAtomic.setValue(100.0 * float(preset.x_atomic))
            self._spnXAtomicUnc.setValue(
                100.0 * float(preset.x_atomic_unc))
        finally:
            self._applying_preset = False
        self._update_ion_mix_enabled()
        self._update_preset_description()
        self._update_mgs()

    def _mark_preset_custom(self) -> None:
        """Called whenever the operator edits the mode / x / Δx
        widgets directly.  Reverts the preset combo to "Custom" so
        the dialog's visible state never pretends to be a preset
        it no longer matches.  No-op while :meth:`_on_preset_changed`
        is writing the widgets."""
        if getattr(self, "_applying_preset", False):
            return
        if not hasattr(self, "_cmbPreset"):
            return
        idx = self._cmbPreset.findData(self._CUSTOM_PRESET_KEY)
        if idx >= 0 and self._cmbPreset.currentIndex() != idx:
            with_block = self._cmbPreset.blockSignals(True)
            try:
                self._cmbPreset.setCurrentIndex(idx)
            finally:
                self._cmbPreset.blockSignals(with_block)
        self._update_preset_description()

    def _update_preset_description(self) -> None:
        """Refresh the small caption under the preset combo.  Shows
        the preset's one-line description when a real preset is
        active; blank when the selection is "Custom"."""
        if not hasattr(self, "_cmbPreset"):
            return
        key = self._cmbPreset.currentData()
        if key == self._CUSTOM_PRESET_KEY:
            self._lblPresetDesc.setText("")
            return
        from ion_composition_presets import get_preset
        preset = get_preset(str(key))
        if preset is None:
            self._lblPresetDesc.setText("")
            return
        self._lblPresetDesc.setText(preset.description)

    def _update_ion_mix_enabled(self) -> None:
        """Enable the default mixed-mode spinboxes only when the
        default mode = Mixed.  Other modes render them insensitive so
        the UI never implies the values are in effect."""
        is_mixed = (self._cmbIonMode.currentData() == "mixed")
        self._spnXAtomic.setEnabled(is_mixed)
        self._spnXAtomicUnc.setEnabled(is_mixed)

    def _on_help_clicked(self) -> None:
        """Open the Experiment Parameters help dialog.  Imported
        lazily so headless tests that only exercise the params
        model do not pay the Qt import cost."""
        try:
            from dlp_experiment_help import open_experiment_help_dialog
            open_experiment_help_dialog(parent=self)
        except Exception:
            # Help must never block the dialog — silently swallow an
            # import or render error rather than surfacing a
            # half-broken secondary window.
            pass

    def _on_gas_selection_changed(self, *_args) -> None:
        """Composite slot: the gas combos changed, so we rebuild
        both the flow readout (legacy ``_update_mgs``) and the
        per-gas composition editors."""
        self._rebuild_per_gas_editors()
        self._update_mgs()

    def _gas_name(self, i: int) -> str:
        t = self._gas_combos[i].currentText().strip()
        return "" if t == "(none)" else t

    def _current_gas_set(self) -> list[str]:
        """Ordered list of currently-selected gas symbols, deduped.

        Preserves the row order so the per-gas editors render in the
        same sequence as the gas combos.  Empty rows and duplicates
        (same gas on two rows) are dropped — a duplicate gas shares
        one composition editor rather than two that could disagree.
        """
        out: list[str] = []
        seen: set[str] = set()
        for i in range(3):
            g = self._gas_name(i)
            if not g or g in seen:
                continue
            seen.add(g)
            out.append(g)
        return out

    # ------------------------------------------------------------------
    # Per-gas composition editors
    # ------------------------------------------------------------------
    def _rebuild_per_gas_editors(self) -> None:
        """Tear down the current per-gas editor widgets and build a
        fresh set matching the currently-selected gas symbols.

        Called whenever the gas combos change and once at dialog
        construction.  State for gases that are removed is KEPT in
        ``self._per_gas_state`` so toggling a gas off and back on
        restores the previous (mode, x, Δx, preset) without the
        operator having to re-enter it.
        """
        # 1. Flush old widgets.  ``deleteLater`` avoids stale
        #    Qt-child leaks from frequent rebuilds on gas toggles.
        while self._per_gas_layout.count():
            item = self._per_gas_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass
        self._per_gas_editors.clear()

        # 2. Build one row per currently-selected gas.
        any_row = False
        for gas in self._current_gas_set():
            row = self._build_per_gas_row(gas)
            self._per_gas_layout.addWidget(row)
            any_row = True

        if not any_row:
            _empty = QLabel("(no gas configured \u2014 add a gas above "
                            "to enable per-gas composition controls)")
            _empty.setStyleSheet("color:#8890a0; font-size:10px;")
            self._per_gas_layout.addWidget(_empty)

    def _build_per_gas_row(self, gas: str) -> QWidget:
        """Construct the editor widget for a single gas row.

        Inert / monatomic gases get a read-only caption.  Molecular
        gases get the full (preset, mode, x, Δx) editor tied back to
        ``self._per_gas_state[gas]``.
        """
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # Fixed-width gas label so columns line up across rows.
        lbl = QLabel(f"<b>{gas}</b>")
        lbl.setMinimumWidth(36)
        row.addWidget(lbl)

        if gas not in ATOMIC_ION_MASS_U:
            caption = QLabel(
                "inert / monatomic \u2014 no molecular\u2194atomic "
                "ambiguity (included in mixture as-is)")
            caption.setStyleSheet("color:#8890a0; font-size:10px;")
            row.addWidget(caption, 1)
            self._per_gas_editors[gas] = {"row": frame, "molecular": False}
            return frame

        # Molecular gas — build the full editor.
        self._ensure_per_gas_state(gas)
        state = self._per_gas_state[gas]

        # Per-gas preset combo.  Uses the gas-filtered preset list
        # so an O₂ row never offers N₂ presets and vice-versa.
        cmb_preset = QComboBox()
        cmb_preset.addItem("Custom", self._CUSTOM_PRESET_KEY)
        for _p in self._presets_for_gas(gas):
            cmb_preset.addItem(_p.label, _p.key)
        idx = cmb_preset.findData(state.get("preset",
                                               self._CUSTOM_PRESET_KEY))
        if idx >= 0:
            cmb_preset.setCurrentIndex(idx)
        cmb_preset.setToolTip(
            f"Preset applied to the {gas} row only.  "
            f"Fills this row's mode / x / \u0394x fields; manual "
            f"edits revert the combo to Custom.")
        row.addWidget(cmb_preset, 1)

        cmb_mode = QComboBox()
        cmb_mode.addItem("Molecular", "molecular")
        cmb_mode.addItem("Atomic", "atomic")
        cmb_mode.addItem("Mixed", "mixed")
        cmb_mode.addItem("Unknown", "unknown")
        mdx = cmb_mode.findData(state.get(
            "mode", DEFAULT_ION_COMPOSITION_MODE))
        if mdx >= 0:
            cmb_mode.setCurrentIndex(mdx)
        cmb_mode.setToolTip(
            f"Positive-ion assumption for {gas}.")
        row.addWidget(cmb_mode)

        spn_x = QDoubleSpinBox()
        spn_x.setRange(0.0, 100.0)
        spn_x.setDecimals(1)
        spn_x.setSingleStep(5.0)
        spn_x.setSuffix(" %")
        spn_x.setMinimumWidth(86)
        spn_x.setValue(100.0 * float(state.get("x_atomic", 0.0)))
        spn_x.setToolTip(f"Atomic-ion fraction x for {gas} (Mixed mode).")
        row.addWidget(spn_x)

        spn_dx = QDoubleSpinBox()
        spn_dx.setRange(0.0, 50.0)
        spn_dx.setDecimals(1)
        spn_dx.setSingleStep(5.0)
        spn_dx.setSuffix(" %")
        spn_dx.setMinimumWidth(86)
        spn_dx.setValue(100.0 * float(state.get("x_atomic_unc", 0.0)))
        spn_dx.setToolTip(
            f"Half-width uncertainty \u0394x for {gas} (Mixed mode).")
        row.addWidget(spn_dx)

        # Wire up signals (captured gas name via default argument).
        def _on_preset(idx_ignored, g=gas,
                        cmb_preset=cmb_preset,
                        cmb_mode=cmb_mode,
                        spn_x=spn_x,
                        spn_dx=spn_dx):
            key = cmb_preset.currentData()
            from ion_composition_presets import get_preset
            preset = get_preset(str(key or ""))
            if preset is not None:
                # Apply preset values to the per-row widgets
                # (signals still fire → state is updated and
                # summary refreshed).
                midx = cmb_mode.findData(preset.mode)
                if midx >= 0:
                    cmb_mode.setCurrentIndex(midx)
                spn_x.setValue(100.0 * float(preset.x_atomic))
                spn_dx.setValue(100.0 * float(preset.x_atomic_unc))
            self._per_gas_state[g]["preset"] = str(
                key or self._CUSTOM_PRESET_KEY)
            self._update_mgs()

        def _on_mode(idx_ignored, g=gas, cmb_mode=cmb_mode,
                      cmb_preset=cmb_preset, spn_x=spn_x, spn_dx=spn_dx):
            self._per_gas_state[g]["mode"] = str(
                cmb_mode.currentData()
                or DEFAULT_ION_COMPOSITION_MODE)
            # Enable x / Δx only when this row is Mixed.
            is_mixed = (self._per_gas_state[g]["mode"] == "mixed")
            spn_x.setEnabled(is_mixed)
            spn_dx.setEnabled(is_mixed)
            # Revert per-gas preset combo to Custom on manual edit.
            self._per_gas_mark_custom(g, cmb_preset)
            self._update_mgs()

        def _on_x(val, g=gas, cmb_preset=cmb_preset):
            self._per_gas_state[g]["x_atomic"] = float(val) / 100.0
            self._per_gas_mark_custom(g, cmb_preset)
            self._update_mgs()

        def _on_dx(val, g=gas, cmb_preset=cmb_preset):
            self._per_gas_state[g]["x_atomic_unc"] = float(val) / 100.0
            self._per_gas_mark_custom(g, cmb_preset)
            self._update_mgs()

        cmb_preset.currentIndexChanged.connect(_on_preset)
        cmb_mode.currentIndexChanged.connect(_on_mode)
        spn_x.valueChanged.connect(_on_x)
        spn_dx.valueChanged.connect(_on_dx)

        # Initial enabled-state for x / Δx.
        is_mixed = (state.get("mode") == "mixed")
        spn_x.setEnabled(is_mixed)
        spn_dx.setEnabled(is_mixed)

        self._per_gas_editors[gas] = {
            "row": frame,
            "molecular": True,
            "cmb_preset": cmb_preset,
            "cmb_mode": cmb_mode,
            "spn_x": spn_x,
            "spn_dx": spn_dx,
        }
        return frame

    def _ensure_per_gas_state(self, gas: str) -> None:
        """Lazily create a state dict for ``gas`` if none exists.

        Seeds from the default triple so a freshly-added molecular
        gas inherits the dialog's default rather than starting as
        "blank" — consistent with how the calculation would have
        treated it before the per-gas redesign.
        """
        if gas in self._per_gas_state:
            return
        self._per_gas_state[gas] = {
            "mode": str(self._params.get(
                "ion_composition_mode", DEFAULT_ION_COMPOSITION_MODE)),
            "x_atomic": float(self._params.get("x_atomic", 0.0)),
            "x_atomic_unc": float(self._params.get("x_atomic_unc", 0.0)),
            "preset": self._CUSTOM_PRESET_KEY,
        }

    def _per_gas_mark_custom(self, gas: str, cmb_preset: QComboBox) -> None:
        """After a manual mode / x / Δx edit on a per-gas row,
        revert that row's preset combo to Custom so the UI never
        pretends a preset is still in effect."""
        if gas not in self._per_gas_state:
            return
        if self._per_gas_state[gas].get("preset") == self._CUSTOM_PRESET_KEY:
            return
        self._per_gas_state[gas]["preset"] = self._CUSTOM_PRESET_KEY
        if cmb_preset is None:
            return
        idx = cmb_preset.findData(self._CUSTOM_PRESET_KEY)
        if idx < 0 or cmb_preset.currentIndex() == idx:
            return
        blocked = cmb_preset.blockSignals(True)
        try:
            cmb_preset.setCurrentIndex(idx)
        finally:
            cmb_preset.blockSignals(blocked)

    def _effective_per_gas_composition(self) -> dict:
        """Return a ``{gas: {...}}`` dict containing per-gas entries
        ONLY for gases currently selected in the gas combos.  This
        is the dict persisted in ``get_params`` — stale entries for
        deselected gases are intentionally dropped so the saved
        state always matches the visible UI.
        """
        out: dict = {}
        for gas in self._current_gas_set():
            if gas not in ATOMIC_ION_MASS_U:
                continue
            st = self._per_gas_state.get(gas)
            if not st:
                continue
            out[gas] = {
                "mode": str(st.get("mode",
                                    DEFAULT_ION_COMPOSITION_MODE)),
                "x_atomic": float(st.get("x_atomic", 0.0)),
                "x_atomic_unc": float(st.get("x_atomic_unc", 0.0)),
                "preset": str(st.get("preset",
                                       self._CUSTOM_PRESET_KEY)),
            }
        return out

    def _update_mgs(self):
        total_sccm = 0.0
        m_sum = 0.0
        for i in range(3):
            gas = self._gas_name(i)
            sccm = self._flow_spins[i].value()
            M = GAS_DATA.get(gas, 0)
            if M > 0 and sccm > 0:
                mgs = sccm_to_mgs(sccm, M)
                self._mgs_labels[i].setText(f"{mgs:.3f} mg/s")
                total_sccm += sccm
                m_sum += sccm * M
            else:
                self._mgs_labels[i].setText("— mg/s")

        if total_sccm <= 0:
            self._lblSummary.setText("No gas flow configured.")
            return

        m_eff = m_sum / total_sccm
        mode = (self._cmbIonMode.currentData()
                if hasattr(self, "_cmbIonMode")
                else DEFAULT_ION_COMPOSITION_MODE)
        gases_now = [{"gas": self._gas_name(i),
                       "flow_sccm": self._flow_spins[i].value()}
                      for i in range(3)
                      if self._gas_name(i)
                      and self._flow_spins[i].value() > 0]
        x_pct = (self._spnXAtomic.value()
                 if hasattr(self, "_spnXAtomic") else 0.0)
        dx_pct = (self._spnXAtomicUnc.value()
                  if hasattr(self, "_spnXAtomicUnc") else 0.0)
        per_gas = (self._effective_per_gas_composition()
                   if hasattr(self, "_per_gas_state") else {})
        m_ion_kg, m_ion_rel = effective_ion_mass_kg_with_unc(
            gases_now, mode=mode,
            x_atomic=x_pct / 100.0,
            x_atomic_unc=dx_pct / 100.0,
            per_gas_composition=per_gas)
        breakdown = per_gas_breakdown(
            gases_now, mode=mode,
            x_atomic=x_pct / 100.0,
            x_atomic_unc=dx_pct / 100.0,
            per_gas_composition=per_gas)

        # Build a compact per-row breakdown table.  Using plain HTML
        # so the QLabel renders it in-place without extra layout.
        rows_html = []
        for b in breakdown:
            tag = ("inert" if not b["is_molecular"]
                    else b["mode"])
            mass_txt = (f"{b['m_ion_u']:.2f} u"
                        if b["sigma_u"] <= 0
                        else (f"{b['m_ion_u']:.2f} \u00b1 "
                              f"{b['sigma_u']:.2f} u"))
            rows_html.append(
                f"&nbsp;&nbsp;\u2022 <b>{b['gas']}</b> "
                f"({b['flow_sccm']:.2f} sccm, "
                f"{100.0 * b['flow_fraction']:.0f}%) "
                f"\u2014 {tag}, m<sub>ion</sub> = {mass_txt}"
            )
        breakdown_html = "<br/>".join(rows_html)

        if m_ion_kg is not None:
            m_ion_u = m_ion_kg / _U_TO_KG
            if m_ion_rel > 0.0:
                unc_txt = (f"  |  <b>m<sub>i,eff</sub></b> "
                            f"(plasma) = {m_ion_u:.2f} \u00b1 "
                            f"{m_ion_u * m_ion_rel:.2f} u "
                            f"(\u00b1{m_ion_rel * 100.0:.1f}%, "
                            f"from ion composition)")
            else:
                unc_txt = (f"  |  <b>m<sub>i,eff</sub></b> "
                            f"(plasma) = {m_ion_u:.2f} u")
        else:
            unc_txt = ""

        # Caption distinguishes the feed-gas mean molar mass (for
        # sccm\u2192mg/s accounting) from the plasma-phase ion-mass
        # assumption m\u1d62,\u2091\u2097\u2097 that actually goes
        # into the Bohm density formula.  Without this split an
        # operator could imagine that changing the ion-composition
        # mode also changes the feed molar mass, which it does not.
        self._lblSummary.setText(
            f"<b>Total feed:</b> {total_sccm:.1f} sccm  |  "
            f"<i>Feed mean M</i> = {m_eff:.2f} g/mol "
            f"({m_eff:.2f} u)"
            f"{unc_txt}<br/>{breakdown_html}")

    def get_params(self) -> dict:
        gases = []
        for i in range(3):
            gas = self._gas_name(i)
            sccm = self._flow_spins[i].value()
            if gas and sccm > 0:
                gases.append({"gas": gas, "flow_sccm": sccm})
        mode = (self._cmbIonMode.currentData()
                if hasattr(self, "_cmbIonMode") else
                DEFAULT_ION_COMPOSITION_MODE)
        if mode not in ION_COMPOSITION_MODES:
            mode = DEFAULT_ION_COMPOSITION_MODE
        # Pull the mixed-mode inputs regardless of mode — the
        # round-trip keeps their previous values even when the
        # operator temporarily toggles away to "molecular" and back.
        x_pct = (self._spnXAtomic.value()
                 if hasattr(self, "_spnXAtomic") else 0.0)
        dx_pct = (self._spnXAtomicUnc.value()
                  if hasattr(self, "_spnXAtomicUnc") else 0.0)
        preset_key = (self._cmbPreset.currentData()
                      if hasattr(self, "_cmbPreset") else
                      self._CUSTOM_PRESET_KEY)
        per_gas = (self._effective_per_gas_composition()
                   if hasattr(self, "_per_gas_state") else {})
        return {
            "gases": gases,
            # Legacy global triple — kept as the fallback for any
            # molecular gas without a per-gas entry.  Persisted
            # verbatim so older builds that do not know about
            # ``per_gas_composition`` still get a usable value.
            "ion_composition_mode": mode,
            "x_atomic": float(x_pct) / 100.0,
            "x_atomic_unc": float(dx_pct) / 100.0,
            # Persisted verbatim so sidecars can cite the preset by
            # stable key rather than reconstructing it from the
            # (mode, x, Δx) triple.
            "ion_composition_preset": str(preset_key or
                                            self._CUSTOM_PRESET_KEY),
            # Per-gas overrides.  Only contains entries for
            # currently-selected molecular gases.  Empty dict when
            # no molecular gas is in the mixture — that's the
            # signal to downstream helpers that the global triple
            # is the only thing needed.
            "per_gas_composition": per_gas,
        }


def _set_combo(combo, text):
    idx = combo.findText(text)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentText(text)


def _deep_copy_params(p):
    import copy
    return copy.deepcopy(p)
