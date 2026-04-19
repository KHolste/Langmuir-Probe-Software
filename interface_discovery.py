"""Interface / instrument discovery for the Langmuir main GUI.

The module has two layers:

* a pure-function API (:func:`discover_resources`, :func:`probe_resource`)
  that knows nothing about Qt and is trivially testable;
* a :class:`InterfaceDiscoveryWindow` (lazy Qt import) that opens from
  the main window's ``Tools → Interface Discovery…`` menu entry.

Design principles
-----------------

* **Honest about limits.**  We do not pretend to do vendor-neutral
  device enumeration.  We merge what two reliable sources report:

  1. whatever the installed PyVISA backend enumerates via
     :func:`pyvisa.ResourceManager.list_resources`;
  2. whatever Windows reports through :func:`serial.tools.list_ports.comports`
     — which also shows COM-port adapters that VISA does not recognise.

  Both sources are shown together with a clear ``source`` column so
  the operator can see exactly who reported each row.

* **Probing is operator-initiated.**  Listing a resource does not open
  it.  Only when the user clicks *Probe* on a specific row do we open
  a VISA session, send ``*IDN?`` and report the outcome via the same
  :class:`visa_errors.ClassifiedVisaError` taxonomy the main GUI
  already uses.

* **Defaults protected.**  Discovery never silently rewrites the SMU
  or K2000 combo selection in the main window.  The operator has to
  explicitly click *Use for SMU* / *Use for K2000* on a row; only then
  is the cache's ``last_successful`` pointer updated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Resource-type classification helpers.
# ---------------------------------------------------------------------------
def classify_visa_resource(resource: str) -> str:
    """Return a short, human-readable transport label for a VISA
    resource string.  Falls through to ``"VISA"`` for anything we
    don't specifically recognise so the UI never shows an empty
    cell.
    """
    up = str(resource or "").upper()
    if up.startswith("GPIB"):
        return "GPIB"
    if up.startswith("ASRL"):
        return "Serial (VISA ASRL)"
    if up.startswith("USB"):
        return "USB (VISA)"
    if up.startswith("TCPIP"):
        return "TCP/IP (VISA)"
    if up.startswith("VXI"):
        return "VXI"
    if up.startswith("PXI"):
        return "PXI"
    return "VISA"


# ---------------------------------------------------------------------------
# Data types.
# ---------------------------------------------------------------------------
@dataclass
class DiscoveredResource:
    """One row in the discovery table.

    ``source`` is one of ``"visa"`` (reported by PyVISA /
    ``list_resources``), ``"serial"`` (reported by
    ``serial.tools.list_ports``), or ``"merged"`` (a COM port that
    appears in BOTH lists — we canonicalise it as a single row with
    the VISA resource string).
    """
    resource: str
    transport: str
    description: str = ""
    source: str = "visa"
    extra: dict = field(default_factory=dict)


@dataclass
class InterfaceProbeResult:
    """Outcome of a single *IDN? probe against one resource."""
    resource: str
    ok: bool
    idn: str = ""
    error_kind: str = ""        # VisaErrorKind value or ""
    error_message: str = ""
    remediation: str = ""


# ---------------------------------------------------------------------------
# Discovery.
# ---------------------------------------------------------------------------
def _visa_resources() -> list[str]:
    """Return the set of resources the installed VISA backend sees.

    Never raises: if the backend is missing or refuses to initialise
    (the typical "no VISA library" case), we return an empty list and
    let the caller annotate the UI.  The serial fallback still
    provides useful information even when no VISA is installed.
    """
    try:
        import pyvisa  # type: ignore
        rm = pyvisa.ResourceManager()
    except Exception:
        return []
    try:
        return [str(r) for r in rm.list_resources()]
    except Exception:
        return []
    finally:
        try:
            rm.close()
        except Exception:
            pass


def _serial_ports() -> list[tuple[str, str, str]]:
    """Return ``[(device, description, hwid), ...]`` from pyserial.

    ``device`` is e.g. ``"COM4"``; ``description`` is the friendly
    Windows device name; ``hwid`` is the USB VID/PID string when
    the port is a USB-to-serial adapter.
    """
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return []
    out: list[tuple[str, str, str]] = []
    try:
        for port in list_ports.comports():
            out.append((
                str(getattr(port, "device", "")),
                str(getattr(port, "description", "") or ""),
                str(getattr(port, "hwid", "") or ""),
            ))
    except Exception:
        return []
    return out


def _com_to_asrl(com: str) -> str:
    """Map a ``COMn`` string to the VISA ``ASRLn::INSTR`` form."""
    u = str(com).strip().upper()
    if u.startswith("COM"):
        return f"ASRL{u[3:]}::INSTR"
    return u


def _asrl_to_com(asrl: str) -> str:
    """Map ``ASRLn::INSTR`` back to ``COMn`` when possible."""
    u = str(asrl).strip().upper()
    if u.startswith("ASRL") and u.endswith("::INSTR"):
        return "COM" + u[len("ASRL"):-len("::INSTR")]
    return ""


def discover_resources() -> list[DiscoveredResource]:
    """Collect the merged resource table.

    The returned list is stable-sorted by transport then resource so
    two successive calls on the same machine produce identical rows.
    VISA rows come first with their transport label; serial ports
    that are also VISA-known are shown as one row (source
    ``"merged"``); serial ports NOT seen by VISA are shown as
    ``source="serial"`` — often the case for fresh USB-to-serial
    adapters that need an INF driver install before VISA will see
    them.
    """
    visa = _visa_resources()
    serial = _serial_ports()

    rows: list[DiscoveredResource] = []
    seen_visa: set[str] = set()

    # Index serial ports by both COM form and VISA ASRL form for fast
    # merge.
    ser_by_com = {dev.upper(): (dev, desc, hwid)
                   for dev, desc, hwid in serial if dev}
    ser_by_asrl = {_com_to_asrl(dev): entry
                    for dev, entry in
                    ((d, (d, desc, hwid))
                     for d, desc, hwid in serial if d)}

    for res in visa:
        up = res.upper()
        # If this is an ASRLn::INSTR and we have the matching COM, use
        # the friendly description from pyserial as the description.
        if up.startswith("ASRL"):
            entry = ser_by_asrl.get(up)
            if entry:
                dev, desc, hwid = entry
                rows.append(DiscoveredResource(
                    resource=res,
                    transport=classify_visa_resource(res),
                    description=desc or dev,
                    source="merged",
                    extra={"com": dev, "hwid": hwid},
                ))
                seen_visa.add(dev.upper())
                continue
        rows.append(DiscoveredResource(
            resource=res,
            transport=classify_visa_resource(res),
            description="",
            source="visa",
        ))

    # Serial-only rows: COM ports that VISA does not report.  Useful
    # for diagnosing "Windows sees the adapter but VISA does not".
    for dev, desc, hwid in serial:
        if dev.upper() in seen_visa:
            continue
        # Was it already folded in as a merged ASRL row?  Skip.
        if _com_to_asrl(dev).upper() in {r.resource.upper()
                                            for r in rows
                                            if r.source == "merged"}:
            continue
        rows.append(DiscoveredResource(
            resource=dev,                     # COMn, not ASRL
            transport="Serial (no VISA)",
            description=desc or "",
            source="serial",
            extra={"hwid": hwid},
        ))

    rows.sort(key=lambda r: (r.transport, r.resource))
    return rows


# ---------------------------------------------------------------------------
# Probing.
# ---------------------------------------------------------------------------
def _coerce_to_visa(resource: str) -> str:
    """Accept both ``COMn`` and full VISA resource strings.  Serial
    devices without a VISA description get mapped to ``ASRLn::INSTR``
    so probing goes through PyVISA consistently.
    """
    s = str(resource).strip()
    if s.upper().startswith("COM"):
        return _com_to_asrl(s)
    return s


def probe_resource(
    resource: str,
    *,
    timeout_ms: int = 1500,
) -> InterfaceProbeResult:
    """Open ``resource`` with a short timeout, send ``*IDN?``, and
    return a structured outcome.

    Never raises.  On any failure the result carries a classified
    ``error_kind`` (via :func:`visa_errors.classify`) plus the
    operator-facing remediation hint, so the GUI can paint the same
    colour language it already uses elsewhere.
    """
    target = _coerce_to_visa(resource)
    try:
        import pyvisa  # type: ignore
    except Exception as exc:
        return _failed(resource, exc,
                        hint_when_import_fails=True)

    rm = None
    inst = None
    try:
        rm = pyvisa.ResourceManager()
        inst = rm.open_resource(target)
        inst.timeout = int(timeout_ms)
        # Some serial instruments expect CR termination; set both
        # conservatively — a Keysight SMU on GPIB ignores them.
        try:
            inst.read_termination = "\r"
            inst.write_termination = "\r"
        except Exception:
            pass
        idn = str(inst.query("*IDN?")).strip()
        return InterfaceProbeResult(
            resource=resource, ok=True, idn=idn)
    except Exception as exc:
        return _failed(resource, exc)
    finally:
        try:
            if inst is not None:
                inst.close()
        except Exception:
            pass
        try:
            if rm is not None:
                rm.close()
        except Exception:
            pass


def _failed(resource: str, exc: BaseException, *,
              hint_when_import_fails: bool = False
              ) -> InterfaceProbeResult:
    """Build an :class:`InterfaceProbeResult` for a failed probe."""
    try:
        from visa_errors import classify, REMEDIATION, VisaErrorKind
        kind = classify(exc)
        hint = REMEDIATION.get(kind,
                                 REMEDIATION[VisaErrorKind.UNKNOWN])
        kind_value = kind.value
    except Exception:
        kind_value = "unknown"
        hint = ("Install Keysight IO Libraries Suite or NI-VISA."
                if hint_when_import_fails else
                "See instrument connection and drivers.")
    return InterfaceProbeResult(
        resource=resource,
        ok=False,
        idn="",
        error_kind=kind_value,
        error_message=f"{type(exc).__name__}: {exc}",
        remediation=hint,
    )


# ---------------------------------------------------------------------------
# Qt window (lazy import — the module above must stay importable in
# headless tests).
# ---------------------------------------------------------------------------
ApplyHook = Callable[[str], None]


def open_interface_discovery(
    parent=None,
    *,
    on_apply_smu: Optional[ApplyHook] = None,
    on_apply_k2000: Optional[ApplyHook] = None,
) -> "InterfaceDiscoveryWindow":
    """Singleton-style helper: create and show the discovery window.

    ``on_apply_smu`` / ``on_apply_k2000`` are callbacks used when the
    operator clicks the *Use for SMU* / *Use for K2000* buttons.  The
    parent is expected to handle cache persistence and to repopulate
    the relevant main-window combo / line edit.
    """
    win = InterfaceDiscoveryWindow(
        parent,
        on_apply_smu=on_apply_smu,
        on_apply_k2000=on_apply_k2000,
    )
    win.show()
    win.raise_()
    win.activateWindow()
    return win


class InterfaceDiscoveryWindow:
    """Dedicated top-level window for interface / resource discovery.

    Layout:
      * a header with Refresh + a one-line summary ("N resources: …").
      * a QTableWidget with columns:
        Resource · Transport · Source · Description · Probe · Result.
      * per-row Probe button (operator-initiated — we never probe
        automatically).
      * footer with Apply-to-SMU / Apply-to-K2000 / Copy / Close.

    The Probe button triggers a single `*IDN?` via
    :func:`probe_resource`; the response (or classified error) lands
    in the Result column of the active row.
    """

    # Column indexes — kept as class constants so tests can address
    # specific columns without magic numbers.
    COL_RES = 0
    COL_TRANSPORT = 1
    COL_SOURCE = 2
    COL_DESC = 3
    COL_PROBE = 4
    COL_RESULT = 5
    COLUMN_HEADERS = (
        "Resource", "Transport", "Source", "Description",
        "Probe", "Result / IDN",
    )

    DEFAULT_SIZE = (900, 520)

    def __init__(self, parent=None, *,
                 on_apply_smu: Optional[ApplyHook] = None,
                 on_apply_k2000: Optional[ApplyHook] = None):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QTableWidget, QHeaderView, QAbstractItemView,
            QAbstractScrollArea,
        )

        self._on_apply_smu = on_apply_smu
        self._on_apply_k2000 = on_apply_k2000

        self._win = QWidget(parent, Qt.WindowType.Window)
        self._win.setWindowTitle("Interface Discovery")
        self._win.setMinimumSize(*self.DEFAULT_SIZE)
        self._win.resize(*self.DEFAULT_SIZE)

        outer = QVBoxLayout(self._win)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Header
        header = QHBoxLayout()
        header.addWidget(QLabel(
            "<b>Available communication interfaces and "
            "instrument resources</b>"))
        header.addStretch(1)
        self.btnRefresh = QPushButton("Refresh")
        self.btnRefresh.setToolTip(
            "Re-scan the installed VISA backend + the Windows "
            "serial-port list.  Does NOT open any instrument.")
        self.btnRefresh.clicked.connect(self.refresh)
        header.addWidget(self.btnRefresh)
        outer.addLayout(header)

        # Summary line
        self.lblSummary = QLabel("")
        self.lblSummary.setStyleSheet("color:#8890a0; font-size: 11px;")
        self.lblSummary.setWordWrap(True)
        outer.addWidget(self.lblSummary)

        # Table
        self.table = QTableWidget(0, len(self.COLUMN_HEADERS),
                                     self._win)
        self.table.setHorizontalHeaderLabels(self.COLUMN_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy
            .AdjustToContents)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(
            self.COL_RES,
            QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(
            self.COL_TRANSPORT,
            QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(
            self.COL_SOURCE,
            QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(
            self.COL_DESC,
            QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(
            self.COL_PROBE,
            QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(
            self.COL_RESULT,
            QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self.table, 1)

        # Footer
        footer = QHBoxLayout()
        self.btnApplySmu = QPushButton("Use for SMU")
        self.btnApplySmu.setToolTip(
            "Apply the selected resource as the SMU's VISA address "
            "in the main window.  Your last-working SMU address "
            "stays the default until you click Connect there.")
        self.btnApplySmu.clicked.connect(self._apply_smu)
        footer.addWidget(self.btnApplySmu)

        self.btnApplyK2000 = QPushButton("Use for K2000")
        self.btnApplyK2000.setToolTip(
            "Apply the selected resource to the K2000 transport / "
            "VISA field in the main window.  For COM ports the "
            "transport switches to RS232 automatically.")
        self.btnApplyK2000.clicked.connect(self._apply_k2000)
        footer.addWidget(self.btnApplyK2000)

        self.btnCopy = QPushButton("Copy resource")
        self.btnCopy.setToolTip(
            "Copy the selected resource string to the clipboard.")
        self.btnCopy.clicked.connect(self._copy_selected)
        footer.addWidget(self.btnCopy)

        footer.addStretch(1)
        self.btnClose = QPushButton("Close")
        self.btnClose.clicked.connect(self._win.close)
        footer.addWidget(self.btnClose)
        outer.addLayout(footer)

        # Initial population
        self.refresh()

    # -- public API -------------------------------------------------
    def show(self):
        self._win.show()

    def raise_(self):
        self._win.raise_()

    def activateWindow(self):
        self._win.activateWindow()

    def close(self):
        # QTableWidget cell widgets (our per-row Probe buttons) live
        # in Qt's ownership tree but are reached by a Python ref via
        # the closure on clicked.connect.  If we let garbage
        # collection tear them down after the QWidget, the offscreen
        # Qt platform corrupts its heap.  Dropping the rows + cell
        # widgets here — while the Qt event loop is still alive —
        # keeps Python and Qt destruction in lockstep.
        try:
            self.table.clearContents()
            self.table.setRowCount(0)
        except Exception:
            pass
        try:
            self._win.deleteLater()
        except Exception:
            pass
        try:
            self._win.close()
        except Exception:
            pass

    def is_visible(self) -> bool:
        return bool(self._win.isVisible())

    # -- slots ------------------------------------------------------
    def refresh(self) -> None:
        """Re-run :func:`discover_resources` and rebuild the table."""
        rows = discover_resources()
        self._rows: list[DiscoveredResource] = list(rows)
        self.table.setRowCount(0)
        for r in rows:
            self._append_row(r)
        # Summary: honest about "nothing found" vs "nothing reachable".
        if not rows:
            self.lblSummary.setText(
                "No interfaces reported by either VISA or the "
                "Windows serial-port list.  Install a system VISA "
                "(Keysight IO Libraries or NI-VISA) or plug in a "
                "USB-to-serial adapter, then Refresh.")
        else:
            visa_n = sum(1 for r in rows if r.source in ("visa",
                                                          "merged"))
            ser_n = sum(1 for r in rows if r.source in ("serial",
                                                         "merged"))
            self.lblSummary.setText(
                f"{len(rows)} resource(s): {visa_n} via VISA, "
                f"{ser_n} via Windows serial enumeration.  "
                "Click Probe on a row to send *IDN? and identify "
                "the instrument.")

    def resources(self) -> list[DiscoveredResource]:
        """Return the currently-displayed rows (for test / external)."""
        return list(getattr(self, "_rows", []))

    # -- internals --------------------------------------------------
    def _append_row(self, r: DiscoveredResource) -> None:
        from PySide6.QtWidgets import (
            QTableWidgetItem, QPushButton,
        )
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, self.COL_RES,
                             QTableWidgetItem(r.resource))
        self.table.setItem(row, self.COL_TRANSPORT,
                             QTableWidgetItem(r.transport))
        self.table.setItem(row, self.COL_SOURCE,
                             QTableWidgetItem(r.source))
        self.table.setItem(row, self.COL_DESC,
                             QTableWidgetItem(r.description or ""))
        btn = QPushButton("Probe")
        btn.setToolTip(
            "Open this resource briefly and send *IDN?.  Only this "
            "one row is touched; other resources are not opened.")
        btn.clicked.connect(lambda _=False, _r=row: self._probe_row(_r))
        self.table.setCellWidget(row, self.COL_PROBE, btn)
        self.table.setItem(row, self.COL_RESULT, QTableWidgetItem(""))

    def _selected_resource(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row].resource

    def _probe_row(self, row: int) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        if row < 0 or row >= len(self._rows):
            return
        self.table.setItem(row, self.COL_RESULT,
                             QTableWidgetItem("probing..."))
        # Process events so the "probing..." cell shows before the
        # blocking VISA open — but ONLY when a real Qt app is
        # running.  Under the offscreen test runner a re-entrant
        # processEvents during teardown can corrupt the Qt heap, so
        # we skip it when no top-level activeWindow is present.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None and app.activeWindow() is not None:
            app.processEvents()
        result = probe_resource(self._rows[row].resource)
        if result.ok:
            self.table.setItem(row, self.COL_RESULT,
                                 QTableWidgetItem(result.idn or "(OK)"))
        else:
            txt = (f"{result.error_kind}: {result.error_message} "
                   f"— {result.remediation}")
            self.table.setItem(row, self.COL_RESULT,
                                 QTableWidgetItem(txt))

    def _apply_smu(self) -> None:
        res = self._selected_resource()
        if res is None:
            return
        if self._on_apply_smu is not None:
            self._on_apply_smu(res)

    def _apply_k2000(self) -> None:
        res = self._selected_resource()
        if res is None:
            return
        if self._on_apply_k2000 is not None:
            self._on_apply_k2000(res)

    def _copy_selected(self) -> None:
        from PySide6.QtWidgets import QApplication
        res = self._selected_resource()
        if res is None:
            return
        QApplication.clipboard().setText(res)


__all__ = [
    "DiscoveredResource", "InterfaceProbeResult",
    "classify_visa_resource", "discover_resources", "probe_resource",
    "open_interface_discovery", "InterfaceDiscoveryWindow",
]
