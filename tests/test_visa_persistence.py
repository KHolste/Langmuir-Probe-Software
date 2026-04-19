"""Tests for the VISA-resource persistence helper (visa_persistence.py)."""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from visa_persistence import VisaCache, DeviceEntry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def cache_path(tmp_path):
    return str(tmp_path / "visa_cache.json")


# ---------------------------------------------------------------------------
# load / save round-trip
# ---------------------------------------------------------------------------
class TestLoadSave:
    def test_missing_file_gives_empty_cache(self, cache_path):
        assert not os.path.exists(cache_path)
        c = VisaCache(cache_path)
        entry = c.get("b2901")
        assert entry.discovered == []
        assert entry.last_successful == ""
        assert entry.scanned_at == ""

    def test_update_scan_persists_to_disk(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", [
            ("GPIB0::23::INSTR", "Keysight,B2901A,,1.0"),
            ("USB0::1::2::MY::0::INSTR", "Keysight,B2901A,,1.0"),
        ])
        assert os.path.exists(cache_path)

        # reload from disk
        c2 = VisaCache(cache_path)
        got = c2.get("b2901")
        resources = got.resources()
        assert "GPIB0::23::INSTR" in resources
        assert "USB0::1::2::MY::0::INSTR" in resources
        assert got.scanned_at  # timestamp present

    def test_update_scan_replaces_previous_list(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", [("GPIB0::23::INSTR", "")])
        c.update_scan("b2901", [("USB0::1::2::3::0::INSTR", "")])
        c2 = VisaCache(cache_path)
        assert c2.get("b2901").resources() == ["USB0::1::2::3::0::INSTR"]

    def test_accepts_plain_string_entries(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", ["GPIB0::23::INSTR"])
        assert c.get("b2901").resources() == ["GPIB0::23::INSTR"]


# ---------------------------------------------------------------------------
# last_successful behaviour
# ---------------------------------------------------------------------------
class TestLastSuccessful:
    def test_mark_successful_is_persisted(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", [
            ("GPIB0::23::INSTR", ""), ("GPIB0::9::INSTR", ""),
        ])
        c.mark_successful("b2901", "GPIB0::23::INSTR")

        c2 = VisaCache(cache_path)
        assert c2.get("b2901").last_successful == "GPIB0::23::INSTR"

    def test_mark_successful_inserts_missing_entry(self, cache_path):
        """If the successful resource isn't in the scanned list it must
        still show up in combo_items so the GUI can preselect it."""
        c = VisaCache(cache_path)
        c.update_scan("b2901", [("GPIB0::5::INSTR", "")])
        c.mark_successful("b2901", "USB0::1::2::3::0::INSTR")

        items = c.combo_items("b2901")
        resources = [res for _, res in items]
        assert resources[0] == "USB0::1::2::3::0::INSTR"  # preselected first
        assert "GPIB0::5::INSTR" in resources

    def test_combo_items_preserve_order_last_successful_first(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", [
            ("GPIB0::1::INSTR", ""),
            ("GPIB0::2::INSTR", ""),
            ("GPIB0::3::INSTR", ""),
        ])
        c.mark_successful("b2901", "GPIB0::2::INSTR")
        rows = [res for _, res in c.combo_items("b2901")]
        assert rows[0] == "GPIB0::2::INSTR"
        # original entries still present exactly once
        assert set(rows) == {
            "GPIB0::1::INSTR", "GPIB0::2::INSTR", "GPIB0::3::INSTR",
        }
        assert len(rows) == 3

    def test_empty_resource_is_ignored(self, cache_path):
        c = VisaCache(cache_path)
        c.mark_successful("b2901", "")
        assert c.get("b2901").last_successful == ""


# ---------------------------------------------------------------------------
# Robustness on malformed / missing files
# ---------------------------------------------------------------------------
class TestFallback:
    def test_corrupt_json_yields_empty_cache(self, cache_path):
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        c = VisaCache(cache_path)  # must not raise
        assert c.get("b2901").discovered == []

    def test_unexpected_root_type_yields_empty_cache(self, cache_path):
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)
        c = VisaCache(cache_path)
        assert c.get("b2901").discovered == []

    def test_garbage_device_entries_are_skipped(self, cache_path):
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "devices": {
                "b2901": "not-a-dict",
                "fug":   {"discovered": "wrong-type",
                           "last_successful": None},
            }}, fh)
        c = VisaCache(cache_path)
        # "b2901" entry is string → skipped, so a fresh empty entry is
        # returned.  "fug" entry has bad discovered → empty list.
        assert c.get("b2901").discovered == []
        assert c.get("fug").discovered == []
        assert c.get("fug").last_successful == ""

    def test_multiple_devices_are_isolated(self, cache_path):
        c = VisaCache(cache_path)
        c.update_scan("b2901", [("GPIB0::23::INSTR", "")])
        c.update_scan("fug",   [("GPIB0::9::INSTR", "")])
        c.mark_successful("b2901", "GPIB0::23::INSTR")

        c2 = VisaCache(cache_path)
        assert c2.get("b2901").last_successful == "GPIB0::23::INSTR"
        assert c2.get("fug").last_successful == ""
        assert c2.get("b2901").resources() == ["GPIB0::23::INSTR"]
        assert c2.get("fug").resources() == ["GPIB0::9::INSTR"]


# ---------------------------------------------------------------------------
# combo_items – UI-facing helper
# ---------------------------------------------------------------------------
class TestComboItems:
    def test_returns_bare_resource_label(self, cache_path):
        """After the UX-refinement pass the visible label is the bare
        resource address only — no IDN suffix.  The IDN is exposed
        separately via :meth:`combo_items_with_idn` so the UI layer
        can render it as a per-item tooltip."""
        c = VisaCache(cache_path)
        c.update_scan("b2901", [("GPIB0::23::INSTR", "Keysight,B2901A")])
        items = c.combo_items("b2901")
        # Resource is in BOTH slots: label == resource (bare).
        assert items[0][1] == "GPIB0::23::INSTR"
        assert items[0][0] == "GPIB0::23::INSTR"
        assert "Keysight" not in items[0][0]
        # IDN remains accessible via the explicit triple form.
        triples = c.combo_items_with_idn("b2901")
        assert triples[0][2] == "Keysight,B2901A"

    def test_empty_cache_returns_empty_list(self, cache_path):
        c = VisaCache(cache_path)
        assert c.combo_items("b2901") == []
