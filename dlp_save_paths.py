"""Method-aware CSV save paths for LP measurements.

Single, Double, and Triple all save into:

    <base>/<method>/LP_<timestamp>_<method>.csv

Layout rules (best practices for measurement-data persistence):

  * **Predictable naming**   — ISO-like timestamp, no abbreviations
  * **Collision avoidance**  — second-resolution timestamp; on
                              collisions a numeric suffix is appended
  * **Consistent defaults**  — same helper for all three methods
  * **No historical tags**    — drop ``DLP_`` / ``DLP_TRIPLE_`` from
                              new files; the method is in the suffix
                              and in the parent folder name
  * **Method metadata**       — preserved in the CSV header (callers'
                              responsibility) AND in the path

The helpers are pure (no Qt, no IO except :meth:`Path.mkdir` on
demand), so they are trivially testable from any context.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

VALID_METHODS = ("single", "double", "triple")


def normalize_method(method) -> str:
    """Return the canonical lowercase method name.  Unknown values
    fall through to ``"double"`` (the historic default workflow)."""
    m = (str(method) if method is not None else "").strip().lower()
    return m if m in VALID_METHODS else "double"


def method_data_dir(base, method) -> Path:
    """Return ``<base>/<method>/`` and create it if necessary.

    ``base`` may be any path-like; ``method`` is normalized via
    :func:`normalize_method`.  Always returns a :class:`Path`.
    """
    p = Path(base) / normalize_method(method)
    p.mkdir(parents=True, exist_ok=True)
    return p


def make_lp_csv_path(folder, method, *, when=None) -> Path:
    """Build a method-tagged timestamped CSV path inside ``folder``.

    Filename layout::

        LP_YYYY-MM-DDTHH-MM-SS_<method>.csv

    ``when`` is mainly for tests so they can pin the timestamp.
    If a file with that exact name already exists (rare — same
    second), a ``_2``, ``_3``, ... numeric suffix is appended
    before the extension to avoid silent overwrites.
    """
    m = normalize_method(method)
    ts = (when or datetime.now()).strftime("%Y-%m-%dT%H-%M-%S")
    base = Path(folder) / f"LP_{ts}_{m}.csv"
    if not base.exists():
        return base
    # Collision: append numeric suffix.
    n = 2
    while True:
        candidate = Path(folder) / f"LP_{ts}_{m}_{n}.csv"
        if not candidate.exists():
            return candidate
        n += 1


def make_lp_csv_path_for_method(base, method, *, when=None) -> Path:
    """Convenience: ``method_data_dir(base, method) / LP_*.csv``.

    Use this when the caller knows only the *base* folder and wants
    a complete, ready-to-write path.
    """
    return make_lp_csv_path(method_data_dir(base, method), method,
                             when=when)
