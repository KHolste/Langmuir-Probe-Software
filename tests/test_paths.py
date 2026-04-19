"""Tests for paths.py — the frozen-safe data path resolver.

Two regimes:
* dev mode (sys.frozen unset) → legacy locations preserved verbatim
  so existing tests + developer state are untouched;
* frozen mode (sys.frozen=True) → everything routes under
  %APPDATA%/JLU-IPI/DLP, with a sane fallback to ~/.dlp when APPDATA
  is missing (CI / non-Windows test runners).
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import paths  # noqa: E402


REPO = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Dev mode (default for this test suite)
# ---------------------------------------------------------------------------
class TestDevMode:
    def test_is_frozen_false_in_dev(self):
        assert paths.is_frozen() is False

    def test_user_data_dir_is_repo_data_in_dev(self):
        d = paths.user_data_dir()
        assert d == REPO / "data"
        assert d.is_dir()

    def test_lp_measurements_data_dir_in_dev(self):
        d = paths.lp_measurements_data_dir()
        assert d == REPO / "data" / "lp_measurements"
        assert d.is_dir()

    def test_double_langmuir_alias_returns_new_path(self):
        # Backward-compat alias: callers that still import the old
        # name automatically benefit from the rename — alias must
        # return the new folder, not the historic one.
        assert (paths.double_langmuir_data_dir()
                == paths.lp_measurements_data_dir())

    def test_legacy_lp_data_dir_points_at_historic_folder(self):
        # legacy_lp_data_dir is for explicit read access / migration
        # discovery — must never be created automatically.
        legacy = paths.legacy_lp_data_dir()
        assert legacy == REPO / "data" / "double_langmuir"
        assert legacy.name == "double_langmuir"

    def test_analysis_history_path_in_dev(self):
        p = paths.analysis_history_path()
        # Legacy location: <repo>/data/analysis_history.txt
        assert p == REPO / "data" / "analysis_history.txt"

    def test_visa_cache_path_in_dev(self):
        p = paths.visa_cache_path()
        # Legacy location: <repo>/visa_cache.json (intentionally NOT
        # under data/ so the developer's existing cache stays valid).
        assert p == REPO / "visa_cache.json"


# ---------------------------------------------------------------------------
# Frozen mode (simulated via monkeypatch)
# ---------------------------------------------------------------------------
class TestFrozenMode:
    @pytest.fixture
    def frozen(self, monkeypatch, tmp_path):
        """Simulate a PyInstaller-frozen build with APPDATA pointing
        into a temp directory."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path))
        return tmp_path

    def test_is_frozen_true_when_simulated(self, frozen):
        assert paths.is_frozen() is True

    def test_user_data_dir_under_appdata(self, frozen):
        d = paths.user_data_dir()
        assert d == frozen / "JLU-IPI" / "DLP"
        assert d.is_dir()

    def test_lp_measurements_data_dir_under_appdata(self, frozen):
        d = paths.lp_measurements_data_dir()
        assert d == frozen / "JLU-IPI" / "DLP" / "lp_measurements"
        assert d.is_dir()

    def test_double_langmuir_alias_under_appdata(self, frozen):
        # Alias still works in frozen mode and routes to the new path.
        assert (paths.double_langmuir_data_dir()
                == frozen / "JLU-IPI" / "DLP" / "lp_measurements")

    def test_analysis_history_path_under_appdata(self, frozen):
        p = paths.analysis_history_path()
        assert p == frozen / "JLU-IPI" / "DLP" / "analysis_history.txt"

    def test_visa_cache_path_under_appdata(self, frozen):
        p = paths.visa_cache_path()
        assert p == frozen / "JLU-IPI" / "DLP" / "visa_cache.json"

    def test_fallback_when_appdata_missing(self, monkeypatch, tmp_path):
        """If APPDATA is unset (non-Windows CI), fall back to ~/.dlp."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        d = paths.user_data_dir()
        # On Windows, Path.home() prefers USERPROFILE; on POSIX, HOME.
        assert d.parent == pathlib.Path.home()
        assert d.name == ".dlp"
        assert d.is_dir()


# ---------------------------------------------------------------------------
# Delegation: legacy helpers return the new resolver's path
# ---------------------------------------------------------------------------
class TestLegacyDelegation:
    def test_analysis_history_default_uses_paths_helper(self):
        from analysis_history import default_history_path
        assert default_history_path() == str(paths.analysis_history_path())

    def test_visa_persistence_default_uses_paths_helper(self):
        from visa_persistence import default_cache_path
        assert default_cache_path() == str(paths.visa_cache_path())

    def test_dlp_default_data_dir_uses_paths_helper(self):
        from DoubleLangmuir_measure_v2 import default_data_dir
        assert default_data_dir() == paths.lp_measurements_data_dir()


# ---------------------------------------------------------------------------
# Migration: legacy double_langmuir/ → new lp_measurements/
# ---------------------------------------------------------------------------
class TestLegacyMigration:
    def _make_legacy_layout(self, base):
        legacy = base / "double_langmuir"
        legacy.mkdir(parents=True, exist_ok=True)
        # A loose CSV in the historic root + a method subfolder
        # (mimicking the prior unified scheme that landed under the
        # old base).
        (legacy / "DLP_2026-01-01T08-00-00.csv").write_text("loose",
                                                              encoding="utf-8")
        sub = legacy / "double"
        sub.mkdir()
        (sub / "LP_2026-01-02T09-00-00_double.csv").write_text("nested",
                                                                 encoding="utf-8")
        return legacy

    def test_no_op_when_legacy_absent(self, tmp_path):
        n = paths.migrate_legacy_lp_data(tmp_path)
        assert n == 0
        # Destination not created unnecessarily.
        assert not (tmp_path / "lp_measurements").exists()

    def test_move_migrates_all_entries(self, tmp_path):
        self._make_legacy_layout(tmp_path)
        n = paths.migrate_legacy_lp_data(tmp_path, copy=False)
        assert n == 2
        new = tmp_path / "lp_measurements"
        assert (new / "DLP_2026-01-01T08-00-00.csv").exists()
        assert (new / "double" / "LP_2026-01-02T09-00-00_double.csv").exists()
        # Legacy folder is now empty (move semantics).
        legacy = tmp_path / "double_langmuir"
        assert legacy.exists()
        assert list(legacy.iterdir()) == []

    def test_copy_leaves_legacy_intact(self, tmp_path):
        self._make_legacy_layout(tmp_path)
        n = paths.migrate_legacy_lp_data(tmp_path, copy=True)
        assert n == 2
        legacy = tmp_path / "double_langmuir"
        assert (legacy / "DLP_2026-01-01T08-00-00.csv").exists()
        assert (legacy / "double" / "LP_2026-01-02T09-00-00_double.csv").exists()
        new = tmp_path / "lp_measurements"
        assert (new / "DLP_2026-01-01T08-00-00.csv").exists()
        assert (new / "double" / "LP_2026-01-02T09-00-00_double.csv").exists()

    def test_idempotent_does_not_overwrite(self, tmp_path):
        self._make_legacy_layout(tmp_path)
        # Pre-populate destination with a colliding file.
        new = tmp_path / "lp_measurements"
        new.mkdir(parents=True, exist_ok=True)
        existing = new / "DLP_2026-01-01T08-00-00.csv"
        existing.write_text("PRE-EXISTING", encoding="utf-8")
        paths.migrate_legacy_lp_data(tmp_path, copy=True)
        # Did not overwrite the colliding file.
        assert existing.read_text(encoding="utf-8") == "PRE-EXISTING"
        # The non-colliding subfolder still got migrated.
        assert (new / "double" / "LP_2026-01-02T09-00-00_double.csv").exists()
