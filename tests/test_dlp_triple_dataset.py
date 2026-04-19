"""Tests for the Triple-Probe data model + CSV writer."""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dlp_triple_dataset import (
    TRIPLE_CSV_COLUMNS,
    TripleDataset,
    TripleSample,
    csv_columns,
    make_triple_csv_path,
    required_field_names,
)


def _make_sample(t=0.0, te=2.5, ne=1e16, **over):
    base = dict(
        t_s=t, u_supply_V=25.0, u_measure_V=2.0, i_measure_A=-1e-3,
        v_d12_V=25.0, v_d13_V=2.0, te_eV=te, ne_m3=ne,
    )
    base.update(over)
    return TripleSample(**base)


# ===========================================================================
# Data model
# ===========================================================================
class TestTripleSample:
    def test_required_fields_present(self):
        names = required_field_names()
        for c in TRIPLE_CSV_COLUMNS:
            assert c in names

    def test_csv_columns_constant_is_stable(self):
        # Order must NOT drift — downstream tools depend on it.
        assert TRIPLE_CSV_COLUMNS == (
            "t_s", "u_supply_V", "u_measure_V", "i_measure_A",
            "v_d12_V", "v_d13_V", "te_eV", "ne_m3",
        )
        assert csv_columns() == TRIPLE_CSV_COLUMNS

    def test_from_worker_dict_full_payload(self):
        payload = {
            "t_rel_s": 0.123, "v_d12_setpoint": 25.0, "v_d12_actual": 25.05,
            "u_meas_v": 2.1, "v_d13": 2.1, "i_a": -1.5e-3,
            "Te_eV": 3.03, "n_e_m3": 1.23e16,
            "species": "Argon (Ar)", "area_m2": 9.7075e-6,
            "mi_kg": 6.6e-26,
        }
        s = TripleSample.from_worker_dict(payload)
        assert s.t_s == pytest.approx(0.123)
        assert s.u_supply_V == pytest.approx(25.0)
        assert s.u_measure_V == pytest.approx(2.1)
        assert s.i_measure_A == pytest.approx(-1.5e-3)
        assert s.v_d12_V == pytest.approx(25.05)
        assert s.v_d13_V == pytest.approx(2.1)
        assert s.te_eV == pytest.approx(3.03)
        assert s.ne_m3 == pytest.approx(1.23e16)
        assert s.species == "Argon (Ar)"
        assert s.area_m2 == pytest.approx(9.7075e-6)
        assert s.mi_kg == pytest.approx(6.6e-26)

    def test_from_worker_dict_partial_payload_safe(self):
        s = TripleSample.from_worker_dict({"t_rel_s": 1.0})
        assert s.t_s == pytest.approx(1.0)
        assert s.u_supply_V == 0.0
        assert s.species is None
        assert s.area_m2 is None

    def test_as_csv_row_uses_stable_format(self):
        s = _make_sample(t=0.5, te=2.5, ne=1e16)
        row = s.as_csv_row()
        parts = row.split(",")
        assert len(parts) == len(TRIPLE_CSV_COLUMNS)
        # current + ne in scientific notation
        assert "e" in parts[3].lower()  # i_measure_A
        assert "e" in parts[7].lower()  # ne_m3


# ===========================================================================
# Dataset
# ===========================================================================
class TestTripleDataset:
    def test_empty(self):
        d = TripleDataset()
        assert len(d) == 0
        assert list(d) == []

    def test_add_and_iterate(self):
        d = TripleDataset()
        for t in (0.0, 0.25, 0.50):
            d.add(_make_sample(t=t))
        assert len(d) == 3
        ts = [s.t_s for s in d]
        assert ts == [0.0, 0.25, 0.50]

    def test_add_rejects_wrong_type(self):
        d = TripleDataset()
        with pytest.raises(TypeError):
            d.add({"t_s": 0.0})  # type: ignore[arg-type]

    def test_add_from_worker_appends_one(self):
        d = TripleDataset()
        s = d.add_from_worker(
            {"t_rel_s": 0.1, "v_d12_setpoint": 25.0, "u_meas_v": 2.0,
             "i_a": -1e-3, "v_d13": 2.0, "v_d12_actual": 25.0,
             "Te_eV": 2.5, "n_e_m3": 1e16})
        assert len(d) == 1
        assert s.t_s == pytest.approx(0.1)

    def test_clear(self):
        d = TripleDataset()
        d.add(_make_sample())
        d.clear()
        assert len(d) == 0


# ===========================================================================
# CSV writer
# ===========================================================================
class TestCsvWriter:
    def _read_lines(self, p):
        return p.read_text(encoding="utf-8").splitlines()

    def test_writes_header_and_rows(self, tmp_path):
        d = TripleDataset()
        for t in (0.0, 0.1, 0.2):
            d.add(_make_sample(t=t))
        p = d.write_csv(tmp_path / "out.csv",
                        meta={"Operator": "JLU"})
        assert p.is_file()
        lines = self._read_lines(p)
        # Header section.  The banner now uses the modern product
        # identity + a versioned schema line; see dlp_csv_schema.
        assert any(l.startswith("# Langmuir Probe Measurement Export")
                   for l in lines)
        assert any(l.startswith("# Schema: lp-measurement-csv v")
                   for l in lines)
        assert any(l.startswith("# Date:") for l in lines)
        assert any(l.startswith("# Samples: 3") for l in lines)
        assert any(l.startswith("# Operator: JLU") for l in lines)
        # Column header
        col_line = next(l for l in lines
                         if l.startswith("# t_s,"))
        assert col_line == "# " + ",".join(TRIPLE_CSV_COLUMNS)
        # Data lines: exactly N
        data_lines = [l for l in lines if not l.startswith("#") and l]
        assert len(data_lines) == 3
        # Each row has the right field count
        for dl in data_lines:
            assert len(dl.split(",")) == len(TRIPLE_CSV_COLUMNS)

    def test_homogeneous_context_promoted_to_header(self, tmp_path):
        d = TripleDataset()
        for t in (0.0, 0.1):
            d.add(_make_sample(t=t,
                               species="Argon (Ar)", area_m2=9.7075e-6,
                               mi_kg=6.6e-26))
        p = d.write_csv(tmp_path / "ctx.csv")
        text = p.read_text(encoding="utf-8")
        assert "# species: Argon (Ar)" in text
        assert "# area_m2: 9.7075e-06" in text or "# area_m2: 9.7075e-6" in text

    def test_empty_dataset_still_writes_file(self, tmp_path):
        p = TripleDataset().write_csv(tmp_path / "empty.csv")
        text = p.read_text(encoding="utf-8")
        assert "# Samples: 0" in text
        assert "# " + ",".join(TRIPLE_CSV_COLUMNS) in text
        # No data rows at all.
        data_lines = [l for l in text.splitlines()
                      if l and not l.startswith("#")]
        assert data_lines == []

    def test_round_trip_numeric_values(self, tmp_path):
        d = TripleDataset()
        d.add(_make_sample(t=0.123, te=2.5, ne=1.234e16,
                            i_measure_A=-1.234e-3))
        p = d.write_csv(tmp_path / "rt.csv")
        data_lines = [l for l in p.read_text(encoding="utf-8").splitlines()
                      if l and not l.startswith("#")]
        assert len(data_lines) == 1
        cols = data_lines[0].split(",")
        # parse back
        parsed = {name: float(val) for name, val
                  in zip(TRIPLE_CSV_COLUMNS, cols)}
        assert parsed["t_s"] == pytest.approx(0.123, rel=1e-5)
        assert parsed["i_measure_A"] == pytest.approx(-1.234e-3, rel=1e-5)
        assert parsed["te_eV"] == pytest.approx(2.5, rel=1e-5)
        assert parsed["ne_m3"] == pytest.approx(1.234e16, rel=1e-5)


# ===========================================================================
# Path helper
# ===========================================================================
class TestPathHelper:
    """Triple's path helper now produces the unified per-method
    layout: ``<base>/triple/LP_<ts>_triple.csv``.  The historic
    ``DLP_TRIPLE_`` prefix is intentionally retired."""

    def test_routes_into_triple_subfolder(self, tmp_path):
        p = make_triple_csv_path(tmp_path)
        assert p.parent == tmp_path / "triple"
        assert p.name.startswith("LP_")
        assert p.name.endswith("_triple.csv")

    def test_prefix_argument_is_ignored_for_unified_naming(self, tmp_path):
        # Custom prefix is silently ignored — naming is now method-
        # suffixed instead.  Kept signature-compatible to avoid
        # breaking ad-hoc callers.
        p = make_triple_csv_path(tmp_path, prefix="EXP42")
        assert "EXP42" not in p.name
        assert p.name.endswith("_triple.csv")


# ===========================================================================
# Integration: worker payload → dataset → CSV
# ===========================================================================
class TestWorkerToCsv:
    def test_round_trip_from_worker_payloads(self, tmp_path):
        worker_payloads = [
            {"t_rel_s": 0.0, "v_d12_setpoint": 25.0, "v_d12_actual": 25.0,
             "u_meas_v": 2.0, "v_d13": 2.0, "i_a": -1e-3,
             "Te_eV": 2.886, "n_e_m3": 1.0e16,
             "species": "Argon (Ar)", "area_m2": 9.7075e-6,
             "mi_kg": 6.633e-26},
            {"t_rel_s": 0.25, "v_d12_setpoint": 25.0, "v_d12_actual": 25.01,
             "u_meas_v": 2.05, "v_d13": 2.05, "i_a": -1.05e-3,
             "Te_eV": 2.96, "n_e_m3": 1.05e16,
             "species": "Argon (Ar)", "area_m2": 9.7075e-6,
             "mi_kg": 6.633e-26},
        ]
        d = TripleDataset()
        for p in worker_payloads:
            d.add_from_worker(p)
        out = d.write_csv(tmp_path / "wt.csv")
        text = out.read_text(encoding="utf-8")
        assert "# Samples: 2" in text
        # Promoted homogeneous context.
        assert "# species: Argon (Ar)" in text
        # Two data rows.
        rows = [l for l in text.splitlines()
                if l and not l.startswith("#")]
        assert len(rows) == 2
