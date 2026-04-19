"""Shared ion-composition presets for the Experiment workflow.

A preset is just a curated tuple of (mode, x_atomic, x_atomic_unc)
with a human-readable label and a short regime description.  It
does *not* compute plasma chemistry; it is a conservative aid for
the common case where the operator would otherwise have to fill
the mode + x_atomic + Δx fields by hand.

This module is the single source of truth for:

* the preset list (``ION_COMPOSITION_PRESETS``),
* applying a preset onto an experiment-params dict
  (:func:`apply_preset`),
* detecting when the current experiment-params dict matches a
  known preset (:func:`detect_current_preset`) — used by the
  Experiment dialog to show "Custom" whenever the operator
  manually edits the values after picking a preset.

No Qt dependency: every helper is pure and trivially testable in
a headless environment.  The UI layer imports ``ION_COMPOSITION_PRESETS``
and calls the helpers; no per-dialog copy of the data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from dlp_experiment_dialog import (
    DEFAULT_ION_COMPOSITION_MODE, ION_COMPOSITION_MODES,
)

#: Reserved key used when the operator has edited the ion-composition
#: fields by hand AFTER picking a preset, or when no preset is in
#: effect.  Persisted verbatim in sidecars so a later reader can
#: tell "the operator overrode the preset" apart from "no preset was
#: ever selected" — both legitimate states.
CUSTOM_PRESET_KEY = "custom"


@dataclass(frozen=True)
class IonCompositionPreset:
    """One operator-facing regime preset.

    Fields:

    * ``key`` — stable, string-enum-like identifier.  Persisted in
      experiment params + sidecars; never translated.
    * ``label`` — short UI string shown in the combo box.
    * ``mode`` — one of :data:`ION_COMPOSITION_MODES`.  Drives the
      existing ``effective_ion_mass_kg_with_unc`` helper.
    * ``x_atomic`` — operator-facing best-estimate atomic-ion
      fraction, in ``[0, 1]``.  Only meaningful when ``mode ==
      "mixed"``.
    * ``x_atomic_unc`` — half-width uncertainty on ``x_atomic`` in
      the same units.  Only meaningful when ``mode == "mixed"``.
    * ``description`` — one-line operator-oriented explanation.  The
      dialog shows it below the combo so the selection is never a
      mystery.
    * ``scope`` — gas symbol this preset is meaningful for
      (``"O2"``, ``"N2"``, ``"H2"``) or ``"any"`` for gas-agnostic
      regimes (inert / unknown).  The per-gas preset combos filter
      by this field so an O₂ row only offers O₂ and gas-agnostic
      presets.  Legacy callers using :func:`all_presets` see the
      full list — the field is backward-compatible.
    """
    key: str
    label: str
    mode: str
    x_atomic: float
    x_atomic_unc: float
    description: str
    scope: str = "any"


#: Curated preset list.  Chosen to cover common low-temperature
#: plasma regimes actually seen in the lab at JLU-IPI.  Every preset
#: is conservative — when the regime is uncertain the preset widens
#: rather than commits.
#:
#: Order is operator-facing: the combo renders in this sequence.
ION_COMPOSITION_PRESETS: tuple[IonCompositionPreset, ...] = (
    IonCompositionPreset(
        key="inert_monatomic",
        label="Inert / monatomic (Ar, He, Kr, Xe, Ne)",
        mode="molecular",
        x_atomic=0.0,
        x_atomic_unc=0.0,
        description=("Noble / monatomic gases have no "
                      "molecular\u2194atomic ambiguity.  No widening."),
        scope="any",
    ),
    IonCompositionPreset(
        key="o2_magnetron_molecular",
        label="O\u2082 magnetron, molecular-ion-dominant",
        mode="molecular",
        x_atomic=0.0,
        x_atomic_unc=0.0,
        description=("Typical few-mTorr DC/RF magnetron in O\u2082 "
                      "\u2014 O\u2082\u207a dominates (literature "
                      "finds O\u207a at a few per-cent level)."),
        scope="O2",
    ),
    IonCompositionPreset(
        key="o2_high_power_atomic_mix",
        label="O\u2082 high-power / ICP \u2014 atomic-ion-rich",
        mode="mixed",
        x_atomic=0.70,
        x_atomic_unc=0.20,
        description=("Low-pressure high-density ICP / ECR sources: "
                      "O\u207a becomes dominant.  x\u00a0=\u00a070\u202f% "
                      "\u00b1\u00a020\u202f% keeps the CI honest."),
        scope="O2",
    ),
    IonCompositionPreset(
        key="n2_molecular",
        label="N\u2082 typical \u2014 molecular-ion-dominant",
        mode="molecular",
        x_atomic=0.0,
        x_atomic_unc=0.0,
        description=("Most low-temperature N\u2082 discharges: "
                      "N\u2082\u207a dominates.  Fall back to "
                      "\u201cUnknown\u201d for high-power plasmas where "
                      "N\u207a becomes significant."),
        scope="N2",
    ),
    IonCompositionPreset(
        key="h2_mixed",
        label="H\u2082 / H \u2014 mixed, uncertain",
        mode="mixed",
        x_atomic=0.50,
        x_atomic_unc=0.30,
        description=("Hydrogen plasmas span H\u2082\u207a / H\u2083\u207a "
                      "/ H\u207a composition strongly dependent on "
                      "power and pressure.  Default 50\u202f% "
                      "\u00b1\u00a030\u202f% \u2014 tighten only with "
                      "diagnostics."),
        scope="H2",
    ),
    IonCompositionPreset(
        key="unknown_widen_ci",
        label="Unknown \u2014 widen n_i/n_e CI",
        mode="unknown",
        x_atomic=0.0,
        x_atomic_unc=0.0,
        description=("You do not know the positive-ion composition.  "
                      "The reported CI spans the full molecular\u2194"
                      "atomic bracket."),
        scope="any",
    ),
)


#: Fast lookup table, key -> preset.  Built at import time.
_PRESETS_BY_KEY: dict[str, IonCompositionPreset] = {
    p.key: p for p in ION_COMPOSITION_PRESETS
}


def get_preset(key: str) -> IonCompositionPreset | None:
    """Return the preset for ``key`` or ``None`` when not found.
    A ``None`` return is the intended way to handle legacy sidecars
    and ``CUSTOM_PRESET_KEY`` (which has no canonical values)."""
    if not key:
        return None
    return _PRESETS_BY_KEY.get(str(key))


def all_presets() -> tuple[IonCompositionPreset, ...]:
    """Operator-ordered tuple of presets.  Provided so UI code does
    not reach into module state directly."""
    return ION_COMPOSITION_PRESETS


def presets_for_gas(gas: str) -> tuple[IonCompositionPreset, ...]:
    """Return the subset of :data:`ION_COMPOSITION_PRESETS` that
    makes sense for ``gas``.

    * Gas-agnostic presets (``scope == "any"``) are always included.
    * Gas-specific presets (``scope == gas``) are included only for
      the matching gas row.

    The per-gas preset combo in the Experiment dialog calls this so
    an O₂ row does not list N₂ presets and vice-versa — which is
    the whole point of the per-gas redesign.
    """
    gas = str(gas or "")
    return tuple(p for p in ION_COMPOSITION_PRESETS
                 if p.scope == "any" or p.scope == gas)


def apply_preset(params: dict | None,
                   preset_key: str) -> dict:
    """Return a new params dict with ``preset_key`` applied.

    Leaves the ``gases`` list and any unrelated keys untouched.
    ``CUSTOM_PRESET_KEY`` or an unknown key is a no-op on the
    composition fields but still records the selected key — this
    lets the dialog say "Custom" after manual edits without
    discarding the operator's values.
    """
    out = dict(params or {})
    preset = get_preset(preset_key)
    out["ion_composition_preset"] = str(preset_key or CUSTOM_PRESET_KEY)
    if preset is None:
        # No change to the composition fields — used when the
        # operator edits the mode / x / Δx directly.
        return out
    out["ion_composition_mode"] = preset.mode
    out["x_atomic"] = float(preset.x_atomic)
    out["x_atomic_unc"] = float(preset.x_atomic_unc)
    return out


def detect_current_preset(params: dict | None,
                            *, atol: float = 1e-6
                            ) -> IonCompositionPreset | None:
    """Return the preset whose fields match ``params``, else None.

    Used by the Experiment dialog: if the operator loads an old
    experiment that was written with preset_key already set, the
    dialog selects that preset; otherwise it picks the preset whose
    (mode, x_atomic, x_atomic_unc) triple matches the current
    values — so pre-preset-era configs snap to the right row.
    """
    if not params:
        return None
    # 1. Explicit preset_key wins when present and recognised.
    explicit = get_preset(params.get("ion_composition_preset", ""))
    if explicit is not None:
        return explicit
    # 2. Field match.
    mode = str(params.get("ion_composition_mode",
                            DEFAULT_ION_COMPOSITION_MODE))
    try:
        x_at = float(params.get("x_atomic", 0.0))
        x_unc = float(params.get("x_atomic_unc", 0.0))
    except (TypeError, ValueError):
        x_at = 0.0
        x_unc = 0.0
    for p in ION_COMPOSITION_PRESETS:
        if p.mode != mode:
            continue
        if abs(p.x_atomic - x_at) > atol:
            continue
        if abs(p.x_atomic_unc - x_unc) > atol:
            continue
        return p
    return None


def params_match_preset(params: dict | None,
                          preset_key: str, *,
                          atol: float = 1e-6) -> bool:
    """Return True iff ``params`` carries the (mode, x, Δx) values
    of the preset identified by ``preset_key``.  Useful for tests
    and for the dialog's "Custom" detection after manual edits."""
    preset = get_preset(preset_key)
    if preset is None:
        return False
    mode = str(params.get("ion_composition_mode",
                            DEFAULT_ION_COMPOSITION_MODE))
    try:
        x_at = float(params.get("x_atomic", 0.0))
        x_unc = float(params.get("x_atomic_unc", 0.0))
    except (TypeError, ValueError):
        return False
    return (mode == preset.mode
            and abs(x_at - preset.x_atomic) < atol
            and abs(x_unc - preset.x_atomic_unc) < atol)


__all__ = [
    "CUSTOM_PRESET_KEY", "IonCompositionPreset",
    "ION_COMPOSITION_PRESETS", "apply_preset", "all_presets",
    "detect_current_preset", "get_preset", "params_match_preset",
    "presets_for_gas",
]
