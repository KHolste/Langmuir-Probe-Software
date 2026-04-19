"""Versioned CSV-header schema for Langmuir measurement exports.

Every measurement CSV the project writes carries a short, stable
schema banner at the top so future readers can tell which metadata
layout they are parsing without relying on the exact spelling of
each comment line.

Layout written by :func:`header_lines`::

    # Langmuir Probe Measurement Export
    # Schema: lp-measurement-csv v1
    # Generator: LangmuirMeasure <version>

The parser in :func:`DoubleLangmuir_measure.parse_csv_dataset` picks
up every ``# key: value`` line into the meta dict, so
``meta["Schema"]`` becomes ``"lp-measurement-csv v1"`` and
``meta["Generator"]`` becomes the app version string.  Legacy CSVs
have no ``Schema`` key at all; :func:`parse_schema_info` reports them
as ``("legacy", 0)`` so downstream code can branch cleanly.
"""
from __future__ import annotations

from typing import Iterable

#: Stable schema identifier.  Changing this string must bump
#: :data:`SCHEMA_VERSION`.  Readers that only understand older
#: versions can keep working by comparing against an upper bound.
SCHEMA_NAME = "lp-measurement-csv"

#: Integer schema version.  Bump when any meta-field rename or
#: per-row column change would break naive readers.
SCHEMA_VERSION = 1

#: Fallback generator string used when the live app cannot import
#: a version constant.  Kept in sync with the Inno Setup AppVersion.
DEFAULT_GENERATOR = "LangmuirMeasure 3.0"


def header_lines(*, generator: str = DEFAULT_GENERATOR
                  ) -> list[str]:
    """Return the three-line CSV banner.

    Intended to be written verbatim at the top of every measurement
    CSV before the per-run ``# key: value`` metadata block.  Lines
    are returned without trailing ``\\n`` so the writer stays in
    control of line endings.
    """
    return [
        "# Langmuir Probe Measurement Export",
        f"# Schema: {SCHEMA_NAME} v{SCHEMA_VERSION}",
        f"# Generator: {generator}",
    ]


def parse_schema_info(meta: dict) -> dict:
    """Interpret the ``Schema`` meta key written by :func:`header_lines`.

    Returns a dict with two keys:

    * ``name`` — schema identifier string (``"lp-measurement-csv"``
      for modern files, ``"legacy"`` for anything older).
    * ``version`` — integer version.  Legacy files report ``0``.

    The helper is intentionally forgiving — a malformed Schema line
    is treated as legacy rather than raising, so a partially-corrupt
    CSV can still be loaded for inspection.
    """
    schema_txt = (meta.get("Schema") or "").strip()
    if not schema_txt:
        return {"name": "legacy", "version": 0}
    parts = schema_txt.split()
    if len(parts) >= 2 and parts[-1].startswith("v"):
        try:
            version = int(parts[-1][1:])
        except ValueError:
            return {"name": parts[0], "version": 0}
        return {"name": parts[0], "version": version}
    return {"name": schema_txt, "version": 0}


def write_header(fh, *, generator: str = DEFAULT_GENERATOR) -> None:
    """Convenience helper: write the schema banner to an open file.

    Keeps the writers' code paths short and identical across both
    the per-sweep (DLP) and per-sample (Triple) CSV writers.
    """
    for line in header_lines(generator=generator):
        fh.write(line + "\n")


__all__ = [
    "SCHEMA_NAME", "SCHEMA_VERSION", "DEFAULT_GENERATOR",
    "header_lines", "parse_schema_info", "write_header",
]
