"""JSON sidecar for analysis-option reproducibility.

When an operator runs Analyze, the values of the Single- /
Double-probe options dataclasses are written to a sidecar JSON file
placed next to the corresponding measurement CSV.  This turns
"which settings produced these T_e numbers?" from a post-hoc guess
into a machine-readable fact.

Layout: ``<measurement>.options.json``::

    {
      "schema": "lp-analysis-options",
      "schema_version": 1,
      "csv_file": "LP_2026-04-19T12-34-56_double.csv",
      "method": "double",
      "fit_model": "tanh_slope",
      "options": { ... SingleAnalysisOptions.to_dict() ... },
      "analysis": {
        "fit_status": "ok",
        "Te_eV": 3.1,
        "Te_err_eV": 0.2,
        "R2": 0.995,
        "NRMSE": 0.03,
        "grade": "good"
      },
      "written_at": "2026-04-19T12:34:58"
    }

Backward compatibility
----------------------
* Missing sidecar → :func:`read_sidecar` returns ``None``; callers
  treat this as "no saved options".
* Malformed / wrong-schema sidecar → also ``None`` (we never raise
  from a reader path).
* Unknown option keys from a future version → preserved in the dict;
  callers filter at the options dataclass level.

No new runtime dependency: stdlib-only (json + pathlib + os).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

#: Stable schema identifier for the sidecar JSON itself.
SIDECAR_SCHEMA_NAME = "lp-analysis-options"

#: Integer schema version.  Bump on any breaking layout change.
SIDECAR_SCHEMA_VERSION = 1


def sidecar_path_for_csv(csv_path) -> Path:
    """Return the conventional sidecar path for a given measurement
    CSV.  Keeps the stem and appends ``.options.json``.

    Example: ``LP_...._double.csv`` → ``LP_...._double.options.json``.
    """
    p = Path(csv_path)
    return p.with_name(p.stem + ".options.json")


def write_sidecar(csv_path, *, method: str, options: dict,
                    fit_model: str | None = None,
                    analysis_summary: dict[str, Any] | None = None,
                    when: datetime | None = None) -> Path:
    """Persist the options/summary pair next to ``csv_path``.

    Atomic: writes a ``.tmp`` sibling first then ``os.replace``-es
    it onto the target so a crash mid-write never leaves a
    half-written sidecar behind.  Returns the sidecar path on
    success.  Raises :class:`OSError` only when the filesystem
    itself refuses the write — callers should decide whether to
    surface that to the operator log.
    """
    sc = sidecar_path_for_csv(csv_path)
    sc.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SIDECAR_SCHEMA_NAME,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "csv_file": Path(csv_path).name,
        "method": str(method),
        "options": _plain(options),
        "written_at": (when or datetime.now()).replace(
            microsecond=0).isoformat(),
    }
    if fit_model:
        payload["fit_model"] = str(fit_model)
    if analysis_summary:
        payload["analysis"] = _plain(analysis_summary)
    tmp = sc.with_suffix(sc.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False,
                  default=_json_default)
    os.replace(tmp, sc)
    return sc


def read_sidecar(csv_path) -> dict | None:
    """Return the parsed sidecar dict next to ``csv_path``, or
    ``None`` when the sidecar is missing, unreadable, wrong schema,
    or malformed JSON.  Never raises — the reader path must stay
    safe for GUI callers."""
    sc = sidecar_path_for_csv(csv_path)
    if not sc.is_file():
        return None
    try:
        with open(sc, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != SIDECAR_SCHEMA_NAME:
        return None
    return data


def has_sidecar(csv_path) -> bool:
    """Return True iff a sidecar file is present next to ``csv_path``."""
    return sidecar_path_for_csv(csv_path).is_file()


# ---------------------------------------------------------------------------
def _plain(obj):
    """Recursively coerce an object tree into JSON-safe primitives.
    Handles numpy scalars and dataclass-like objects that expose
    ``to_dict`` without needing a numpy import at module load."""
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _plain(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (str, int, bool, type(None))):
        return obj
    if isinstance(obj, float):
        # JSON has no NaN/Infinity in strict mode; stringify so the
        # record stays loadable even when stdlib json strict=True.
        if obj != obj or obj in (float("inf"), float("-inf")):
            return str(obj)
        return obj
    # numpy scalars quack like floats but aren't — best-effort cast.
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


def _json_default(obj):
    """Stdlib json fallback serialiser; catches anything _plain missed."""
    try:
        return _plain(obj)
    except Exception:
        return str(obj)


__all__ = [
    "SIDECAR_SCHEMA_NAME", "SIDECAR_SCHEMA_VERSION",
    "sidecar_path_for_csv", "write_sidecar", "read_sidecar",
    "has_sidecar",
]
