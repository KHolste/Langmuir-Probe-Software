"""
Double-Langmuir-Probe Monitor – Standalone-Akquisitions-GUI.

Keysight B2901 SMU ueber NI GPIB-USB: Spannungssweep mit Strommessung,
Live-I-V-Plot und CSV-Export.

Erste Minimalversion – kein Fit, keine Plasmaparameter-Extraktion.
"""
from __future__ import annotations

import json
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSpinBox, QComboBox,
    QPushButton, QTextEdit, QProgressBar, QScrollArea, QSizePolicy,
    QSplitter, QFrame, QFileDialog, QMessageBox, QCheckBox,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from keysight_b2901 import KeysightB2901PSU
from fake_b2901 import FakeB2901
from theme import DARK_THEME, build_stylesheet
from utils import append_log, set_led
from visa_persistence import VisaCache

log = logging.getLogger("DLP")

# ── helpers ────────────────────────────────────────────────────────────


def build_voltage_list(
    v_start: float, v_stop: float, v_step: float,
) -> np.ndarray:
    """Return monotonic voltage array from *v_start* to *v_stop* (inclusive)."""
    if v_step <= 0:
        raise ValueError("v_step must be > 0")
    if v_start <= v_stop:
        pts = np.arange(v_start, v_stop + v_step * 0.5, v_step)
    else:
        pts = np.arange(v_start, v_stop - v_step * 0.5, -v_step)
    return pts


def build_sweep_voltages(
    v_start: float, v_stop: float, v_step: float,
    bidirectional: bool = False,
) -> list[tuple[float, str]]:
    """Return [(voltage, direction), ...] for a complete sweep.

    Forward runs v_start → v_stop. If *bidirectional*, a reverse leg
    v_stop → v_start is appended **without** repeating the turning point.
    """
    fwd = build_voltage_list(v_start, v_stop, v_step)
    pairs = [(float(v), "fwd") for v in fwd]
    if bidirectional and len(fwd) > 1:
        rev = fwd[-2::-1]  # exclude turning point
        pairs += [(float(v), "rev") for v in rev]
    return pairs


def make_csv_path(folder: Path, prefix: str = "DLP",
                   method: str = "double") -> Path:
    """Auto-generate a timestamped CSV path under the per-method
    subfolder convention.

    Returns ``<folder>/<method>/LP_<timestamp>_<method>.csv`` —
    method defaults to ``"double"`` so legacy callers (V1/V2 direct
    sweeps without a method tag) end up in the Double bucket.  The
    ``prefix`` argument is kept for backward signature compatibility
    but no longer affects the filename; the new naming is method-
    suffixed instead.  See :mod:`dlp_save_paths` for the underlying
    helpers.
    """
    from dlp_save_paths import make_lp_csv_path_for_method
    return make_lp_csv_path_for_method(folder, method)


def write_csv(
    path: Path,
    meta: dict,
    voltages: list[float],
    currents: list[float],
    stds: list[float],
    v_actual: list[float],
    directions: list[str] | None = None,
    compliance: list[bool] | None = None,
) -> None:
    """Write I-V data with metadata header to *path*."""
    has_ext = directions is not None and compliance is not None
    with open(path, "w", newline="", encoding="utf-8") as fh:
        # Versioned schema banner first — see dlp_csv_schema.  Legacy
        # readers continue to work (the comment parser only captures
        # "k: v" lines; the product-identity line is ignored).
        from dlp_csv_schema import write_header
        write_header(fh)
        for k, v in meta.items():
            fh.write(f"# {k}: {v}\n")
        fh.write("#\n")
        if has_ext:
            fh.write("# V_soll_V,V_ist_V,I_mean_A,I_std_A,dir,compl\n")
            for i, (vs, va, im, sd, dr, cp) in enumerate(zip(
                voltages, v_actual, currents, stds, directions, compliance,
            )):
                try:
                    fh.write(f"{float(vs):.6g},{float(va):.6g},"
                             f"{float(im):.6e},{float(sd):.6e},"
                             f"{str(dr)},{1 if cp else 0}\n")
                except Exception as exc:
                    print(f"[write_csv] ERROR at row {i}: {exc} "
                          f"(vs={vs!r}, va={va!r}, im={im!r}, "
                          f"sd={sd!r}, dr={dr!r}, cp={cp!r})", flush=True)
                    fh.write(f"# ERROR at row {i}: {exc}\n")
        else:
            fh.write("# V_soll_V,V_ist_V,I_mean_A,I_std_A\n")
            for vs, va, im, sd in zip(voltages, v_actual, currents, stds):
                fh.write(f"{float(vs):.6g},{float(va):.6g},"
                         f"{float(im):.6e},{float(sd):.6e}\n")


# ── worker ─────────────────────────────────────────────────────────────


class DLPScanWorker(QObject):
    """Runs voltage sweep in a background thread."""

    point = Signal(int, int, float, float, float, float, bool, str)
    #              idx  n   V_soll V_ist  I_mean I_std  compl dir
    finished = Signal(float)   # elapsed_s
    failed = Signal(str)
    stopped = Signal()

    def __init__(
        self, smu,
        sweep: list[tuple[float, str]],
        settle_s: float,
        n_avg: int,
    ):
        super().__init__()
        self.smu = smu
        self.sweep = sweep
        self.settle_s = settle_s
        self.n_avg = max(1, n_avg)
        self._stop = False

    def request_stop(self):
        self._stop = True

    @Slot()
    def run(self):
        t0 = time.perf_counter()
        n = len(self.sweep)
        op = "output_enable"
        try:
            self.smu.output(True)
            for idx, (v_soll, direction) in enumerate(self.sweep):
                if self._stop:
                    self.stopped.emit()
                    return
                op = f"set_voltage({v_soll:.4g})"
                self.smu.set_voltage(v_soll)
                time.sleep(self.settle_s)
                op = f"read_voltage @step {idx}"
                v_ist = self.smu.read_voltage()
                op = f"read_current @step {idx}"
                readings = [self.smu.read_current() for _ in range(self.n_avg)]
                i_mean = float(np.mean(readings))
                i_std = float(np.std(readings, ddof=1)) if len(readings) > 1 else 0.0
                compl = (hasattr(self.smu, "is_in_compliance")
                         and self.smu.is_in_compliance())
                self.point.emit(idx, n, v_soll, v_ist, i_mean, i_std,
                                compl, direction)
            self.finished.emit(time.perf_counter() - t0)
        except Exception as exc:
            etype = type(exc).__name__
            self.failed.emit(f"{etype}: {exc} [during {op}]")
        finally:
            try:
                self.smu.set_voltage(0.0)
                self.smu.output(False)
            except Exception:
                pass


# ── main window ────────────────────────────────────────────────────────


class DLPMainWindow(QMainWindow):
    """Double-Langmuir-Probe Monitor GUI."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Double-Langmuir-Probe Monitor")
        self.resize(1000, 620)
        self._theme = DARK_THEME
        self.setStyleSheet(build_stylesheet(self._theme))

        self.smu: KeysightB2901PSU | None = None
        self._worker: DLPScanWorker | None = None
        self._thread: QThread | None = None
        self._save_folder = Path.cwd()
        self._visa_cache = VisaCache()
        self._visa_device_key = "b2901"

        # data buffers
        self._v_soll: list[float] = []
        self._v_ist: list[float] = []
        self._i_mean: list[float] = []
        self._i_std: list[float] = []
        self._directions: list[str] = []
        self._compliance: list[bool] = []

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self):
        # A QScrollArea wraps the whole UI so the window stays usable
        # when shrunk or when the inner splitters are dragged wide –
        # both horizontal and vertical scrollbars appear on demand.
        scroll = QScrollArea()
        scroll.setObjectName("centralScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(scroll)

        central = QWidget()
        central.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Expanding)
        scroll.setWidget(central)
        root = QHBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("splitMain")
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)
        self._splitter_main = splitter

        # left panel — slightly more breathing room than before so the
        # group boxes stop visually crowding each other on small
        # displays.  Outer margins go from 4 px to 8 px; inter-group
        # spacing from the Qt default (~6) to 8.
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(8)

        # -- instrument group
        grp_inst = QGroupBox("Instrument")
        gl = QVBoxLayout(grp_inst)
        gl.setContentsMargins(8, 8, 8, 8)
        gl.setSpacing(4)
        row = QHBoxLayout()
        row.addWidget(QLabel("VISA:"))
        self.cmbVisa = QComboBox()
        self.cmbVisa.setEditable(True)
        # Compactness: combo shows only the bare resource address
        # (e.g. "GPIB0::25::INSTR"); a typical address is ~20 chars,
        # so 100 px is enough.  The full IDN lives in the per-item
        # tooltip + the IDN label below.  Width grows naturally with
        # the row's stretch when the operator enlarges the window.
        self.cmbVisa.setMinimumWidth(100)
        self.cmbVisa.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmbVisa.setToolTip("Last-scanned VISA resources. "
                                 "Last successful connection is preselected. "
                                 "Hover an entry for its instrument IDN. "
                                 "Use Scan to refresh.")
        row.addWidget(self.cmbVisa, 1)
        self.btnScan = QPushButton("Scan")
        self.btnScan.setMaximumWidth(60)
        self.btnScan.clicked.connect(self._scan_resources)
        row.addWidget(self.btnScan)
        gl.addLayout(row)
        self._populate_visa_combo_from_cache()

        row_sim = QHBoxLayout()
        self.chkSim = QCheckBox("Simulation")
        self.chkSim.setToolTip("Use simulated SMU (no hardware required)")
        row_sim.addWidget(self.chkSim)
        row_sim.addStretch()
        gl.addLayout(row_sim)
        # Exposed for subclasses (V2) so they can append a Sim-Options
        # button next to the Simulation checkbox without parent-walking.
        self._row_sim_layout = row_sim

        # Compact connect row: Connect button + connection LED +
        # compliance LED (with its label) + spacer.  IDN moves to its
        # own line below so a long instrument string never widens the
        # whole left column.
        row2 = QHBoxLayout()
        self.btnConnect = QPushButton("Connect")
        self.btnConnect.clicked.connect(self._toggle_connect)
        row2.addWidget(self.btnConnect)
        self.ledConn = QFrame()
        self.ledConn.setFixedSize(16, 16)
        set_led(self.ledConn, self._theme["led_grey"])
        row2.addWidget(self.ledConn)
        # Iteration 4b – live compliance indicator, fed from the
        # per-point ``compl`` flag the worker already produces.  Sticky
        # red within a sweep so the user can glance at it after the run.
        self.lblComplLed = QLabel("Compl")
        self.lblComplLed.setToolTip(
            "Compliance LED:\n"
            "  grey  – idle / not sweeping\n"
            "  green – sweep running, no compliance hit so far\n"
            "  red   – at least one compliance hit during this sweep")
        row2.addWidget(self.lblComplLed)
        self.ledCompl = QFrame()
        self.ledCompl.setFixedSize(16, 16)
        self.ledCompl.setToolTip(self.lblComplLed.toolTip())
        set_led(self.ledCompl, self._theme["led_grey"])
        self._compl_led_state = "idle"
        row2.addWidget(self.ledCompl)
        row2.addStretch(1)
        gl.addLayout(row2)
        # IDN on its own slim line beneath the buttons; word-wrap +
        # small grey font keeps it readable without forcing the
        # column wider.
        self.lblIdn = QLabel("")
        self.lblIdn.setWordWrap(True)
        self.lblIdn.setStyleSheet("color: #8890a0; font-size: 10px;")
        gl.addWidget(self.lblIdn)
        lv.addWidget(grp_inst)
        # Exposed for V2 subclasses.
        self._inst_layout = gl
        self._grp_inst = grp_inst

        # -- sweep parameters
        grp_sw = QGroupBox("Sweep")
        sv = QVBoxLayout(grp_sw)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(4)
        self.spnVstart = self._add_double_row(sv, "V_start (V):", -210, 210, -50.0)
        self.spnVstop = self._add_double_row(sv, "V_stop (V):", -210, 210, 50.0)
        self.spnVstep = self._add_double_row(sv, "V_step (V):", 0.01, 100, 0.5)
        self.spnSettle = self._add_double_row(sv, "Settle (s):", 0.01, 10.0, 0.02)
        r_avg = QHBoxLayout()
        r_avg.addWidget(QLabel("Averages:"))
        self.spnAvg = QSpinBox()
        self.spnAvg.setRange(1, 100)
        self.spnAvg.setValue(3)
        r_avg.addWidget(self.spnAvg)
        sv.addLayout(r_avg)
        self.spnCompl = self._add_double_row(sv, "Compliance (mA):", 0.01, 100, 10.0)
        self.chkBidir = QCheckBox("Bidirectional")
        self.chkBidir.setToolTip("Sweep forward then reverse")
        sv.addWidget(self.chkBidir)
        lv.addWidget(grp_sw)

        # -- controls
        grp_ctrl = QGroupBox("Control")
        cv = QVBoxLayout(grp_ctrl)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(4)
        rc = QHBoxLayout()
        self.btnStart = QPushButton("Start")
        self.btnStart.clicked.connect(self._start_sweep)
        rc.addWidget(self.btnStart)
        self.btnStop = QPushButton("Stop")
        self.btnStop.setEnabled(False)
        self.btnStop.clicked.connect(self._stop_sweep)
        rc.addWidget(self.btnStop)
        cv.addLayout(rc)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        cv.addWidget(self.progress)
        self.lblStatus = QLabel("Idle")
        cv.addWidget(self.lblStatus)
        lv.addWidget(grp_ctrl)
        # Exposed for V2 subclasses.
        self._ctrl_layout = cv
        self._grp_ctrl = grp_ctrl

        # -- save folder
        grp_file = QGroupBox("Output")
        fv = QVBoxLayout(grp_file)
        fv.setContentsMargins(8, 8, 8, 8)
        fv.setSpacing(4)
        rf = QHBoxLayout()
        self.lblFolder = QLabel(str(self._save_folder))
        self.lblFolder.setWordWrap(True)
        rf.addWidget(self.lblFolder, 1)
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(30)
        btn_browse.clicked.connect(self._browse_folder)
        rf.addWidget(btn_browse)
        fv.addLayout(rf)
        rc2 = QHBoxLayout()
        btn_save_cfg = QPushButton("Save Config")
        btn_save_cfg.clicked.connect(self._save_config)
        rc2.addWidget(btn_save_cfg)
        btn_load_cfg = QPushButton("Load Config")
        btn_load_cfg.clicked.connect(self._load_config)
        rc2.addWidget(btn_load_cfg)
        fv.addLayout(rc2)
        lv.addWidget(grp_file)
        # Exposed for V2 subclasses (inserts the new Process-gas-types
        # group between Control and Output by inserting before grp_file).
        self._left_v_layout = lv
        self._grp_file = grp_file
        self._fv_layout = fv

        lv.addStretch()
        splitter.addWidget(left)

        # right panel – plot on top, log below, separated by a
        # vertical splitter so the ratio can be adjusted with the mouse.
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.setObjectName("splitRight")
        right_split.setChildrenCollapsible(False)
        self._splitter_right = right_split

        plot_container = QWidget()
        plot_container.setContentsMargins(2, 2, 2, 2)
        pv = QVBoxLayout(plot_container)
        pv.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(6, 4))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumSize(200, 150)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self.ax = self.figure.add_subplot(111)
        self._apply_plot_theme()
        self.line_fwd, = self.ax.plot([], [], "o-",
                                      color=self._theme["plot_done"],
                                      markersize=3, label="Forward")
        self.line_rev, = self.ax.plot([], [], "s-",
                                      color=self._theme["plot_ahead"],
                                      markersize=3, label="Reverse")
        self.line_compl, = self.ax.plot([], [], "x",
                                        color=self._theme["danger"],
                                        markersize=7, label="Compliance")
        self.ax.set_xlabel("Voltage (V)")
        self.ax.set_ylabel("Current (A)")
        self.ax.grid(True, color=self._theme["plot_grid"], alpha=0.5)
        # Suppress the cryptic offset / scientific-notation badge.
        from utils import apply_clean_axis_format
        apply_clean_axis_format(self.ax)
        self.figure.tight_layout()
        pv.addWidget(self.canvas)
        right_split.addWidget(plot_container)

        # log — long SCPI strings, IDNs and tracebacks are easier to
        # read with horizontal scrolling than with auto-wrap.
        self.txtLog = QTextEdit()
        self.txtLog.setObjectName("txtLog")
        self.txtLog.setReadOnly(True)
        self.txtLog.setMinimumHeight(80)
        self.txtLog.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        right_split.addWidget(self.txtLog)
        right_split.setStretchFactor(0, 3)
        right_split.setStretchFactor(1, 1)

        splitter.addWidget(right_split)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # Compactness: pre-seed the splitter so the left column starts
        # at ~280 px (≈25 % narrower than the previous default).  The
        # user can still drag the handle to widen it.
        splitter.setSizes([280, 720])

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _add_double_row(layout, label, lo, hi, default):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spn = QDoubleSpinBox()
        spn.setRange(lo, hi)
        spn.setDecimals(3)
        spn.setValue(default)
        row.addWidget(spn)
        layout.addLayout(row)
        return spn

    def _apply_plot_theme(self):
        t = self._theme
        self.figure.set_facecolor(t["plot_fig"])
        self.ax.set_facecolor(t["plot_bg"])
        for spine in self.ax.spines.values():
            spine.set_color(t["plot_fg"])
        self.ax.tick_params(colors=t["plot_fg"])
        self.ax.xaxis.label.set_color(t["plot_fg"])
        self.ax.yaxis.label.set_color(t["plot_fg"])

    def _set_sweep_ui(self, running: bool):
        self.btnStart.setEnabled(not running)
        self.btnStop.setEnabled(running)
        self.btnConnect.setEnabled(not running)
        for w in (self.spnVstart, self.spnVstop, self.spnVstep,
                  self.spnSettle, self.spnAvg, self.spnCompl,
                  self.chkBidir):
            w.setEnabled(not running)

    # ── compliance LED (iteration 4b) ─────────────────────────────

    _COMPL_LED_COLOR_KEY = {
        "idle":  "led_grey",
        "clear": "led_green",
        "hit":   "led_red",
    }

    def _set_compliance_led(self, state: str) -> None:
        """Drive the live compliance indicator from sweep data.

        ``state`` ∈ {"idle", "clear", "hit"}.  No SCPI traffic — the
        helper is invoked from the same point-handler that already
        receives the worker's per-point ``compl`` flag.

        Sticky-red contract (do NOT remove without explicit UX review):
        once :meth:`_on_point` records the first ``compl=True`` of a
        sweep, the LED stays red for every subsequent point because the
        update expression is ``"hit" if any(self._compliance) else
        "clear"``.  The state is intentionally NOT cleared on
        :meth:`_on_done` / :meth:`_on_stopped` / :meth:`_on_fail` –
        glancing at the LED after a run must reveal whether the SMU
        ever saturated.  The state resets to ``"idle"`` only on
        connect / disconnect, on the next ``_start_sweep``, or when an
        abort path produces zero data points (see those handlers).
        """
        key = self._COMPL_LED_COLOR_KEY.get(state)
        if key is None:
            state, key = "idle", "led_grey"
        self._compl_led_state = state
        set_led(self.ledCompl, self._theme[key])

    # ── slots ─────────────────────────────────────────────────────

    def _populate_visa_combo_from_cache(self) -> None:
        """Fill cmbVisa from the persisted scan cache.

        The visible text is the bare resource address only; the
        instrument IDN (when known) is attached as a per-item tooltip
        so it stays discoverable on hover without bloating the combo
        column.  Preselects the last-successful entry.
        """
        # Prefer the (label, resource, idn) triple form so we can wire
        # the IDN onto the per-item tooltip; fall back to the legacy
        # 2-tuple if an older cache implementation is in use.
        try:
            items = self._visa_cache.combo_items_with_idn(
                self._visa_device_key)
        except AttributeError:
            items = [(res, res, "") for _, res
                     in self._visa_cache.combo_items(
                         self._visa_device_key)]
        self.cmbVisa.blockSignals(True)
        self.cmbVisa.clear()
        for label, res, idn in items:
            self.cmbVisa.addItem(label, res)
            if idn:
                self.cmbVisa.setItemData(
                    self.cmbVisa.count() - 1,
                    f"{res}\n{idn}",
                    Qt.ItemDataRole.ToolTipRole)
        self.cmbVisa.blockSignals(False)
        last = self._visa_cache.get(self._visa_device_key).last_successful
        if last:
            idx = self.cmbVisa.findData(last)
            if idx >= 0:
                self.cmbVisa.setCurrentIndex(idx)

    def _scan_resources(self):
        append_log(self, "Scanning VISA resources ...", "info")
        try:
            resources = KeysightB2901PSU.scan_visa_resources()
        except Exception as exc:
            from visa_errors import format_for_operator
            append_log(self, format_for_operator(exc,
                                                   context="VISA scan"),
                       "error")
            return
        self._visa_cache.update_scan(self._visa_device_key, resources)
        self._populate_visa_combo_from_cache()
        if not resources:
            append_log(self, "No VISA instruments found.", "warn")
        else:
            append_log(self, f"{len(resources)} instrument(s) found.", "ok")

    def _toggle_connect(self):
        if self.smu is not None:
            self.smu.close()
            self.smu = None
            set_led(self.ledConn, self._theme["led_grey"])
            self._set_compliance_led("idle")
            self.lblIdn.setText("")
            self.btnConnect.setText("Connect")
            self.chkSim.setEnabled(True)
            append_log(self, "Disconnected.", "info")
            return

        compl_a = self.spnCompl.value() / 1000.0

        if self.chkSim.isChecked():
            self.smu = FakeB2901(current_compliance=compl_a,
                                  noise_std=1e-6)
            idn = self.smu.connect()
            set_led(self.ledConn, self._theme["led_green"])
            self._set_compliance_led("idle")
            self.lblIdn.setText(idn)
            self.btnConnect.setText("Disconnect")
            self.chkSim.setEnabled(False)
            append_log(self, f"Simulation connected: {idn}", "ok")
            return

        txt = self.cmbVisa.currentText()
        visa = self.cmbVisa.currentData() or (txt.split()[0] if txt.split() else "")
        if not visa:
            append_log(self, "No VISA resource selected.", "warn")
            return
        self.smu = KeysightB2901PSU(visa_resource=visa,
                                     current_compliance=compl_a)
        try:
            idn = self.smu.connect()
            set_led(self.ledConn, self._theme["led_green"])
            self._set_compliance_led("idle")
            self.lblIdn.setText(idn)
            self.btnConnect.setText("Disconnect")
            self.chkSim.setEnabled(False)
            append_log(self, f"Connected: {idn}", "ok")
            self._visa_cache.mark_successful(self._visa_device_key, visa)
        except Exception as exc:
            from visa_errors import format_for_operator
            append_log(self, format_for_operator(
                exc, context=f"B2901 connect ({visa})"), "error")
            self.smu = None

    def _browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Save folder",
                                              str(self._save_folder))
        if d:
            self._save_folder = Path(d)
            self.lblFolder.setText(str(self._save_folder))

    # ── sweep control ─────────────────────────────────────────────

    def _start_sweep(self):
        if self.smu is None:
            append_log(self, "Not connected.", "warn")
            return

        try:
            sweep = build_sweep_voltages(
                self.spnVstart.value(), self.spnVstop.value(),
                self.spnVstep.value(), self.chkBidir.isChecked(),
            )
        except ValueError as exc:
            append_log(self, str(exc), "error")
            return

        # update compliance in case user changed it
        compl_a = self.spnCompl.value() / 1000.0
        try:
            self.smu.set_current_limit(compl_a)
        except Exception as exc:
            append_log(self, f"Compliance set failed: {exc}", "error")
            return

        # clear buffers
        self._v_soll.clear(); self._v_ist.clear()
        self._i_mean.clear(); self._i_std.clear()
        self._directions.clear(); self._compliance.clear()
        # iter 4b: sweep is about to start — flip LED to "clean" (green).
        self._set_compliance_led("clear")
        for ln in (self.line_fwd, self.line_rev, self.line_compl):
            ln.set_data([], [])
        self.ax.relim(); self.ax.autoscale_view()
        self.canvas.draw_idle()
        self.progress.setMaximum(len(sweep))
        self.progress.setValue(0)

        self._worker = DLPScanWorker(
            self.smu, sweep, self.spnSettle.value(), self.spnAvg.value(),
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.point.connect(self._on_point, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        self._worker.failed.connect(self._on_fail, Qt.ConnectionType.QueuedConnection)
        self._worker.stopped.connect(self._on_stopped, Qt.ConnectionType.QueuedConnection)
        for sig in (self._worker.finished, self._worker.failed, self._worker.stopped):
            sig.connect(self._thread.quit)

        self._set_sweep_ui(True)
        self.lblStatus.setText("Sweeping ...")
        mode = "bidir" if self.chkBidir.isChecked() else "fwd"
        append_log(self, f"Sweep started ({mode}): "
                         f"{sweep[0][0]:.3f} -> {sweep[-1][0]:.3f} V, "
                         f"{len(sweep)} pts", "ok")
        self._thread.start()

    def _stop_sweep(self):
        if self._worker:
            self._worker.request_stop()

    # ── worker callbacks ──────────────────────────────────────────

    @Slot(int, int, float, float, float, float, bool, str)
    def _on_point(self, idx, n, v_soll, v_ist, i_mean, i_std,
                  compl, direction):
        self._v_soll.append(float(v_soll))
        self._v_ist.append(float(v_ist))
        self._i_mean.append(float(i_mean))
        self._i_std.append(float(i_std))
        self._directions.append(str(direction))
        self._compliance.append(bool(compl))

        # iter 4b: sticky-red within a sweep so the user can glance at
        # the LED after the run and tell whether the SMU ever saturated.
        self._set_compliance_led("hit" if any(self._compliance) else "clear")

        # update fwd/rev lines
        fwd_v = [v for v, d in zip(self._v_ist, self._directions) if d == "fwd"]
        fwd_i = [i for i, d in zip(self._i_mean, self._directions) if d == "fwd"]
        self.line_fwd.set_data(fwd_v, fwd_i)
        rev_v = [v for v, d in zip(self._v_ist, self._directions) if d == "rev"]
        rev_i = [i for i, d in zip(self._i_mean, self._directions) if d == "rev"]
        self.line_rev.set_data(rev_v, rev_i)
        # compliance markers
        cv = [v for v, c in zip(self._v_ist, self._compliance) if c]
        ci = [i for i, c in zip(self._i_mean, self._compliance) if c]
        self.line_compl.set_data(cv, ci)

        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw_idle()
        self.progress.setValue(idx + 1)
        tag = " COMPL" if compl else ""
        self.lblStatus.setText(f"Point {idx+1}/{n} [{direction}] "
                               f"V={v_ist:.3f} V  I={i_mean:.4e} A{tag}")

    @Slot(float)
    def _on_done(self, elapsed):
        self._set_sweep_ui(False)
        self.lblStatus.setText(f"Done ({elapsed:.1f} s)")
        append_log(self, f"Sweep finished in {elapsed:.1f} s "
                         f"({len(self._v_soll)} points).", "ok")
        self._save_csv(run_status="completed")

    @Slot(str)
    def _on_fail(self, msg):
        self._set_sweep_ui(False)
        self.lblStatus.setText(f"ERROR: {msg}")
        append_log(self, f"Sweep failed: {msg}", "error")
        if self._v_soll:
            self._save_csv(run_status="failed", failure_reason=msg)
        else:
            # Abort before the first point arrived: the green from
            # _start_sweep would falsely advertise "swept clean".
            self._set_compliance_led("idle")

    @Slot()
    def _on_stopped(self):
        self._set_sweep_ui(False)
        self.lblStatus.setText("Stopped")
        append_log(self, "Sweep stopped by user.", "warn")
        if self._v_soll:
            self._save_csv(run_status="aborted")
        else:
            # Same rationale as _on_fail: drop the leftover green if
            # the user stopped before any point came in.
            self._set_compliance_led("idle")

    # ── CSV ───────────────────────────────────────────────────────

    def _csv_dataset_method(self) -> str:
        """Resolve the acquisition-method tag used both for the CSV
        subfolder routing and the ``Method`` meta key.

        Subclasses (e.g. :class:`LPmeasurement.LPMainWindow`) override
        this to return the live dataset method.  The default here
        falls back to ``"double"`` so V1-only installations keep the
        historic behaviour.
        """
        return (getattr(self, "_dataset_method", None) or "double")

    def _make_csv_path(self, folder):
        """Hook around :func:`make_csv_path` so subclasses can route
        saves into a per-method subfolder without monkey-patching the
        module-level function."""
        return make_csv_path(folder, method=self._csv_dataset_method())

    def _write_csv(self, path, meta, *args, **kwargs):
        """Hook around :func:`write_csv` so subclasses can inject meta
        keys or redirect the write without monkey-patching the
        module-level function."""
        return write_csv(path, meta, *args, **kwargs)

    def _save_csv(self, run_status: str = "completed",
                   failure_reason: str = ""):
        path = self._make_csv_path(self._save_folder)
        meta = {
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Instrument": self.lblIdn.text(),
            "VISA": (self.cmbVisa.currentText().split()[0]
                     if self.cmbVisa.currentText().split() else ""),
            "V_start_V": f"{self.spnVstart.value():.6g}",
            "V_stop_V": f"{self.spnVstop.value():.6g}",
            "V_step_V": f"{self.spnVstep.value():.6g}",
            "Settle_s": f"{self.spnSettle.value():.4g}",
            "Compliance_A": f"{self.spnCompl.value()/1000:.6g}",
            "Averages": str(self.spnAvg.value()),
            "Bidirectional": str(self.chkBidir.isChecked()),
            "Points": str(len(self._v_soll)),
            "Run_Status": run_status,
        }
        if failure_reason:
            meta["Failure_Reason"] = failure_reason
        # Acquisition method tag — resolved through the hook so V1 and
        # V2 share the same source of truth and LP-level subclasses
        # can override it without monkey-patching.
        meta["Method"] = self._csv_dataset_method()
        try:
            self._write_csv(path, meta, self._v_soll, self._i_mean,
                            self._i_std, self._v_ist,
                            self._directions, self._compliance)
            # Remember the written path so the Analyze path can place
            # the options sidecar next to it.  Cleared on reload via
            # load_csv_dataset so stale references don't leak.
            self._last_csv_path = path
            append_log(self, f"Saved: {path.name}", "ok")
        except Exception as exc:
            append_log(self, f"CSV save failed: {exc}", "error")

    # ── CSV reload ─────────────────────────────────────────────────

    @staticmethod
    def parse_csv_dataset(path):
        """Parse a CSV produced by :func:`write_csv` and return a
        tuple ``(meta, V_soll, V_ist, I_mean, I_std, directions,
        compliance)``.  Header lines starting with ``#`` are parsed
        into the meta dict; the first non-comment, non-numeric line
        is the column-name row and is skipped."""
        from pathlib import Path
        meta = {}
        rows = []
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    payload = s[1:].strip()
                    if ":" in payload:
                        k, v = payload.split(":", 1)
                        meta[k.strip()] = v.strip()
                    continue
                # Skip a column-name row if the first field isn't
                # numeric (defensive — covers both "V_soll_V,..." and
                # plain numeric data files).
                first = s.split(",", 1)[0].strip()
                try:
                    float(first)
                except ValueError:
                    continue
                rows.append([p.strip() for p in s.split(",")])
        v_soll, v_ist, i_mean, i_std = [], [], [], []
        directions, compliance = [], []
        for r in rows:
            if len(r) < 4:
                continue
            v_soll.append(float(r[0]))
            v_ist.append(float(r[1]))
            i_mean.append(float(r[2]))
            i_std.append(float(r[3]))
            directions.append(r[4] if len(r) > 4 else "")
            if len(r) > 5:
                compliance.append(r[5].lower() in ("true", "1", "yes"))
            else:
                compliance.append(False)
        return meta, v_soll, v_ist, i_mean, i_std, directions, compliance

    def load_csv_dataset(self, path) -> dict:
        """Load a CSV produced by :meth:`_save_csv` into the in-memory
        sweep buffers so the existing Analyze pipeline can run against
        it.  Returns the parsed ``meta`` dict so callers can inspect
        the ``Method`` tag (or any other header field).

        Also updates ``self._last_csv_path`` so a subsequent Analyze
        places the options sidecar next to the loaded CSV.
        """
        meta, v_soll, v_ist, i_mean, i_std, directions, compliance = \
            self.parse_csv_dataset(path)
        for buf in (self._v_soll, self._v_ist, self._i_mean,
                    self._i_std, self._directions, self._compliance):
            buf.clear()
        self._v_soll.extend(v_soll)
        self._v_ist.extend(v_ist)
        self._i_mean.extend(i_mean)
        self._i_std.extend(i_std)
        self._directions.extend(directions)
        self._compliance.extend(compliance)
        from pathlib import Path as _P
        self._last_csv_path = _P(path)
        return meta

    # ── config ────────────────────────────────────────────────────

    def get_config(self) -> dict:
        """Return current settings as a serialisable dict."""
        return {
            "v_start": self.spnVstart.value(),
            "v_stop": self.spnVstop.value(),
            "v_step": self.spnVstep.value(),
            "settle_s": self.spnSettle.value(),
            "averages": self.spnAvg.value(),
            "compliance_mA": self.spnCompl.value(),
            "bidirectional": self.chkBidir.isChecked(),
            "simulation": self.chkSim.isChecked(),
            "save_folder": str(self._save_folder),
        }

    def apply_config(self, cfg: dict) -> None:
        """Apply a config dict to the GUI widgets."""
        self.spnVstart.setValue(cfg.get("v_start", self.spnVstart.value()))
        self.spnVstop.setValue(cfg.get("v_stop", self.spnVstop.value()))
        self.spnVstep.setValue(cfg.get("v_step", self.spnVstep.value()))
        self.spnSettle.setValue(cfg.get("settle_s", self.spnSettle.value()))
        self.spnAvg.setValue(cfg.get("averages", self.spnAvg.value()))
        self.spnCompl.setValue(cfg.get("compliance_mA", self.spnCompl.value()))
        self.chkBidir.setChecked(cfg.get("bidirectional", False))
        self.chkSim.setChecked(cfg.get("simulation", False))
        folder = cfg.get("save_folder")
        if folder:
            self._save_folder = Path(folder)
            self.lblFolder.setText(str(self._save_folder))

    def _save_config(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "Save Config", "dlp_config.json", "JSON (*.json)")
        if not p:
            return
        try:
            Path(p).write_text(
                json.dumps(self.get_config(), indent=2), encoding="utf-8")
            append_log(self, f"Config saved: {Path(p).name}", "ok")
        except Exception as exc:
            append_log(self, f"Config save failed: {exc}", "error")

    def _load_config(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Load Config", "", "JSON (*.json)")
        if not p:
            return
        try:
            cfg = json.loads(Path(p).read_text(encoding="utf-8"))
            self.apply_config(cfg)
            append_log(self, f"Config loaded: {Path(p).name}", "ok")
        except Exception as exc:
            append_log(self, f"Config load failed: {exc}", "error")

    # ── cleanup ───────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker:
            self._worker.request_stop()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        if self.smu:
            self.smu.close()
        super().closeEvent(event)


# ── main ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    app = QApplication(sys.argv)
    win = DLPMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
