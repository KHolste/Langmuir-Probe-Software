"""
Persistent analysis history for the Double-Langmuir-Probe pipeline.

Each analysis run appends **one** block to a single, human-readable text
file.  A block looks like::

    === 2026-04-17T14:32:15 ===
    T_e  = 2.34 +- 0.12 eV
    I_sat = 1.23e-03 A
    ...
    <blank line>

Always the same file is used so the user sees a chronological log
instead of a growing pile of individual analysis files.

Default location is ``data/analysis_history.txt`` alongside the existing
measurement data directory.  The helper is independent of Qt so it is
trivially testable.

Record parsing returns the entries in **reverse chronological order**
(newest first) because that matches the analysis-log window.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

DEFAULT_DIRNAME = "data"
DEFAULT_FILENAME = "analysis_history.txt"
HEADER_PREFIX = "=== "
HEADER_SUFFIX = " ==="


def default_history_path() -> str:
    """Default location of the analysis history file.

    Dev mode → ``<repo>/data/analysis_history.txt``.
    Frozen build → ``%APPDATA%/JLU-IPI/DLP/analysis_history.txt``.

    Resolution is delegated to :mod:`paths` so the rule lives in one
    place and the frozen-build fallback works without per-call
    ``__file__`` gymnastics.
    """
    from paths import analysis_history_path
    return str(analysis_history_path())


def _iso_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass
class AnalysisRecord:
    """One persisted analysis entry – timestamp + free-form body text."""
    timestamp: str
    body: str

    def to_text(self) -> str:
        return (f"{HEADER_PREFIX}{self.timestamp}{HEADER_SUFFIX}\n"
                f"{self.body.rstrip()}\n\n")


def append_record(body: str,
                  *,
                  path: str | None = None,
                  timestamp: str | None = None) -> AnalysisRecord:
    """Append *body* to the history file and return the stored record.

    The file (and its parent directory) are created on demand.  Failures
    are logged, not raised, so an I/O hiccup never takes down a running
    measurement.
    """
    rec = AnalysisRecord(
        timestamp=timestamp or _iso_now(),
        body=body.rstrip(),
    )
    target = path or default_history_path()
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(rec.to_text())
    except OSError as exc:
        log.warning("Analysis history write failed (%s): %s", target, exc)
    return rec


def load_records(path: str | None = None) -> list[AnalysisRecord]:
    """Return all stored records **newest-first**.

    Missing or malformed files yield an empty list – the caller can then
    show an empty analysis window without special-casing.
    """
    target = path or default_history_path()
    if not os.path.isfile(target):
        return []
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        log.warning("Analysis history read failed (%s): %s", target, exc)
        return []

    records: list[AnalysisRecord] = []
    current_ts: str | None = None
    current_body: list[str] = []
    for line in raw.splitlines():
        if line.startswith(HEADER_PREFIX) and line.endswith(HEADER_SUFFIX):
            if current_ts is not None:
                records.append(AnalysisRecord(
                    timestamp=current_ts,
                    body="\n".join(current_body).rstrip(),
                ))
            current_ts = line[len(HEADER_PREFIX):-len(HEADER_SUFFIX)].strip()
            current_body = []
        elif current_ts is not None:
            current_body.append(line)
    if current_ts is not None:
        records.append(AnalysisRecord(
            timestamp=current_ts,
            body="\n".join(current_body).rstrip(),
        ))
    records.reverse()
    return records
