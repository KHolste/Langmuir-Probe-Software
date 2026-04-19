"""
Persistence helper for VISA resource discovery.

The Keysight B2901 SMU is connected via a GPIB-USB adapter.  Scanning
VISA resources is slow enough (seconds per re-open in NI-VISA) that we
cache the last scan on disk and preselect the last-successfully-used
resource at program start.

Storage format: a small JSON file (``visa_cache.json``) located next to
``Buehler.ini`` in the project root.  Independent of :class:`AppConfig`
so DLP-Standalone (no INI) and the RPA main GUI (INI-backed) can share
the same cache without coupling.

Structure::

    {
      "version": 1,
      "devices": {
        "b2901": {
          "discovered": [
            {"resource": "GPIB0::23::INSTR", "idn": "Agilent,B2901A,…"}
          ],
          "last_successful": "GPIB0::23::INSTR",
          "scanned_at": "2026-04-17T08:15:00"
        }
      }
    }

Multiple device keys (``"b2901"``, ``"fug_rpa"`` …) are supported so the
same helper can serve more than one hardware target.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Iterable

log = logging.getLogger(__name__)

CACHE_VERSION = 1
DEFAULT_FILENAME = "visa_cache.json"


def default_cache_path() -> str:
    """Return the default cache path.

    Dev mode preserves the legacy ``<repo>/visa_cache.json`` location
    so the developer's existing cache is not orphaned.  Frozen build
    routes to ``%APPDATA%/JLU-IPI/DLP/visa_cache.json`` via
    :mod:`paths`, avoiding the read-only Program Files trap.
    """
    from paths import visa_cache_path
    return str(visa_cache_path())


@dataclass
class DeviceEntry:
    discovered: list[dict] = field(default_factory=list)
    last_successful: str = ""
    scanned_at: str = ""

    def resources(self) -> list[str]:
        """Return just the resource strings from the discovered list."""
        return [d.get("resource", "") for d in self.discovered
                if d.get("resource")]


class VisaCache:
    """In-memory view of the persisted VISA cache, keyed by device id."""

    def __init__(self, path: str | None = None):
        self.path = path or default_cache_path()
        self._devices: dict[str, DeviceEntry] = {}
        self.load()

    # ---- file I/O ----------------------------------------------------

    def load(self) -> None:
        """Load the cache from disk.  Missing/corrupt file → empty cache."""
        self._devices = {}
        if not os.path.isfile(self.path):
            log.debug("VISA cache not found (%s) – starting empty.", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("VISA cache unreadable (%s): %s – using empty fallback.",
                        self.path, exc)
            return
        if not isinstance(data, dict):
            log.warning("VISA cache has unexpected root type – using empty fallback.")
            return
        devices = data.get("devices", {})
        if not isinstance(devices, dict):
            return
        for key, raw in devices.items():
            if not isinstance(raw, dict):
                continue
            discovered_raw = raw.get("discovered", [])
            discovered: list[dict] = []
            if isinstance(discovered_raw, list):
                for item in discovered_raw:
                    if isinstance(item, dict) and item.get("resource"):
                        discovered.append({
                            "resource": str(item["resource"]),
                            "idn": str(item.get("idn", "")),
                        })
                    elif isinstance(item, str) and item:
                        discovered.append({"resource": item, "idn": ""})
            self._devices[str(key)] = DeviceEntry(
                discovered=discovered,
                last_successful=str(raw.get("last_successful", "") or ""),
                scanned_at=str(raw.get("scanned_at", "") or ""),
            )

    def save(self) -> None:
        """Persist the current cache to disk (best-effort)."""
        payload = {
            "version": CACHE_VERSION,
            "devices": {k: asdict(v) for k, v in self._devices.items()},
        }
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("VISA cache write failed (%s): %s", self.path, exc)

    # ---- per-device accessors ---------------------------------------

    def get(self, device_key: str) -> DeviceEntry:
        """Return the entry for *device_key*, creating it if absent."""
        return self._devices.setdefault(device_key, DeviceEntry())

    def update_scan(self, device_key: str,
                    discovered: Iterable[tuple[str, str] | str]) -> None:
        """Replace the discovered list for *device_key* and timestamp it."""
        items: list[dict] = []
        for d in discovered:
            if isinstance(d, tuple):
                res, idn = d[0], (d[1] if len(d) > 1 else "")
            else:
                res, idn = str(d), ""
            if res:
                items.append({"resource": str(res), "idn": str(idn)})
        entry = self.get(device_key)
        entry.discovered = items
        entry.scanned_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def mark_successful(self, device_key: str, resource: str) -> None:
        """Record *resource* as the most recent working connection."""
        if not resource:
            return
        entry = self.get(device_key)
        entry.last_successful = resource
        # Ensure the resource appears in the discovered list (handles manual
        # entries that were never scanned).
        if not any(d.get("resource") == resource for d in entry.discovered):
            entry.discovered.append({"resource": resource, "idn": ""})
        self.save()

    # ---- helpers for UI ---------------------------------------------

    def combo_items(self, device_key: str) -> list[tuple[str, str]]:
        """Return ``(display_text, resource)`` pairs for ComboBox
        population.

        Display text is the bare VISA resource address only — for
        example ``"GPIB0::25::INSTR"`` — to keep the left control
        column compact and the combo legible.  The full instrument
        IDN string is **not** embedded in the visible text; it is
        already surfaced elsewhere (the IDN label below the combo
        and the connect-success log line).  Callers that want a
        per-item IDN tooltip can use :meth:`combo_items_with_idn`.

        The last-successful entry is placed first so a default
        selection of index 0 is the expected resource.  If the
        last-successful value is not in the discovered list, it is
        still prepended.
        """
        return [(res, res) for _label, res, _idn
                in self.combo_items_with_idn(device_key)]

    def combo_items_with_idn(self, device_key: str
                              ) -> list[tuple[str, str, str]]:
        """Return ``(display_text, resource, idn)`` triples.

        Same ordering as :meth:`combo_items`; the IDN is exposed
        separately so the UI layer can attach it as a per-item
        tooltip without polluting the visible combo text.
        """
        entry = self.get(device_key)
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        def add(res: str, idn: str = "") -> None:
            if not res or res in seen:
                return
            seen.add(res)
            rows.append((res, res, idn or ""))

        if entry.last_successful:
            idn = ""
            for d in entry.discovered:
                if d.get("resource") == entry.last_successful:
                    idn = d.get("idn", "")
                    break
            add(entry.last_successful, idn)
        for d in entry.discovered:
            add(d.get("resource", ""), d.get("idn", ""))
        return rows
