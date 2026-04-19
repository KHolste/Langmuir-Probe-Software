"""Tests for the versioned CSV schema banner and the analysis-options
JSON sidecar.

Covers:
* schema header is written at the top of new CSVs,
* ``parse_schema_info`` classifies modern vs legacy,
* legacy CSVs (no schema banner) remain parseable,
* sidecar round-trip (write → read → dict equality on options),
* sidecar detection + malformed-file robustness,
* atomic-write behaviour (no ``.tmp`` left behind),
* LP Double / Single analyze paths drop a sidecar next to the CSV.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dlp_csv_schema import (  # noqa: E402
    SCHEMA_NAME, SCHEMA_VERSION, header_lines, parse_schema_info,
    write_header,
)
from analysis_options_sidecar import (  # noqa: E402
    SIDECAR_SCHEMA_NAME, SIDECAR_SCHEMA_VERSION,
    has_sidecar, read_sidecar, sidecar_path_for_csv, write_sidecar,
)


# ---------------------------------------------------------------------------
# CSV schema banner.
# ---------------------------------------------------------------------------
class TestCsvSchemaBanner:
    def test_header_lines_content(self):
        lines = header_lines()
        assert lines[0].startswith("# Langmuir Probe Measurement Export")
        assert lines[1] == f"# Schema: {SCHEMA_NAME} v{SCHEMA_VERSION}"
        assert lines[2].startswith("# Generator: LangmuirMeasure")

    def test_write_header_into_file(self, tmp_path):
        p = tmp_path / "x.csv"
        with open(p, "w", encoding="utf-8") as fh:
            write_header(fh, generator="LangmuirMeasure test")
        body = p.read_text(encoding="utf-8")
        assert "Schema: lp-measurement-csv v" in body
        assert "Generator: LangmuirMeasure test" in body

    def test_parse_schema_info_modern(self):
        info = parse_schema_info({"Schema": "lp-measurement-csv v1"})
        assert info == {"name": "lp-measurement-csv", "version": 1}

    def test_parse_schema_info_legacy_missing_key(self):
        info = parse_schema_info({"Date": "2026-04-19 12:00:00"})
        assert info == {"name": "legacy", "version": 0}

    def test_parse_schema_info_malformed(self):
        # Unparseable Schema value is degraded to version 0, not an
        # exception — the reader path must never blow up on bad data.
        info = parse_schema_info({"Schema": "something-else"})
        assert info["version"] == 0
        info2 = parse_schema_info({"Schema": "lp-measurement-csv vBAD"})
        assert info2["version"] == 0


class TestWriteCsvEmitsSchema:
    def test_write_csv_prepends_schema_banner(self, tmp_path):
        # Exercises the real write_csv path used by V1 and V2 saves.
        from DoubleLangmuir_measure import write_csv
        p = tmp_path / "sweep.csv"
        write_csv(p, {"Date": "2026-04-19 12:00:00"},
                   [0.0, 1.0], [1e-3, 2e-3], [1e-4, 1e-4],
                   [0.0, 1.0])
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "# Langmuir Probe Measurement Export"
        assert lines[1].startswith("# Schema: lp-measurement-csv v")
        assert lines[2].startswith("# Generator:")
        # Per-run meta still comes after the banner.
        assert any(l.startswith("# Date:") for l in lines)

    def test_parse_csv_dataset_still_works_on_new_schema(self, tmp_path):
        # Schema lines go into the meta dict because they match the
        # "k: v" convention; numeric rows still parse.
        from DoubleLangmuir_measure import (write_csv,
                                              DLPMainWindow)
        p = tmp_path / "sweep.csv"
        write_csv(p, {"Method": "double"},
                   [0.0, 1.0], [1e-3, 2e-3], [1e-4, 1e-4],
                   [0.0, 1.0])
        meta, v_soll, v_ist, i_mean, i_std, _d, _c = \
            DLPMainWindow.parse_csv_dataset(p)
        info = parse_schema_info(meta)
        assert info["name"] == "lp-measurement-csv"
        assert info["version"] == SCHEMA_VERSION
        assert v_soll == [0.0, 1.0]
        assert len(i_mean) == 2

    def test_legacy_csv_without_schema_still_parses(self, tmp_path):
        # Hand-craft a pre-schema CSV (no schema banner, legacy
        # product name) and assert the parser reads it.  Protects
        # the historical archive of measurements.
        p = tmp_path / "legacy.csv"
        p.write_text(
            "# Buehler Double-Langmuir-Probe Export\n"
            "# Date: 2024-01-01\n"
            "#\n"
            "# V_soll_V,V_ist_V,I_mean_A,I_std_A\n"
            "0.0,0.0,1e-3,1e-4\n"
            "1.0,1.0,2e-3,1e-4\n",
            encoding="utf-8",
        )
        from DoubleLangmuir_measure import DLPMainWindow
        meta, v_soll, *_ = DLPMainWindow.parse_csv_dataset(p)
        info = parse_schema_info(meta)
        assert info == {"name": "legacy", "version": 0}
        assert v_soll == [0.0, 1.0]


class TestTripleWriterEmitsSchema:
    def test_triple_dataset_write_csv_has_schema_banner(self, tmp_path):
        from dlp_triple_dataset import TripleDataset, TripleSample
        d = TripleDataset()
        # Populate a valid sample — as_csv_row rejects None for the
        # numeric columns, so ``te_eV``/``ne_m3`` must be real floats.
        d.add(TripleSample(t_s=0.0, u_supply_V=0.0, u_measure_V=0.0,
                            i_measure_A=0.0, v_d12_V=0.0,
                            v_d13_V=0.0, te_eV=2.5, ne_m3=1e16))
        p = d.write_csv(tmp_path / "triple.csv")
        lines = p.read_text(encoding="utf-8").splitlines()
        assert any(l.startswith("# Schema: lp-measurement-csv v")
                   for l in lines)
        assert any(l.startswith("# Langmuir Probe Measurement Export")
                   for l in lines)


# ---------------------------------------------------------------------------
# Sidecar round-trip and robustness.
# ---------------------------------------------------------------------------
class TestSidecarRoundTrip:
    def test_sidecar_path_convention(self, tmp_path):
        csv = tmp_path / "LP_2026-04-19T12-00-00_double.csv"
        sc = sidecar_path_for_csv(csv)
        assert sc.name == "LP_2026-04-19T12-00-00_double.options.json"
        assert sc.parent == csv.parent

    def test_write_then_read_equal_options(self, tmp_path):
        csv = tmp_path / "LP_x_double.csv"
        csv.write_text("dummy\n", encoding="utf-8")
        opts = {"compliance_mode": "exclude_clipped",
                "hysteresis_threshold_pct": 5.0}
        sc = write_sidecar(csv, method="double", options=opts,
                             fit_model="tanh_slope",
                             analysis_summary={"Te_eV": 3.2,
                                               "R2": 0.99,
                                               "fit_status": "ok"})
        assert sc.is_file()
        assert has_sidecar(csv)
        data = read_sidecar(csv)
        assert data is not None
        assert data["schema"] == SIDECAR_SCHEMA_NAME
        assert data["schema_version"] == SIDECAR_SCHEMA_VERSION
        assert data["method"] == "double"
        assert data["options"] == opts
        assert data["fit_model"] == "tanh_slope"
        assert data["analysis"]["Te_eV"] == 3.2

    def test_sidecar_is_atomic_no_tmp_left_behind(self, tmp_path):
        csv = tmp_path / "LP_x_single.csv"
        csv.write_text("dummy\n", encoding="utf-8")
        write_sidecar(csv, method="single",
                       options={"te_window_factor": 3.0})
        # Atomic writer should have renamed its .tmp sibling away.
        assert not (tmp_path /
                     "LP_x_single.options.json.tmp").exists()
        assert (tmp_path / "LP_x_single.options.json").is_file()

    def test_read_missing_sidecar_returns_none(self, tmp_path):
        csv = tmp_path / "nothing.csv"
        csv.write_text("", encoding="utf-8")
        assert read_sidecar(csv) is None
        assert not has_sidecar(csv)

    def test_read_malformed_sidecar_returns_none(self, tmp_path):
        csv = tmp_path / "broken.csv"
        csv.write_text("", encoding="utf-8")
        sc = sidecar_path_for_csv(csv)
        sc.write_text("{not valid json", encoding="utf-8")
        assert read_sidecar(csv) is None

    def test_read_wrong_schema_returns_none(self, tmp_path):
        csv = tmp_path / "wrong.csv"
        csv.write_text("", encoding="utf-8")
        sc = sidecar_path_for_csv(csv)
        sc.write_text(json.dumps({"schema": "some-other-thing"}),
                       encoding="utf-8")
        assert read_sidecar(csv) is None

    def test_sidecar_tolerates_non_json_floats(self, tmp_path):
        csv = tmp_path / "nan.csv"
        csv.write_text("", encoding="utf-8")
        sc = write_sidecar(csv, method="double",
                             options={"x": 1.0},
                             analysis_summary={"Te_eV": float("nan"),
                                                "R2": float("inf")})
        data = read_sidecar(csv)
        # NaN/Inf are stringified so stdlib json stays strict-safe.
        assert data is not None
        assert data["analysis"]["Te_eV"] == "nan"
        assert data["analysis"]["R2"] == "inf"


# ---------------------------------------------------------------------------
# End-to-end: LP analyze paths drop a sidecar next to the CSV.
# ---------------------------------------------------------------------------
class TestLPAnalyzeWritesSidecar:
    """End-to-end through LPMainWindow.  Uses the simulation path so
    no real instrument is touched.  Only asserts sidecar production —
    the analysis numbers themselves are covered by dedicated suites.
    """

    @pytest.fixture
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def _make_window_with_csv(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        # Populate minimal sweep buffers + advertise a last-saved
        # CSV so the analyze path knows where to place the sidecar.
        csv_path = tmp_path / "LP_2026-04-19T12-00-00_double.csv"
        csv_path.write_text("# Langmuir Probe Measurement Export\n"
                              "# Schema: lp-measurement-csv v1\n"
                              "# Date: 2026-04-19 12:00:00\n",
                              encoding="utf-8")
        win._last_csv_path = csv_path
        return win, csv_path

    def test_double_analyze_writes_sidecar(self, qapp, tmp_path):
        import numpy as np
        win, csv_path = self._make_window_with_csv(qapp, tmp_path)
        try:
            # Synthesize a clean DLP I-V curve.
            V = np.linspace(-30.0, 30.0, 61)
            I = 1e-3 * np.tanh(V / 6.0)
            win._v_soll = V.tolist()
            win._v_ist = V.tolist()
            win._i_mean = I.tolist()
            win._i_std = [1e-5] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            # Run Double analyze through the real dispatcher path.
            win._run_analysis()
            sc = sidecar_path_for_csv(csv_path)
            assert sc.is_file(), "Double analyze must drop a sidecar"
            data = read_sidecar(csv_path)
            assert data["method"] == "double"
            assert data["options"]["compliance_mode"] in (
                "exclude_clipped", "include_all")
            assert "analysis" in data
        finally:
            win.close()

    def test_single_analyze_writes_sidecar(self, qapp, tmp_path):
        import numpy as np
        win, csv_path = self._make_window_with_csv(qapp, tmp_path)
        try:
            V = np.linspace(-40.0, 20.0, 61)
            I = 1e-4 * (np.exp(np.clip(V / 3.0, -50, 50)) - 1) - 5e-5
            win._v_soll = V.tolist()
            win._v_ist = V.tolist()
            win._i_mean = I.tolist()
            win._i_std = [1e-6] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            win._run_single_analysis()
            sc = sidecar_path_for_csv(csv_path)
            assert sc.is_file(), "Single analyze must drop a sidecar"
            data = read_sidecar(csv_path)
            assert data["method"] == "single"
            assert "te_window_factor" in data["options"]
        finally:
            win.close()

    def test_analyze_without_csv_does_not_crash(self, qapp, tmp_path):
        # When nothing has been saved yet, the sidecar write should
        # silently skip — the analysis itself must still proceed.
        from LPmeasurement import LPMainWindow
        import numpy as np
        win = LPMainWindow()
        try:
            V = np.linspace(-30.0, 30.0, 61)
            I = 1e-3 * np.tanh(V / 6.0)
            win._v_soll = V.tolist()
            win._v_ist = V.tolist()
            win._i_mean = I.tolist()
            win._i_std = [1e-5] * len(V)
            win._directions = ["fwd"] * len(V)
            win._compliance = [False] * len(V)
            win._last_csv_path = None  # nothing saved yet
            # Must not raise.
            win._run_analysis()
        finally:
            win.close()
