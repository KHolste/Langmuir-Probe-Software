"""Frozen-safe data path resolver for the DLP application.

Centralises every writeable user-data path in one module so the app
behaves predictably regardless of how it was launched:

* **Dev build** (run from the source tree, ``sys.frozen`` not set):
  legacy locations are preserved so existing developer state and
  every passing test keep working unchanged.

* **Frozen build** (PyInstaller-packaged exe): everything goes under
  ``%APPDATA%\\JLU-IPI\\DLP\\``.  This is the only mode where the
  ``__file__``-anchored writes used by the legacy helpers would have
  been a problem (read-only Program Files install).

Each helper is the single source of truth for its respective file or
directory.  Direct ``os.path.dirname(__file__)`` writes elsewhere in
the codebase should migrate here over time.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ORG = "JLU-IPI"
_APP = "DLP"


def is_frozen() -> bool:
    """True when running from a PyInstaller-frozen build."""
    return bool(getattr(sys, "frozen", False))


def _repo_root() -> Path:
    """Source-tree root for dev mode (this file lives in the root)."""
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Writable per-user data root for the DLP app.

    Frozen build → ``%APPDATA%/JLU-IPI/DLP`` (or ``~/.dlp`` if APPDATA
    is unset, e.g. on non-Windows test runners).
    Dev build    → ``<repo>/data`` so existing tests + developer files
                   keep their location.
    """
    if is_frozen():
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata) / _ORG / _APP
        else:
            base = Path.home() / ("." + _APP.lower())
    else:
        base = _repo_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


#: Current base subfolder name for all LP measurements (Single,
#: Double, Triple).  Replaces the historic ``double_langmuir`` name
#: which carried false semantics now that Single and Triple share
#: the same root.  See :func:`lp_measurements_data_dir`.
LP_MEASUREMENTS_FOLDER = "lp_measurements"

#: Historic folder name kept for backward-compat reads / migration
#: only.  Never written to by new code paths.
LEGACY_LP_MEASUREMENTS_FOLDER = "double_langmuir"


def lp_measurements_data_dir() -> Path:
    """Default save folder for *all* LP measurements (single / double /
    triple).  Per-method subfolders live underneath via
    :mod:`dlp_save_paths`.

    Returns ``<user_data_dir>/lp_measurements/`` and creates it if
    necessary.  Old installs with data under ``<base>/double_langmuir/``
    keep that data on disk untouched — see :func:`legacy_lp_data_dir`
    and :func:`migrate_legacy_lp_data` for explicit access / opt-in
    migration."""
    d = user_data_dir() / LP_MEASUREMENTS_FOLDER
    d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_lp_data_dir() -> Path:
    """Historic ``<user_data_dir>/double_langmuir/`` location.

    Returned without ``mkdir`` — callers should check
    :meth:`Path.exists` before reading.  Provided so existing user
    installations can discover their pre-rename data, and so the
    migration helper can find the source files.
    """
    return user_data_dir() / LEGACY_LP_MEASUREMENTS_FOLDER


def double_langmuir_data_dir() -> Path:
    """Deprecated alias of :func:`lp_measurements_data_dir`.

    Kept for forward call sites that still import the old name —
    returns the *new* folder so legacy callers automatically benefit
    from the rename without a code change.  New code should call
    :func:`lp_measurements_data_dir` directly.
    """
    return lp_measurements_data_dir()


def migrate_legacy_lp_data(base: Path | None = None,
                            *, copy: bool = False) -> int:
    """Opt-in helper: move (or copy) historical CSVs from
    ``<base>/double_langmuir/`` into ``<base>/lp_measurements/``.

    Idempotent: existing files in the destination are not
    overwritten and are counted as already-migrated.  Returns the
    number of items (files + non-empty subfolders) processed; 0 if
    no legacy folder is present or it is empty.

    Not called automatically — invoke from a one-shot script or
    expose via a UI menu entry.  ``copy=True`` leaves the legacy
    tree intact (recommended for the first run); the default move
    behaviour empties the legacy tree as items succeed.
    """
    import shutil
    if base is None:
        base = user_data_dir()
    src = Path(base) / LEGACY_LP_MEASUREMENTS_FOLDER
    if not src.exists() or not src.is_dir():
        return 0
    dst = Path(base) / LP_MEASUREMENTS_FOLDER
    dst.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in sorted(src.iterdir()):
        target = dst / entry.name
        if target.exists():
            continue  # idempotent: don't overwrite
        if copy:
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
        else:
            shutil.move(str(entry), str(target))
        moved += 1
    return moved


def analysis_history_path() -> Path:
    """Path of the persistent analysis-history text file.

    Dev mode keeps the legacy ``<repo>/data/analysis_history.txt`` so
    ``tests/test_analysis_history.py`` and existing local history are
    not disturbed.  Frozen mode routes to ``user_data_dir()``.
    """
    if is_frozen():
        return user_data_dir() / "analysis_history.txt"
    return _repo_root() / "data" / "analysis_history.txt"


def visa_cache_path() -> Path:
    """Path of the VISA-scan cache JSON file.

    Dev mode keeps the legacy ``<repo>/visa_cache.json`` so the
    developer's existing scan cache is not orphaned by this migration.
    Frozen mode routes to ``user_data_dir()``.
    """
    if is_frozen():
        return user_data_dir() / "visa_cache.json"
    return _repo_root() / "visa_cache.json"
