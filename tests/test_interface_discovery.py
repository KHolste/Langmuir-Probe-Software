"""Tests for the interface-discovery feature.

Covers:

* README.md exists and carries the core sections a new reader expects.
* ``discover_resources`` merges VISA + pyserial results deterministically.
* ``probe_resource`` returns a structured success/failure envelope and
  never raises.
* Main window gains a Tools menu entry and opens the discovery window.
* Applying a discovered resource to SMU / K2000 updates the UI AND
  does NOT overwrite the cache's last-successful pointer (that stays
  bound to an actual Connect success).
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
    app.sendPostedEvents(None, 0)


# ---------------------------------------------------------------------------
# Part 1 — README.md
# ---------------------------------------------------------------------------
class TestReadme:
    def test_readme_exists_at_repo_root(self):
        readme = pathlib.Path(__file__).resolve().parent.parent \
                  / "README.md"
        assert readme.is_file(), "README.md must exist at repo root"

    def test_readme_covers_core_sections(self):
        readme = pathlib.Path(__file__).resolve().parent.parent \
                  / "README.md"
        text = readme.read_text(encoding="utf-8")
        # Purpose + identity.
        assert "Langmuir" in text
        # Measurement modes.
        for mode in ("Single", "Double", "Triple", "Cleaning"):
            assert mode in text, f"README must mention {mode}"
        # Hardware context.
        for hw in ("B2901", "Keithley 2000", "GPIB"):
            assert hw in text, f"README must mention {hw}"
        # Build / run / test quickstart + prerequisites.
        for kw in ("pip install", "pytest", "build.bat",
                   "VISA", "INSTALL_prereqs.md"):
            assert kw in text, f"README should mention {kw}"
        # Main entry point.
        assert "LPmeasurement.py" in text


# ---------------------------------------------------------------------------
# Part 2 — pure discovery API
# ---------------------------------------------------------------------------
class TestDiscoveryAPI:
    def test_discover_returns_list_of_resources(self, monkeypatch):
        import interface_discovery as m
        monkeypatch.setattr(m, "_visa_resources",
                             lambda: ["GPIB0::23::INSTR",
                                      "TCPIP0::10.0.0.5::INSTR",
                                      "ASRL4::INSTR"])
        monkeypatch.setattr(m, "_serial_ports",
                             lambda: [("COM4", "USB Serial Port",
                                        "USB VID:PID=0403:6001"),
                                       ("COM7", "Prolific USB-Serial",
                                        "USB VID:PID=067B:2303")])
        rows = m.discover_resources()
        by_res = {r.resource: r for r in rows}
        # COM4 appears as ASRL4::INSTR in both lists → merged
        assert "ASRL4::INSTR" in by_res
        assert by_res["ASRL4::INSTR"].source == "merged"
        assert by_res["ASRL4::INSTR"].description  # pyserial friendly name
        # COM7 is serial-only (VISA does not know it)
        assert "COM7" in by_res
        assert by_res["COM7"].source == "serial"
        # GPIB + TCP stay as VISA-only rows
        assert by_res["GPIB0::23::INSTR"].transport == "GPIB"
        assert by_res["TCPIP0::10.0.0.5::INSTR"].transport \
               == "TCP/IP (VISA)"

    def test_discover_handles_empty_sources(self, monkeypatch):
        import interface_discovery as m
        monkeypatch.setattr(m, "_visa_resources", lambda: [])
        monkeypatch.setattr(m, "_serial_ports", lambda: [])
        assert m.discover_resources() == []

    def test_classify_visa_resource(self):
        from interface_discovery import classify_visa_resource
        assert classify_visa_resource("GPIB0::9::INSTR") == "GPIB"
        assert classify_visa_resource("ASRL1::INSTR") == \
               "Serial (VISA ASRL)"
        assert classify_visa_resource("USB0::0x2A8D::1::INSTR") == \
               "USB (VISA)"
        assert classify_visa_resource("TCPIP0::host::INSTR") == \
               "TCP/IP (VISA)"
        assert classify_visa_resource("") == "VISA"


# ---------------------------------------------------------------------------
# Part 3 — probe envelope
# ---------------------------------------------------------------------------
class TestProbeResult:
    def test_probe_success_returns_idn(self, monkeypatch):
        import interface_discovery as m

        class _Inst:
            timeout = 0
            def __setattr__(self, k, v):
                object.__setattr__(self, k, v)
            def query(self, _cmd):
                return "Keysight,B2901A,FAKE,1.0\n"
            def close(self):
                pass

        class _RM:
            def open_resource(self, target):
                return _Inst()
            def close(self):
                pass

        class _PyVisa:
            @staticmethod
            def ResourceManager():
                return _RM()

        # monkeypatch import of pyvisa inside probe_resource via a
        # real sys.modules swap so the existing `import pyvisa` works.
        monkeypatch.setitem(sys.modules, "pyvisa", _PyVisa)
        r = m.probe_resource("GPIB0::23::INSTR", timeout_ms=100)
        assert r.ok is True
        assert "B2901A" in r.idn
        assert r.error_kind == ""

    def test_probe_failure_is_classified(self, monkeypatch):
        import interface_discovery as m
        from pyvisa.errors import VisaIOError
        from pyvisa import constants as _vc

        class _RM:
            def open_resource(self, target):
                raise VisaIOError(_vc.VI_ERROR_RSRC_NFOUND)
            def close(self):
                pass

        class _PyVisa:
            @staticmethod
            def ResourceManager():
                return _RM()

        monkeypatch.setitem(sys.modules, "pyvisa", _PyVisa)
        r = m.probe_resource("GPIB0::99::INSTR", timeout_ms=100)
        assert r.ok is False
        assert r.error_kind == "no_device"
        assert r.error_message  # carries type + message
        assert r.remediation    # operator-facing hint

    def test_probe_never_raises_on_unexpected_exc(self, monkeypatch):
        import interface_discovery as m

        class _PyVisa:
            @staticmethod
            def ResourceManager():
                raise RuntimeError("something weird")

        monkeypatch.setitem(sys.modules, "pyvisa", _PyVisa)
        r = m.probe_resource("GPIB0::1::INSTR", timeout_ms=100)
        assert r.ok is False
        assert "RuntimeError" in r.error_message


# ---------------------------------------------------------------------------
# Part 4 — main window menu + discovery window
# ---------------------------------------------------------------------------
class TestMainWindowMenuWiring:
    def test_tools_menu_has_interface_discovery_action(self, qapp):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            actions = {a.text().replace("&", "")
                        for a in win.menuBar().actions()}
            assert "Tools" in actions
            # Find the submenu.
            tools_menu = None
            for a in win.menuBar().actions():
                if a.text().replace("&", "") == "Tools":
                    tools_menu = a.menu()
                    break
            assert tools_menu is not None
            sub_texts = {a.text() for a in tools_menu.actions()}
            assert any("Interface Discovery" in t for t in sub_texts), \
                sub_texts
        finally:
            win.close()

    def test_open_discovery_window_sets_singleton(self, qapp,
                                                     monkeypatch):
        import interface_discovery as mod
        calls = {"opened": 0}

        real_open = mod.open_interface_discovery
        def _fake(parent=None, *, on_apply_smu=None,
                    on_apply_k2000=None):
            calls["opened"] += 1
            return real_open(parent=parent,
                               on_apply_smu=on_apply_smu,
                               on_apply_k2000=on_apply_k2000)

        monkeypatch.setattr(mod, "open_interface_discovery", _fake)
        # Keep the scan output deterministic and cheap.
        monkeypatch.setattr(mod, "_visa_resources", lambda: [])
        monkeypatch.setattr(mod, "_serial_ports", lambda: [])

        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._open_interface_discovery()
            assert calls["opened"] == 1
            assert getattr(win, "_discovery_window", None) is not None
            # A second click reuses the existing window — one open.
            win._open_interface_discovery()
            assert calls["opened"] == 1
        finally:
            # Drop every reference the test holds before the main
            # window closes — Qt's offscreen platform is extra
            # sensitive to deletion order across tests when cell
            # widgets are in play.
            try:
                win._discovery_window.close()
            except Exception:
                pass
            win._discovery_window = None
            try:
                win.close()
            except Exception:
                pass
            win.deleteLater()


class TestDiscoveryWindowBehaviour:
    def test_refresh_populates_rows(self, qapp, monkeypatch):
        import interface_discovery as m
        monkeypatch.setattr(m, "_visa_resources",
                             lambda: ["GPIB0::23::INSTR"])
        monkeypatch.setattr(m, "_serial_ports",
                             lambda: [("COM4", "USB Serial", "")])
        from interface_discovery import InterfaceDiscoveryWindow
        w = InterfaceDiscoveryWindow()
        try:
            rows = w.resources()
            assert len(rows) == 2
            # The table has the expected column count.
            assert w.table.columnCount() == len(w.COLUMN_HEADERS)
            assert w.table.rowCount() == 2
        finally:
            w.close()

    def test_probe_row_updates_result_cell(self, qapp, monkeypatch):
        import interface_discovery as m

        class _Inst:
            timeout = 0
            def __setattr__(self, k, v):
                object.__setattr__(self, k, v)
            def query(self, _cmd):
                return "FAKE-IDN-42\n"
            def close(self):
                pass

        class _RM:
            def open_resource(self, _t): return _Inst()
            def close(self): pass

        class _PyVisa:
            @staticmethod
            def ResourceManager(): return _RM()

        monkeypatch.setattr(m, "_visa_resources",
                             lambda: ["GPIB0::7::INSTR"])
        monkeypatch.setattr(m, "_serial_ports", lambda: [])
        monkeypatch.setitem(sys.modules, "pyvisa", _PyVisa)

        from interface_discovery import InterfaceDiscoveryWindow
        w = InterfaceDiscoveryWindow()
        try:
            w._probe_row(0)
            result_item = w.table.item(0, w.COL_RESULT)
            assert result_item is not None
            assert "FAKE-IDN-42" in result_item.text()
        finally:
            w.close()


# ---------------------------------------------------------------------------
# Part 5 — applying discovered resources back into the main GUI
# ---------------------------------------------------------------------------
class TestApplyHooks:
    @pytest.fixture
    def isolated_cache(self, tmp_path, monkeypatch):
        """Every test in this class gets a fresh, on-disk, disposable
        visa_cache so the shared repo-root cache file is never
        polluted — that file is what a dev actually sees when running
        the GUI, and test runs should NOT rewrite it.
        """
        import visa_persistence as vp
        cache_path = tmp_path / "visa_cache.json"
        monkeypatch.setattr(vp, "default_cache_path",
                             lambda: str(cache_path))
        yield cache_path

    def test_apply_smu_populates_combo_without_overwriting_last(
            self, qapp, isolated_cache):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            # Pre-seed the cache with a "working" resource and make
            # sure Use-for-SMU does NOT change it.
            cache = win._visa_cache
            key = win._visa_device_key
            cache.mark_successful(key, "GPIB0::23::INSTR")
            before = cache.get(key).last_successful
            win._apply_discovered_smu_resource("GPIB0::77::INSTR")
            after = cache.get(key).last_successful
            assert before == after == "GPIB0::23::INSTR"
            # But the combo now contains the new candidate.
            resources_in_combo = {win.cmbVisa.itemData(i)
                                    for i in range(win.cmbVisa.count())}
            assert "GPIB0::77::INSTR" in resources_in_combo
            # And the discovered list inside the cache gained the new
            # resource as a candidate for later selection.
            cached_resources = {
                d.get("resource")
                for d in cache.get(key).discovered
            }
            assert "GPIB0::77::INSTR" in cached_resources
        finally:
            win.close()

    def test_apply_k2000_gpib_populates_visa_field(self, qapp,
                                                      isolated_cache):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._apply_discovered_k2000_resource("GPIB0::9::INSTR")
            assert win.editK2000Visa.text() == "GPIB0::9::INSTR"
            assert win.cmbK2000Transport.currentText().upper() == "GPIB"
        finally:
            win.close()

    def test_apply_k2000_serial_switches_transport(self, qapp,
                                                      isolated_cache):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._apply_discovered_k2000_resource("COM4")
            assert win.cmbK2000Transport.currentText().upper() == "RS232"
            assert win.editK2000Port.text().upper() == "COM4"
        finally:
            win.close()

    def test_apply_k2000_asrl_maps_back_to_com(self, qapp,
                                                  isolated_cache):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            win._apply_discovered_k2000_resource("ASRL4::INSTR")
            assert win.cmbK2000Transport.currentText().upper() == "RS232"
            assert win.editK2000Port.text().upper() == "COM4"
        finally:
            win.close()

    def test_k2000_connect_persists_last_successful(self, qapp,
                                                      isolated_cache):
        # Sim-mode connect must NOT pollute the cache.
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            cache = win._visa_cache
            before = cache.get(win.K2000_CACHE_KEY).last_successful
            win.chkK2000Sim.setChecked(True)
            win._toggle_k2000_connect()
            after = cache.get(win.K2000_CACHE_KEY).last_successful
            assert before == after
        finally:
            win.close()

    def test_restore_k2000_last_successful_prefills_combobox(
            self, qapp, isolated_cache):
        from LPmeasurement import LPMainWindow
        win = LPMainWindow()
        try:
            cache = win._visa_cache
            cache.mark_successful(win.K2000_CACHE_KEY, "GPIB0::25::INSTR")
            # Invoke the restore path explicitly — it ran once in
            # __init__, but now the cache has a real value.
            win._restore_k2000_last_successful()
            assert win.editK2000Visa.text() == "GPIB0::25::INSTR"
            assert win.cmbK2000Transport.currentText().upper() == "GPIB"
        finally:
            win.close()
