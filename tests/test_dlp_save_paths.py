"""Tests for the unified per-method CSV save-path scheme.

The ``dlp_save_paths`` module is the single source of truth for
where Single, Double, and Triple measurements land on disk:

    <base>/<method>/LP_<timestamp>_<method>.csv

These tests pin:
  * pure helper behaviour (folder routing, naming, normalization,
    collision avoidance);
  * the V1 / Triple legacy entry points now route through the
    new helper;
  * the LP-Hauptfenster's V2-module patch routes Double saves
    into ``<base>/double/`` and Single saves into ``<base>/single/``;
  * the Triple dataset writer lands in ``<base>/triple/``.
"""
from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
class TestNormalizeMethod:
    def test_canonical_values_pass_through(self):
        from dlp_save_paths import normalize_method
        for m in ("single", "double", "triple"):
            assert normalize_method(m) == m

    def test_case_and_whitespace_are_normalised(self):
        from dlp_save_paths import normalize_method
        assert normalize_method("  Single  ") == "single"
        assert normalize_method("DOUBLE") == "double"
        assert normalize_method("Triple") == "triple"

    def test_unknown_falls_back_to_double(self):
        from dlp_save_paths import normalize_method
        assert normalize_method(None) == "double"
        assert normalize_method("") == "double"
        assert normalize_method("quad") == "double"


class TestMethodDataDir:
    def test_creates_subfolder_with_correct_name(self, tmp_path):
        from dlp_save_paths import method_data_dir
        for m in ("single", "double", "triple"):
            d = method_data_dir(tmp_path, m)
            assert d == tmp_path / m
            assert d.is_dir()

    def test_unknown_method_routed_to_double(self, tmp_path):
        from dlp_save_paths import method_data_dir
        d = method_data_dir(tmp_path, "garbage")
        assert d == tmp_path / "double"


class TestMakeLpCsvPath:
    def test_filename_layout_is_method_suffixed(self, tmp_path):
        from dlp_save_paths import make_lp_csv_path
        when = datetime(2026, 4, 19, 10, 30, 45)
        p = make_lp_csv_path(tmp_path, "single", when=when)
        assert p.name == "LP_2026-04-19T10-30-45_single.csv"
        assert p.parent == tmp_path

    def test_collision_appends_numeric_suffix(self, tmp_path):
        from dlp_save_paths import make_lp_csv_path
        when = datetime(2026, 4, 19, 10, 30, 45)
        # Pre-create the would-be file.
        first = tmp_path / "LP_2026-04-19T10-30-45_double.csv"
        first.write_text("dummy", encoding="utf-8")
        p = make_lp_csv_path(tmp_path, "double", when=when)
        assert p.name == "LP_2026-04-19T10-30-45_double_2.csv"
        # And again — should bump to _3.
        p2 = tmp_path / "LP_2026-04-19T10-30-45_double_2.csv"
        p2.write_text("dummy", encoding="utf-8")
        p3 = make_lp_csv_path(tmp_path, "double", when=when)
        assert p3.name == "LP_2026-04-19T10-30-45_double_3.csv"


class TestMakeLpCsvPathForMethod:
    def test_creates_subfolder_and_returns_full_path(self, tmp_path):
        from dlp_save_paths import make_lp_csv_path_for_method
        when = datetime(2026, 4, 19, 10, 30, 45)
        p = make_lp_csv_path_for_method(tmp_path, "triple", when=when)
        assert p.parent == tmp_path / "triple"
        assert p.name.endswith("_triple.csv")
        assert p.parent.is_dir()


# ---------------------------------------------------------------------------
class TestLegacyEntryPointsRoute:
    """V1's ``make_csv_path`` and Triple's ``make_triple_csv_path``
    are now thin wrappers around the unified helper."""

    def test_v1_make_csv_path_lands_in_double_subfolder(self, tmp_path):
        from DoubleLangmuir_measure import make_csv_path
        p = make_csv_path(tmp_path)
        assert p.parent == tmp_path / "double"
        assert p.name.startswith("LP_")
        assert p.name.endswith("_double.csv")

    def test_v1_make_csv_path_respects_explicit_method(self, tmp_path):
        from DoubleLangmuir_measure import make_csv_path
        for m in ("single", "double", "triple"):
            p = make_csv_path(tmp_path, method=m)
            assert p.parent == tmp_path / m
            assert p.name.endswith(f"_{m}.csv")

    def test_triple_helper_lands_in_triple_subfolder(self, tmp_path):
        from dlp_triple_dataset import make_triple_csv_path
        p = make_triple_csv_path(tmp_path)
        assert p.parent == tmp_path / "triple"
        assert p.name.endswith("_triple.csv")

    def test_triple_helper_ignores_legacy_prefix(self, tmp_path):
        from dlp_triple_dataset import make_triple_csv_path
        p = make_triple_csv_path(tmp_path, prefix="HISTORICAL")
        assert "HISTORICAL" not in p.name
        assert p.name.endswith("_triple.csv")


# ---------------------------------------------------------------------------
class TestLpMainWindowSaveRouting:
    """Saving from LPMainWindow under each method must land under
    the matching subfolder, not in the flat base folder."""

    @pytest.fixture(scope="module")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    def _populate_dataset(self, win):
        import numpy as np
        from fake_b2901_v2 import FakeB2901v2
        f = FakeB2901v2(model="single_probe", te_eV=4.0,
                        i_ion_sat=5.5e-6, i_electron_sat=1e-3,
                        v_plasma_V=0.0, sheath_conductance=0.0,
                        electron_sat_slope=0.0,
                        current_compliance=10.0)
        f.connect(); f.output(True)
        for v in np.linspace(-50, 50, 51):
            f.set_voltage(v)
            win._v_soll.append(v); win._v_ist.append(v)
            win._i_mean.append(f.read_current())
            win._i_std.append(0.0)
            win._directions.append("fwd")
            win._compliance.append(False)

    def test_single_saves_into_single_subfolder(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            self._populate_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win._save_csv(run_status="completed")
            files = list(tmp_path.rglob("LP_*.csv"))
            assert len(files) == 1
            assert files[0].parent == tmp_path / "single"
            assert files[0].name.endswith("_single.csv")
            text = files[0].read_text(encoding="utf-8")
            assert "Method: single" in text
        finally:
            win.close()

    def test_double_saves_into_double_subfolder(self, qapp, tmp_path):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            assert win.btnMethodDouble.isChecked()
            win._dataset_method = "double"
            self._populate_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win._save_csv(run_status="completed")
            files = list(tmp_path.rglob("LP_*.csv"))
            assert len(files) == 1
            assert files[0].parent == tmp_path / "double"
            assert files[0].name.endswith("_double.csv")
            text = files[0].read_text(encoding="utf-8")
            assert "Method: double" in text
        finally:
            win.close()

    def test_subfolders_are_distinct(self, qapp, tmp_path):
        # Two saves in succession under different methods land in
        # separate subfolders — never the same folder.
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            self._populate_dataset(win)
            win._save_folder = tmp_path
            win.chkSave.setChecked(True)
            win.chkAutoAnalyze.setChecked(False)
            win.btnMethodSingle.setChecked(True)
            win._dataset_method = "single"
            win._save_csv(run_status="completed")
            win.btnMethodDouble.setChecked(True)
            win._dataset_method = "double"
            win._save_csv(run_status="completed")
            single_files = list((tmp_path / "single").glob("LP_*.csv"))
            double_files = list((tmp_path / "double").glob("LP_*.csv"))
            assert len(single_files) == 1
            assert len(double_files) == 1
            assert single_files[0].name.endswith("_single.csv")
            assert double_files[0].name.endswith("_double.csv")
        finally:
            win.close()


# ---------------------------------------------------------------------------
class TestTripleDatasetSaveRouting:
    """The Triple dataset writer (used by the LP/Triple window for
    both manual save and auto-save) must land in ``<base>/triple/``."""

    def test_dataset_write_csv_uses_triple_path(self, tmp_path):
        from dlp_triple_dataset import (TripleDataset,
                                          make_triple_csv_path)
        ds = TripleDataset()
        p = make_triple_csv_path(tmp_path)
        out = ds.write_csv(p, meta={"Method": "triple"})
        assert out.exists()
        assert out.parent == tmp_path / "triple"
        assert out.name.endswith("_triple.csv")
        text = out.read_text(encoding="utf-8")
        assert "Method: triple" in text

    def test_default_autosave_path_routes_into_triple_subfolder(
            self, qapp, tmp_path, monkeypatch):
        # Force the LP-window's data-dir helper to use tmp_path so
        # we don't pollute the real default folder.  Don't construct
        # the full LPMeasurementWindow (depends on a real SMU and a
        # parent QWidget); just exercise the path helper through the
        # same module entry the window uses.
        import paths as _paths
        monkeypatch.setattr(_paths, "lp_measurements_data_dir",
                            lambda: tmp_path)
        from dlp_triple_dataset import make_triple_csv_path
        p = make_triple_csv_path(_paths.lp_measurements_data_dir())
        assert p.parent == tmp_path / "triple"
        assert p.name.endswith("_triple.csv")

    @pytest.fixture(scope="module")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app
