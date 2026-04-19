"""Triple-Probe data model + CSV writer (Phase 3).

Sits between the Phase-2 worker (which emits one ``sample`` dict per
tick) and the future Triple-Probe window (which will display + save
a time-series of those samples).

* :class:`TripleSample` — flat dataclass with exactly the eight
  scientifically load-bearing fields plus a few metadata slots.
* :class:`TripleDataset` — append-only collection with iteration,
  ``__len__``, ``clear`` and ``write_csv``.  No GUI dependency.
* :func:`make_triple_csv_path` — timestamped path helper, mirrors
  the project convention used by ``DoubleLangmuir_measure.write_csv``.

CSV format mirrors the project style: ``# key: value`` metadata
header lines, one ``# col1,col2,…`` header line, then plain CSV rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


__all__ = [
    "TripleSample", "TripleDataset", "make_triple_csv_path",
    "TRIPLE_CSV_COLUMNS",
]


#: Stable column order for the per-row CSV section.  Kept as a module
#: constant so downstream tooling (and tests) can rely on it.
TRIPLE_CSV_COLUMNS: tuple[str, ...] = (
    "t_s",
    "u_supply_V",
    "u_measure_V",
    "i_measure_A",
    "v_d12_V",
    "v_d13_V",
    "te_eV",
    "ne_m3",
)


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------
@dataclass
class TripleSample:
    """One Triple-Langmuir-Probe sample.

    The eight first fields are the scientific payload; the remaining
    fields are optional context that is normally written to the CSV
    header (per-file metadata) rather than per-row.
    """

    t_s: float
    u_supply_V: float
    u_measure_V: float
    i_measure_A: float
    v_d12_V: float
    v_d13_V: float
    te_eV: float
    ne_m3: float

    # context (not part of TRIPLE_CSV_COLUMNS by design)
    species: Optional[str] = None
    area_m2: Optional[float] = None
    mi_kg: Optional[float] = None

    @classmethod
    def from_worker_dict(cls, d: dict) -> "TripleSample":
        """Build a sample from the Phase-2 worker payload.

        Expected keys (see ``dlp_triple_worker.TripleProbeWorker``):
        ``t_rel_s, v_d12_setpoint, v_d12_actual, u_meas_v, v_d13,
        i_a, Te_eV, n_e_m3, species, area_m2, mi_kg``.
        Missing keys default to ``0.0`` for numeric fields and
        ``None`` for context fields, so partial dicts (e.g. early
        ticks) still round-trip cleanly.
        """
        def f(key: str) -> float:
            v = d.get(key, 0.0)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float("nan")

        return cls(
            t_s=f("t_rel_s"),
            u_supply_V=f("v_d12_setpoint"),
            u_measure_V=f("u_meas_v"),
            i_measure_A=f("i_a"),
            v_d12_V=f("v_d12_actual"),
            v_d13_V=f("v_d13"),
            te_eV=f("Te_eV"),
            ne_m3=f("n_e_m3"),
            species=d.get("species"),
            area_m2=(float(d["area_m2"]) if d.get("area_m2") is not None
                     else None),
            mi_kg=(float(d["mi_kg"]) if d.get("mi_kg") is not None
                   else None),
        )

    def as_csv_row(self) -> str:
        """Return the per-row CSV string in TRIPLE_CSV_COLUMNS order.

        Voltages/times use ``%.6g`` (compact + readable), currents and
        densities use ``%.6e`` (exponent-friendly), Te in ``%.6g``.
        """
        d = asdict(self)
        parts = []
        for col in TRIPLE_CSV_COLUMNS:
            v = d[col]
            if col in ("i_measure_A", "ne_m3"):
                parts.append(f"{float(v):.6e}")
            else:
                parts.append(f"{float(v):.6g}")
        return ",".join(parts)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
@dataclass
class TripleDataset:
    """Append-only collection of :class:`TripleSample`.

    Designed so the future Triple-Probe window can:
    * call ``add(sample)`` from the worker's ``sample`` signal,
    * read ``len(dataset)`` for progress / status,
    * iterate to feed a live Te/n_e plot,
    * call ``write_csv(path, meta=…)`` on demand or at stop.
    """

    samples: list[TripleSample] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Collection API
    # ------------------------------------------------------------------
    def add(self, sample: TripleSample) -> None:
        if not isinstance(sample, TripleSample):
            raise TypeError(
                f"expected TripleSample, got {type(sample).__name__}")
        self.samples.append(sample)

    def add_from_worker(self, payload: dict) -> TripleSample:
        """Convenience: wrap a worker payload and append in one call."""
        s = TripleSample.from_worker_dict(payload)
        self.add(s)
        return s

    def clear(self) -> None:
        self.samples.clear()

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self) -> Iterator[TripleSample]:
        return iter(self.samples)

    # ------------------------------------------------------------------
    # CSV output
    # ------------------------------------------------------------------
    def write_csv(self, path: Path | str,
                  meta: Optional[dict[str, Any]] = None) -> Path:
        """Write the dataset to ``path`` in the project CSV style.

        Returns the resolved Path so tests / callers can verify the
        location written to.  Empty datasets still produce a file
        with header + meta — no rows — so post-mortem inspection is
        possible after an aborted run.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            # Versioned schema banner — shared with the Double writer
            # so a third-party parser only needs to understand one
            # header layout across all three measurement methods.
            from dlp_csv_schema import write_header
            write_header(fh)
            fh.write(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            fh.write(f"# Samples: {len(self.samples)}\n")
            if meta:
                for k, v in meta.items():
                    fh.write(f"# {k}: {v}\n")
            # If the dataset has homogeneous context, expose it in the
            # header so the per-row section stays flat.
            for ctx in ("species", "area_m2", "mi_kg"):
                vals = {getattr(s, ctx) for s in self.samples
                        if getattr(s, ctx) is not None}
                if len(vals) == 1:
                    fh.write(f"# {ctx}: {next(iter(vals))}\n")
            fh.write("#\n")
            fh.write("# " + ",".join(TRIPLE_CSV_COLUMNS) + "\n")
            for s in self.samples:
                fh.write(s.as_csv_row() + "\n")
        return p


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------
def make_triple_csv_path(folder: Path | str,
                         prefix: str = "DLP_TRIPLE") -> Path:
    """Timestamped CSV path under the unified per-method layout.

    Returns ``<folder>/triple/LP_<timestamp>_triple.csv``.  The
    ``prefix`` argument is kept for backward signature compatibility
    but is ignored — the new naming places the method in a folder
    and as a filename suffix.  See :mod:`dlp_save_paths`.
    """
    from dlp_save_paths import make_lp_csv_path_for_method
    return make_lp_csv_path_for_method(folder, "triple")


# ---------------------------------------------------------------------------
# Back-compat introspection helper
# ---------------------------------------------------------------------------
def csv_columns() -> tuple[str, ...]:
    """Return ``TRIPLE_CSV_COLUMNS`` (kept as a function so downstream
    code can use it without importing the constant separately)."""
    return TRIPLE_CSV_COLUMNS


def required_field_names() -> tuple[str, ...]:
    """Return the required (non-context) TripleSample field names."""
    optional = {"species", "area_m2", "mi_kg", "samples"}
    return tuple(f.name for f in fields(TripleSample)
                 if f.name not in optional)
