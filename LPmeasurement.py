"""LP-Measurement main window — entry point of the application.

This module is the new home of the main Langmuir-Probe-Measurement
GUI (formerly ``DoubleLangmuir_measure_v3.DLPMainWindowV3``).  It
inherits the V2 sweep window, adds the Keithley 2000 multimeter
panel, the methods band, and opens the dedicated LP measurement
sub-window (now in :mod:`dlp_lp_window`) on click.

Start the application via::

    python LPmeasurement.py

``DoubleLangmuir_measure_v3.py`` remains as a thin compatibility
shim so existing imports / tests keep working unchanged.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from DoubleLangmuir_measure_v2 import (
    DLPMainWindowV2,
    _ensure_valid_app_font,
)
from dlp_instrument_dialog import apply_instrument_options
from keithley_2000 import (
    BAUD_RATES,
    DEFAULT_BAUD,
    DEFAULT_SERIAL_PORT,
    Keithley2000DMM,
    TRANSPORTS,
)
from fake_keithley_2000 import FakeKeithley2000
from utils import append_log, set_led

log = logging.getLogger(__name__)

#: Default GPIB resource for the K2000 on the JLU-IPI bench.
DEFAULT_K2000_VISA = Keithley2000DMM.DEFAULT_VISA  # "GPIB0::9::INSTR"


def _format_compact_double(fit_dict, plasma_dict, comparison_list,
                             *, compliance_info: dict | None = None) -> str:
    """Compact one-block summary of the Double-probe analysis.

    Replaces V2's ~25-line verbose block + 8-line model comparison
    with a single ~6-line table plus a one-line-per-model footer
    (only emitted if more than one model was actually compared).
    Preserves T_e, I_sat, model name, fit grade, and n_i/Bohm —
    the wider parameter-detail dump is intentionally dropped.

    When ``fit_dict`` carries a non-``OK`` ``fit_status`` (see
    :class:`dlp_fit_models.FitStatus`) the block is topped with a
    prominent "Status" row so the operator cannot confuse a failed
    fit with a merely weak one.  ``compliance_info`` — when present
    and flagged — renders an additional "Compliance" row below the
    numbers, carrying the provenance of any clipped-point handling.
    When a 95 % CI was computed, a second-line CI hint is appended
    under the T_e row so the number is never shown without its
    uncertainty context.
    """
    if fit_dict is None:
        return ""
    import html as _html
    from dlp_fit_models import FitStatus, FAILURE_STATUSES

    def _is_nan(x) -> bool:
        try:
            return isinstance(x, float) and x != x
        except Exception:
            return False

    te = fit_dict.get("Te_eV")
    te_err = fit_dict.get("Te_err_eV")
    isat = fit_dict.get("I_sat_fit_A")
    model = fit_dict.get("label", fit_dict.get("model_key", "?"))
    r2 = fit_dict.get("R2")
    nrmse = fit_dict.get("NRMSE")
    grade = fit_dict.get("grade", "?")
    grade_color = fit_dict.get("grade_color", "#888")

    rows = []

    # ── Fit-status banner ─────────────────────────────────────────
    # A non-OK status is surfaced as the first row with a coloured
    # label so the operator cannot mistake a failed fit for a merely
    # poor one.  OK fits keep the original, compact layout.
    fit_status = fit_dict.get("fit_status", FitStatus.OK)
    if fit_status != FitStatus.OK:
        reason = (fit_dict.get("fit_error_reason")
                  or fit_dict.get("fit_warning_reason"))
        if fit_status in FAILURE_STATUSES:
            banner_color = "#f06060"  # red — fit untrustworthy
            banner_label = "Fit failed"
        else:  # POOR / WARNING — converged, flag only
            banner_color = "#e0b050"
            banner_label = "Fit warning"
        status_cell = (f"<span style='color:{banner_color};"
                       f" font-weight:600;'>{banner_label}: "
                       f"{_html.escape(str(fit_status))}</span>")
        if reason:
            status_cell += (f"<br/><span style='color:#aab;"
                            f" font-size:11px;'>"
                            f"{_html.escape(str(reason))}</span>")
        rows.append(f"<tr><td><b>Status</b></td><td>{status_cell}</td></tr>")

    if te is not None and not _is_nan(te) and te_err is not None and not _is_nan(te_err):
        te_html = f"{te:.3f} &#177; {te_err:.3f} eV"
    elif te is not None and not _is_nan(te):
        te_html = f"{te:.3f} eV"
    else:
        te_html = "<span style='color:#aa6'>n/a</span>"
    # Attach the 95 % CI under the T_e line so the number is never
    # presented without uncertainty context.  "covariance" is the
    # always-on asymptotic form; "bootstrap" is the stronger opt-in.
    ci_lo = fit_dict.get("Te_ci95_lo_eV")
    ci_hi = fit_dict.get("Te_ci95_hi_eV")
    ci_method = fit_dict.get("Te_ci_method", "unavailable")
    if (ci_lo is not None and ci_hi is not None
            and not _is_nan(ci_lo) and not _is_nan(ci_hi)):
        label = ("bootstrap 95% CI"
                 if ci_method == "bootstrap"
                 else "95% CI (\u00b1z\u00b7\u03c3)")
        te_html += (f"<br/><span style='color:#889;font-size:10px;'>"
                    f"{label}: [{ci_lo:.3f}, {ci_hi:.3f}] eV</span>")
    elif ci_method == "unavailable" and te is not None and not _is_nan(te):
        te_html += ("<br/><span style='color:#988;font-size:10px;'>"
                    "95% CI: unavailable</span>")
    rows.append(f"<tr><td><b>T_e</b></td><td>{te_html}</td></tr>")

    if isat is not None and not _is_nan(isat):
        isat_html = f"{isat * 1e3:.3f} mA"
    else:
        isat_html = "<span style='color:#aa6'>n/a</span>"
    # I_sat 95 % CI from the fit covariance — same honest conventions
    # as the T_e CI row above.
    isat_lo = fit_dict.get("I_sat_ci95_lo_A")
    isat_hi = fit_dict.get("I_sat_ci95_hi_A")
    isat_method = fit_dict.get("I_sat_ci_method", "unavailable")
    if (isat_lo is not None and isat_hi is not None
            and not _is_nan(isat_lo) and not _is_nan(isat_hi)):
        isat_html += (f"<br/><span style='color:#889;font-size:10px;'>"
                      f"95% CI: [{isat_lo * 1e3:.3f}, "
                      f"{isat_hi * 1e3:.3f}] mA</span>")
    elif isat_method == "unavailable" and isat is not None \
            and not _is_nan(isat):
        isat_html += ("<br/><span style='color:#988;font-size:10px;'>"
                      "95% CI: unavailable</span>")
    rows.append(f"<tr><td><b>I_sat</b></td><td>{isat_html}</td></tr>")

    rows.append(f"<tr><td><b>Model</b></td><td>{model}</td></tr>")

    fit_bits = []
    if r2 is not None and not _is_nan(r2):
        fit_bits.append(f"R&#178;={r2:.3f}")
    if nrmse is not None and not _is_nan(nrmse):
        fit_bits.append(f"NRMSE={nrmse:.1%}")
    fit_bits.append(f"<span style='color:{grade_color}'>[{grade}]</span>")
    rows.append(f"<tr><td><b>Fit</b></td><td>{', '.join(fit_bits)}</td></tr>")

    if plasma_dict:
        n_i = plasma_dict.get("n_i_m3")
        gas = plasma_dict.get("ion_label", "")
        if n_i is not None and not _is_nan(n_i):
            tag = (f"<span style='color:#888'>(Bohm"
                   f"{', ' + gas if gas else ''})</span>")
            n_i_html = f"{n_i:.3e} m^-3 {tag}"
            # n_i 95 % CI — label honestly by scope.  The note is
            # "fit_only" / "fit+area" / "fit+mass" / "fit+area+mass"
            # depending on which uncertainty inputs the operator
            # supplied in the Double options dialog.  Probe area
            # and ion mass are treated as exact *only* when the
            # label literally says "fit_only".
            n_lo = plasma_dict.get("n_i_ci95_lo_m3")
            n_hi = plasma_dict.get("n_i_ci95_hi_m3")
            n_method = plasma_dict.get("n_i_ci_method", "unavailable")
            n_note = plasma_dict.get("n_i_ci_note", "fit_only")
            n_label = n_note.replace("_", "-")
            if (n_method != "unavailable"
                    and n_lo is not None and n_hi is not None
                    and not _is_nan(n_lo) and not _is_nan(n_hi)):
                n_i_html += (
                    f"<br/><span style='color:#889;font-size:10px;'>"
                    f"95% CI ({n_label}): "
                    f"[{n_lo:.3e}, {n_hi:.3e}] m^-3</span>")
            elif n_method == "unavailable":
                n_i_html += ("<br/><span style='color:#988;"
                              "font-size:10px;'>n_i 95% CI: unavailable"
                              "</span>")
            rows.append(f"<tr><td><b>n_i</b></td><td>"
                        f"{n_i_html}</td></tr>")

    # Compliance / clipping provenance — always rendered when any
    # point was flagged so the displayed T_e / I_sat carry their
    # data-quality context on the same screen.
    if compliance_info and int(compliance_info.get("n_flagged", 0)) > 0:
        n_fl = int(compliance_info["n_flagged"])
        n_to = int(compliance_info.get("n_total", 0))
        frac = float(compliance_info.get("clipped_fraction", 0.0))
        action = compliance_info.get("action", "n/a")
        source = compliance_info.get("source", "operator_provided")
        suspected = source == "heuristic_suspected"
        label = "suspected clipping" if suspected else "clipped"
        if action == "excluded_from_fit":
            comp_color = "#bb8"
            comp_text = (f"{n_fl}/{n_to} {label} point(s) excluded "
                         f"from fit ({frac:.1%})")
        elif action == "retained_in_fit":
            comp_color = "#e0b050"
            comp_text = (f"{n_fl}/{n_to} {label} point(s) retained "
                         f"in fit ({frac:.1%}) — may bias T_e")
        else:
            comp_color = "#888"
            comp_text = f"{n_fl}/{n_to} {label} point(s) ({frac:.1%})"
        if suspected:
            comp_text += (" <span style='color:#888;font-size:10px;'>"
                          "(legacy heuristic)</span>")
        rows.append(f"<tr><td><b>Compliance</b></td><td>"
                    f"<span style='color:{comp_color}'>{comp_text}"
                    f"</span></td></tr>")

    cmp_html = ""
    if comparison_list and len(comparison_list) > 1:
        active_key = fit_dict.get("model_key")
        cmp_rows = []
        for entry in comparison_list:
            marker = ("&#9654;" if entry.get("model_key") == active_key
                      else "&nbsp;&nbsp;")
            label = entry.get("label", entry.get("model_key", "?"))
            te_e = entry.get("Te_eV"); r2_e = entry.get("R2")
            te_str = (f"{te_e:.2f} eV" if te_e is not None
                      and not _is_nan(te_e) else "&#8212;")
            r2_str = (f"R&#178;={r2_e:.3f}" if r2_e is not None
                      and not _is_nan(r2_e) else "")
            color = entry.get("grade_color", "#888")
            cmp_rows.append(
                f"<tr><td>{marker}</td><td>{label}</td>"
                f"<td>{te_str}</td>"
                f"<td><span style='color:{color}'>{r2_str}</span></td></tr>")
        cmp_html = (f"<div style='font-family:Consolas, monospace; "
                    f"font-size:10px; margin-top:6px; color:#aac;'>"
                    f"<b>Models:</b><table>"
                    f"{''.join(cmp_rows)}</table></div>")

    return (f"<div style='border:1px solid #58a; padding:8px; "
            f"margin:8px 0; background:#222;'>"
            f"<h3 style='color:#58a; margin:0 0 6px 0;'>"
            f"Double-Probe Analysis</h3>"
            f"<table style='font-family:Consolas, monospace;'>"
            f"{''.join(rows)}</table>"
            f"{cmp_html}</div>")


class LPMainWindow(DLPMainWindowV2):
    """Main window of the Langmuir-Probe Measurement application.

    Successor of the former ``DLPMainWindowV3`` — same behaviour,
    new home (:mod:`LPmeasurement`).  An alias is kept in
    :mod:`DoubleLangmuir_measure_v3` for backwards compatibility.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Langmuir Probe Measurement")

        self.k2000 = None  # Keithley2000DMM | FakeKeithley2000 | None
        grp_k2000 = self._build_k2000_groupbox()
        if grp_k2000 is not None:
            self._build_three_column_layout(grp_k2000)

        # Build the top menu bar.  Kept separate from the group-box
        # construction so adding / removing menu entries never risks
        # disturbing the main column layout.
        self._build_menu_bar()

        # Preload the K2000 VISA field with the last-successful
        # resource if the operator has one cached.  This is a soft
        # preload — the default K2000 VISA stays in place if the
        # cache is empty, and a later Connect success updates it.
        self._restore_k2000_last_successful()

        # Default method = Double (the historic primary workflow:
        # double-probe sweep, floating output, remote sense ON).
        # _apply_method_mode runs via the QButtonGroup's
        # buttonToggled signal that we wired in _build_methods_group.
        try:
            self.btnMethodDouble.setChecked(True)
        except Exception:
            pass

        # ── Wrong-method guard ─────────────────────────────────────
        # Snapshot the active method on every Start so the Analyze
        # dispatcher can refuse to run double-fit logic on
        # single-probe data (or vice versa) after a method swap.
        self._dataset_method: str | None = None
        self._last_single_analysis: dict | None = None
        try:
            self.btnStart.clicked.connect(self._stamp_dataset_method)
        except Exception:
            pass
        # Re-route the Analyze button through our method-aware
        # dispatcher.  V2 connected ``_run_analysis`` directly; we
        # disconnect every existing slot first (single connection in
        # V2's __init__, so that's safe) and reconnect the dispatcher.
        try:
            self.btnAnalyze.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.btnAnalyze.clicked.connect(self._run_analysis_dispatch)

        # Per-method analysis options.  Each method's dialog now
        # governs only its own analysis path — the previous
        # situation where SingleAnalysisOptions silently controlled
        # Double behaviour was the convergence pass's main UX fix.
        from dlp_single_options import SingleAnalysisOptions
        from dlp_double_options import DoubleAnalysisOptions
        self._single_analysis_options: SingleAnalysisOptions = \
            SingleAnalysisOptions()
        self._double_analysis_options: DoubleAnalysisOptions = \
            DoubleAnalysisOptions()
        # Re-route Fit Model… so it dispatches by method: Single
        # opens Single options, Double keeps the existing dialog,
        # Triple shows a clear info dialog.
        try:
            self.btnFitModel.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.btnFitModel.clicked.connect(self._open_fit_model_dispatch)
        try:
            self.btnFitModel.setToolTip(
                "Open analysis settings for the active method "
                "(Single → Single options; Double → fit-model "
                "selection; Triple → no fit, closed-form math).")
        except Exception:
            pass

        # ── CSV: inject Method tag into V2's save path ─────────────
        # V2 overrides _save_csv and builds its own meta dict; the
        # only seam that doesn't touch V2 is to wrap the
        # ``write_csv`` reference inside the V2 module so V2's call
        # routes through our wrapper.  The wrapper installs ONCE
        # (idempotent) and reads the dataset method from a class-
        # level registry (weak refs) rather than capturing ``self``,
        # so test instances can be GC'd cleanly.
        LPMainWindow._install_csv_method_wrapper()
        LPMainWindow._csv_method_register(self)

        # Mid-sweep method-button lock — see _set_sweep_ui override
        # below.  We deliberately do NOT wrap via instance attribute
        # assignment with a closure over self (causes a PySide6 GC
        # cycle on Windows) — a regular method override that
        # delegates via super() avoids the cycle entirely.

        # ── Single-probe plot overlays ─────────────────────────────
        # axvline objects for V_f and V_p, cleared on every Start.
        self._single_overlay_lines: list = []
        try:
            self.btnStart.clicked.connect(self._clear_single_overlays)
        except Exception:
            pass

        # ── Load CSV + Migrate-Legacy buttons (next to Plot…) ──────
        # Adds two small action buttons to the existing plot-header
        # row: Load CSV… for reloading a saved sweep, and Migrate
        # Legacy Data… for the one-time house-keeping of moving
        # historical data out of the old "double_langmuir" base
        # folder into the new "lp_measurements" base folder.
        try:
            self._install_load_csv_button()
        except Exception as exc:
            log.warning("Load-CSV button install failed: %s", exc)
        try:
            self._install_migrate_legacy_button()
        except Exception as exc:
            log.warning("Migrate-Legacy button install failed: %s", exc)
        # Non-invasive startup hint when legacy data is still around.
        try:
            self._announce_legacy_data_if_present()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_k2000_groupbox(self):
        """Build the 'Multimeter (Keithley 2000)' QGroupBox in isolation.

        The group is returned (not inserted) so the caller can place
        it in whatever column the layout strategy demands.  V3 puts
        it at the top of the new third column; future variants could
        place it elsewhere without touching this builder.
        """
        grp = QGroupBox("Multimeter (Keithley 2000)")
        gv = QVBoxLayout(grp)

        # Transport selector + transport-specific stack
        row_tr = QHBoxLayout()
        row_tr.addWidget(QLabel("Transport:"))
        self.cmbK2000Transport = QComboBox()
        for t in TRANSPORTS:
            self.cmbK2000Transport.addItem(t)
        self.cmbK2000Transport.setToolTip(
            "Connection type to the K2000.\n"
            "GPIB: standard VISA resource (default GPIB0::9::INSTR).\n"
            "RS232: serial line — requires the front-panel RS232 "
            "switch on the K2000 to be enabled.")
        row_tr.addWidget(self.cmbK2000Transport, 1)
        gv.addLayout(row_tr)

        self.stackK2000Transport = QStackedWidget()
        gv.addWidget(self.stackK2000Transport)

        # GPIB page
        gpib_page = QWidget()
        gpib_layout = QHBoxLayout(gpib_page)
        gpib_layout.setContentsMargins(0, 0, 0, 0)
        gpib_layout.addWidget(QLabel("VISA:"))
        self.editK2000Visa = QLineEdit(DEFAULT_K2000_VISA)
        self.editK2000Visa.setToolTip(
            "VISA resource string for the Keithley 2000 DMM.\n"
            "Default for the JLU-IPI bench: GPIB0::9::INSTR")
        gpib_layout.addWidget(self.editK2000Visa, 1)
        self.stackK2000Transport.addWidget(gpib_page)

        # RS232 page
        rs232_page = QWidget()
        rs232_layout = QHBoxLayout(rs232_page)
        rs232_layout.setContentsMargins(0, 0, 0, 0)
        rs232_layout.addWidget(QLabel("Port:"))
        self.editK2000Port = QLineEdit(DEFAULT_SERIAL_PORT)
        self.editK2000Port.setToolTip(
            "Serial port for the K2000 (e.g. COM3).  Will be "
            "translated to ASRL{N}::INSTR for PyVISA.")
        rs232_layout.addWidget(self.editK2000Port, 1)
        rs232_layout.addWidget(QLabel("Baud:"))
        self.cmbK2000Baud = QComboBox()
        for b in BAUD_RATES:
            self.cmbK2000Baud.addItem(str(b), b)
        self.cmbK2000Baud.setCurrentText(str(DEFAULT_BAUD))
        self.cmbK2000Baud.setToolTip(
            "Baud rate.  Default 9600 matches the K2000 power-up "
            "default; raise only if the front-panel setting is changed.")
        rs232_layout.addWidget(self.cmbK2000Baud)
        self.stackK2000Transport.addWidget(rs232_page)

        # Wire combo → stack (and start on GPIB).
        self.cmbK2000Transport.currentIndexChanged.connect(
            self.stackK2000Transport.setCurrentIndex)
        self.stackK2000Transport.setCurrentIndex(0)

        # Sim + Connect row + LED
        row2 = QHBoxLayout()
        self.chkK2000Sim = QCheckBox("Sim")
        self.chkK2000Sim.setToolTip(
            "Use a simulated K2000 (FakeKeithley2000) instead of GPIB "
            "hardware. Useful for offline GUI work.")
        row2.addWidget(self.chkK2000Sim)
        self.btnK2000Connect = QPushButton("Connect")
        self.btnK2000Connect.clicked.connect(self._toggle_k2000_connect)
        row2.addWidget(self.btnK2000Connect)
        self.ledK2000 = QFrame()
        self.ledK2000.setFixedSize(16, 16)
        set_led(self.ledK2000, self._theme["led_grey"])
        row2.addWidget(self.ledK2000)
        gv.addLayout(row2)

        # IDN line (small grey font, matches the rest of the dialog hints)
        self.lblK2000Idn = QLabel("")
        self.lblK2000Idn.setStyleSheet("color: #8890a0; font-size: 10px;")
        self.lblK2000Idn.setWordWrap(True)
        gv.addWidget(self.lblK2000Idn)

        # Read row
        row3 = QHBoxLayout()
        self.btnK2000Read = QPushButton("Read voltage")
        self.btnK2000Read.setToolTip(
            "Trigger one DC-voltage measurement on the K2000 and "
            "display the result.")
        self.btnK2000Read.setEnabled(False)
        self.btnK2000Read.clicked.connect(self._read_k2000_voltage)
        row3.addWidget(self.btnK2000Read)
        self.lblK2000Value = QLabel("\u2014 V")
        self.lblK2000Value.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13pt;")
        row3.addWidget(self.lblK2000Value, 1)
        gv.addLayout(row3)

        # Options row — dedicated "K2000 Options..." button mirrors
        # the SMU's "Instrument..." button.  Opens the autorange /
        # fixed-range / NPLC dialog; applying the returned options
        # is an idempotent operation on a connected instrument, and
        # a stored-for-next-connect operation otherwise.
        row4 = QHBoxLayout()
        self.btnK2000Options = QPushButton("K2000 Options\u2026")
        self.btnK2000Options.setToolTip(
            "Configure autorange, fixed voltage range, and NPLC "
            "integration time for the Keithley 2000.\n"
            "Useful for Triple-probe live measurement: turning "
            "autorange off plus a fixed range removes range-switch "
            "latency and occasional glitches.")
        self.btnK2000Options.clicked.connect(self._open_k2000_options)
        row4.addWidget(self.btnK2000Options)
        row4.addStretch(1)
        gv.addLayout(row4)

        # Seed the in-memory K2000 options dataclass at window build
        # time so the dialog has a stable object to edit / return.
        from dlp_k2000_options import K2000Options
        self._k2000_options = K2000Options()

        return grp

    def _build_three_column_layout(self, grp_k2000) -> None:
        """Re-organise the inherited two-column layout into three columns.

        Target structure:
          * column 1 — left controls (Output remains the last group),
          * column 2 — plot canvas alone,
          * column 3 — Keithley 2000 group on top, log view below
            (held together by a vertical splitter so the operator can
            adjust the K2000-vs-log ratio with the mouse).
        """
        splitter = getattr(self, "_splitter_main", None)
        right_split = getattr(self, "_splitter_right", None)
        if splitter is None or right_split is None:
            log.warning("V3: splitter not available — keeping V2 layout.")
            return

        plot_container = right_split.widget(0)
        log_widget = self.txtLog
        if plot_container is None or log_widget is None:
            log.warning("V3: plot/log not available — keeping V2 layout.")
            return

        # Detach plot + log from the existing right vertical splitter.
        plot_container.setParent(None)
        log_widget.setParent(None)
        # Drop the (now empty) right splitter from the main one.
        right_split.setParent(None)
        right_split.deleteLater()
        self._splitter_right = None

        # Slot a small header row above the plot canvas with a single
        # "Plot…" button that opens a dedicated settings dialog.  Keeps
        # the main window uncluttered while still exposing axes / grid
        # / legend controls.
        plot_layout = plot_container.layout()
        if plot_layout is not None:
            header = QHBoxLayout()
            header.setContentsMargins(2, 2, 2, 2)
            header.setSpacing(4)
            header.addStretch(1)
            self.btnPlotSettings = QPushButton("Plot\u2026")
            self.btnPlotSettings.setToolTip(
                "Open the plot settings dialog (axes range, grid, "
                "legend, reset view).")
            self.btnPlotSettings.setMaximumWidth(80)
            self.btnPlotSettings.clicked.connect(self._open_plot_settings)
            header.addWidget(self.btnPlotSettings)
            plot_layout.insertLayout(0, header)

        # Column 2: plot on top, "Langmuir Probe Methods" group below.
        col2 = QWidget()
        col2_layout = QVBoxLayout(col2)
        col2_layout.setContentsMargins(0, 0, 0, 0)
        col2_layout.setSpacing(4)
        col2_layout.addWidget(plot_container, 1)
        col2_layout.addWidget(self._build_methods_group(), 0)
        splitter.addWidget(col2)
        self._col2_container = col2

        # Column 3: vertical splitter — K2000 on top, log (with a
        # small "Log" heading) below.
        third = QSplitter(Qt.Orientation.Vertical)
        third.setObjectName("splitThird")
        third.setChildrenCollapsible(False)
        third.addWidget(grp_k2000)

        log_box = QWidget()
        log_v = QVBoxLayout(log_box)
        log_v.setContentsMargins(0, 0, 0, 0)
        log_v.setSpacing(2)
        self.lblLog = QLabel("Log")
        self.lblLog.setStyleSheet("font-weight: bold;")
        log_v.addWidget(self.lblLog)
        log_v.addWidget(log_widget, 1)
        third.addWidget(log_box)
        self._log_box = log_box
        third.setStretchFactor(0, 0)
        third.setStretchFactor(1, 1)
        splitter.addWidget(third)
        self._splitter_third = third

        # Stretch policy: controls slim, plot dominant, K2000+log
        # somewhat wider than before so long log lines stay readable.
        # The left controls column is now seeded narrow (280 px) since
        # the VISA combo no longer carries the IDN string and the IDN
        # label is word-wrapped.  Three-column ratio 1 : 4 : 3
        # (~14 % / 50 % / 36 %) — the middle plot area dominates,
        # the K2000+log column keeps room for long log lines, and the
        # left controls column does not waste pixels on whitespace.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([280, 800, 540])
        # Inside the third column, give the log the larger share by
        # default — K2000 panel is small, log benefits from height.
        third.setSizes([180, 340])

    def _build_methods_group(self) -> "QGroupBox":
        """Build the 'Langmuir Probe Methods' selector that sits below
        the plot.  Iteration scope: structural only — buttons are
        exposed as attributes but carry no measurement logic yet.  A
        future iteration will wire them to actual parameter presets.
        """
        grp = QGroupBox("Langmuir Probe Methods")
        # Subtle "active mode" highlight on checked buttons — accent
        # border + slightly lifted background, fits the dark theme.
        grp.setStyleSheet(
            "QGroupBox QPushButton:checked { "
            " background-color: #2e3a55; "
            " border: 1px solid #4f8ef7; "
            " color: #ffffff; "
            "}")
        row = QHBoxLayout(grp)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        self.btnMethodSingle = QPushButton("Single")
        self.btnMethodDouble = QPushButton("Double")
        self.btnMethodTriple = QPushButton("Triple")
        self.btnMethodCleaning = QPushButton("Cleaning")

        # Mode buttons (exclusive, checkable) vs. action buttons.
        for b in (self.btnMethodSingle, self.btnMethodDouble,
                  self.btnMethodTriple):
            b.setCheckable(True)
        self.methodGroup = QButtonGroup(grp)
        self.methodGroup.setExclusive(True)
        for b in (self.btnMethodSingle, self.btnMethodDouble,
                  self.btnMethodTriple):
            self.methodGroup.addButton(b)
        # Mode → instrument defaults are applied centrally on toggle.
        self.methodGroup.buttonToggled.connect(
            self._on_method_button_toggled)

        method_buttons = (
            (self.btnMethodSingle, "Single Langmuir probe — preset (TBD)."),
            (self.btnMethodDouble, "Double Langmuir probe — preset (TBD)."),
            (self.btnMethodTriple, "Triple Langmuir probe — preset (TBD)."),
            (self.btnMethodCleaning,
             "Open the timed probe-cleaning dialog (modal)."),
        )
        # Cleaning + Triple are wired; Single/Double remain visual-only.
        self.btnMethodCleaning.clicked.connect(self._open_cleaning_dialog)
        self.btnMethodTriple.clicked.connect(self._open_triple_window)
        for b, tip in method_buttons:
            b.setToolTip(tip)
            b.setMinimumHeight(30)
            b.setMinimumWidth(80)
            row.addWidget(b, 1)

        # Iter "widget regrouping": Probe Params… moves out of the
        # Instrument group and joins the methods band as the 5th slot
        # — the dialog directly affects probe geometry/area, so it
        # belongs next to the probe-method selectors.
        if hasattr(self, "btnProbeParams"):
            self.btnProbeParams.setMinimumHeight(30)
            self.btnProbeParams.setMinimumWidth(110)
            row.addWidget(self.btnProbeParams, 1)

        self.grpMethods = grp
        return grp

    # ------------------------------------------------------------------
    # K2000 control
    # ------------------------------------------------------------------
    @Slot()
    def _toggle_k2000_connect(self) -> None:
        """Connect or disconnect the K2000 (idempotent UI behaviour)."""
        if self.k2000 is not None:
            try:
                self.k2000.close()
            except Exception as exc:
                append_log(self, f"K2000 close error: {exc}", "warn")
            self.k2000 = None
            set_led(self.ledK2000, self._theme["led_grey"])
            self.lblK2000Idn.setText("")
            self.btnK2000Connect.setText("Connect")
            self.btnK2000Read.setEnabled(False)
            self.chkK2000Sim.setEnabled(True)
            append_log(self, "K2000 disconnected.", "info")
            return

        if self.chkK2000Sim.isChecked():
            self.k2000 = FakeKeithley2000(voltage=0.6)
            label = "Simulation"
        else:
            transport = self.cmbK2000Transport.currentText().upper()
            if transport == "RS232":
                port = self.editK2000Port.text().strip() or DEFAULT_SERIAL_PORT
                baud = int(self.cmbK2000Baud.currentData() or DEFAULT_BAUD)
                self.k2000 = Keithley2000DMM(
                    transport="RS232", port=port, baud=baud)
                label = f"RS232 {port} @ {baud}"
            else:
                visa = (self.editK2000Visa.text().strip()
                        or DEFAULT_K2000_VISA)
                self.k2000 = Keithley2000DMM(
                    transport="GPIB", visa_resource=visa)
                label = visa

        try:
            idn = self.k2000.connect()
        except Exception as exc:
            # Classify the failure so the operator sees "no VISA
            # library", "resource not found", "timeout" or similar —
            # plus a one-line remediation hint — instead of a raw
            # PyVISA exception string.
            from visa_errors import format_for_operator
            msg = format_for_operator(exc,
                                       context=f"K2000 connect ({label})")
            append_log(self, msg, "error")
            self.k2000 = None
            return

        set_led(self.ledK2000, self._theme["led_green"])
        self.lblK2000Idn.setText(idn)
        self.btnK2000Connect.setText("Disconnect")
        self.btnK2000Read.setEnabled(True)
        self.chkK2000Sim.setEnabled(False)
        append_log(self, f"K2000 connected ({label}): {idn}", "ok")
        # Record the resource that just worked so the discovery
        # window + next app launch keep it as the default.  Only the
        # real-hardware paths update the cache; sim mode does not
        # pollute the persisted resource list with a phantom entry.
        try:
            if not self.chkK2000Sim.isChecked():
                cache = getattr(self, "_visa_cache", None)
                if cache is not None:
                    transport = self.cmbK2000Transport.currentText().upper()
                    if transport == "RS232":
                        port = (self.editK2000Port.text().strip()
                                or DEFAULT_SERIAL_PORT)
                        cache.mark_successful(self.K2000_CACHE_KEY, port)
                    else:
                        visa = (self.editK2000Visa.text().strip()
                                or DEFAULT_K2000_VISA)
                        cache.mark_successful(self.K2000_CACHE_KEY, visa)
        except Exception:
            pass  # cache persistence is best-effort; connect succeeded.
        # Push the operator-chosen options to the freshly-connected
        # instrument.  Idempotent on the fake, cheap SCPI writes on
        # the real K2000.  Errors are logged as warnings, never
        # re-raised — the connection is already good at this point.
        self._apply_k2000_options_to_live()

    # ------------------------------------------------------------------
    # Menu bar + Interface Discovery
    # ------------------------------------------------------------------
    #: Device key used when persisting the K2000's last-successful
    #: resource in :class:`visa_persistence.VisaCache`.  Kept as a
    #: class constant so the discovery window + K2000 connect paths
    #: share one source of truth.
    K2000_CACHE_KEY = "k2000"

    def _build_menu_bar(self) -> None:
        """Attach a Tools menu with an Interface Discovery action.

        Using the window's native menu bar (QMainWindow.menuBar) keeps
        the entry accessible via keyboard (Alt+T, I) and via the
        shortcut Ctrl+Shift+I, which is an uncommon-enough chord that
        it will not collide with existing shortcuts bound by the V2
        base window.
        """
        from PySide6.QtGui import QAction, QKeySequence
        mbar = self.menuBar()
        # Remove any stale "Tools" entry a base class might have
        # built so re-instantiation in tests does not duplicate it.
        for act in mbar.actions():
            if act.text().replace("&", "") == "Tools":
                mbar.removeAction(act)
        tools = mbar.addMenu("&Tools")
        act = QAction("Interface Discovery\u2026", self)
        act.setShortcut(QKeySequence("Ctrl+Shift+I"))
        act.setToolTip(
            "Scan the installed VISA backend and the Windows serial "
            "ports, and probe individual resources for an *IDN? "
            "response.  Useful when you don't know whether a GPIB / "
            "USB / RS232 adapter is recognised yet.")
        act.triggered.connect(self._open_interface_discovery)
        tools.addAction(act)
        self._actDiscovery = act

    @Slot()
    def _open_interface_discovery(self) -> None:
        """Show the interface-discovery singleton window, wiring the
        Apply-to-SMU / Apply-to-K2000 callbacks back into the main
        window.  A second click reuses the existing window.
        """
        existing = getattr(self, "_discovery_window", None)
        if existing is not None and existing.is_visible():
            existing.raise_()
            existing.activateWindow()
            return
        try:
            from interface_discovery import open_interface_discovery
        except Exception as exc:  # pragma: no cover - defensive
            append_log(self,
                       f"Interface Discovery unavailable: "
                       f"{type(exc).__name__}: {exc}", "warn")
            return
        self._discovery_window = open_interface_discovery(
            parent=self,
            on_apply_smu=self._apply_discovered_smu_resource,
            on_apply_k2000=self._apply_discovered_k2000_resource,
        )

    def _apply_discovered_smu_resource(self, resource: str) -> None:
        """Callback for Interface Discovery → Use for SMU.

        Updates the SMU VISA combo box, ensures the entry exists in
        the persisted discovery list, but does NOT overwrite the
        cache's last-successful pointer — the operator still has to
        click Connect for that.  Matches the behaviour of the SMU
        Scan button.
        """
        if not resource:
            return
        try:
            cache = getattr(self, "_visa_cache", None)
            key = getattr(self, "_visa_device_key", "b2901")
            if cache is not None:
                entry = cache.get(key)
                if not any(d.get("resource") == resource
                            for d in entry.discovered):
                    entry.discovered.append(
                        {"resource": resource, "idn": ""})
                    cache.save()
                self._populate_visa_combo_from_cache()
            combo = getattr(self, "cmbVisa", None)
            if combo is not None:
                idx = combo.findData(resource)
                if idx < 0:
                    combo.addItem(resource, resource)
                    idx = combo.findData(resource)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            append_log(self,
                       f"SMU resource set from discovery: {resource} "
                       "(click Connect to make it the default).",
                       "info")
        except Exception as exc:  # pragma: no cover - defensive
            append_log(self,
                       f"Apply-to-SMU failed: "
                       f"{type(exc).__name__}: {exc}", "warn")

    def _apply_discovered_k2000_resource(self, resource: str) -> None:
        """Callback for Interface Discovery → Use for K2000.

        Sets the K2000 transport + VISA/COM field.  GPIB / USB / LAN
        VISA strings keep the GPIB transport; ``COMn`` or
        ``ASRLn::INSTR`` switches the transport to RS232 and stores
        the bare COM form in editK2000Port.  Like the SMU path this
        does NOT pin last-successful — that happens on Connect.
        """
        if not resource:
            return
        try:
            up = str(resource).strip().upper()
            if up.startswith("COM") or up.startswith("ASRL"):
                # Serial path.  Show COM form in the editor — the
                # driver translates it to ASRLn::INSTR on connect.
                com = resource
                if up.startswith("ASRL") and up.endswith("::INSTR"):
                    com = "COM" + up[len("ASRL"):-len("::INSTR")]
                idx = self.cmbK2000Transport.findText(
                    "RS232", flags=Qt.MatchFlag.MatchExactly)
                if idx < 0:
                    idx = self.cmbK2000Transport.findText("RS232")
                if idx >= 0:
                    self.cmbK2000Transport.setCurrentIndex(idx)
                self.editK2000Port.setText(com)
            else:
                # GPIB / USB / LAN VISA — stay on the GPIB transport.
                idx = self.cmbK2000Transport.findText("GPIB")
                if idx >= 0:
                    self.cmbK2000Transport.setCurrentIndex(idx)
                self.editK2000Visa.setText(resource)
            append_log(self,
                       f"K2000 resource set from discovery: "
                       f"{resource} (click Connect to make it the "
                       "default).", "info")
        except Exception as exc:  # pragma: no cover - defensive
            append_log(self,
                       f"Apply-to-K2000 failed: "
                       f"{type(exc).__name__}: {exc}", "warn")

    def _restore_k2000_last_successful(self) -> None:
        """Preload the K2000 VISA field from the persisted cache.

        No-op on first run (cache empty) and on any read error — the
        hard-coded ``DEFAULT_K2000_VISA`` is a safe fallback.  This
        is called once at window construction so a user who had a
        working K2000 address on the last session does not have to
        re-type it.
        """
        try:
            cache = getattr(self, "_visa_cache", None)
            if cache is None:
                return
            last = cache.get(self.K2000_CACHE_KEY).last_successful
            if not last:
                return
            up = str(last).strip().upper()
            if up.startswith("COM") or up.startswith("ASRL"):
                com = last
                if up.startswith("ASRL") and up.endswith("::INSTR"):
                    com = "COM" + up[len("ASRL"):-len("::INSTR")]
                self.cmbK2000Transport.setCurrentText("RS232")
                self.editK2000Port.setText(com)
            else:
                self.cmbK2000Transport.setCurrentText("GPIB")
                self.editK2000Visa.setText(last)
        except Exception:
            pass  # Never block window construction on cache I/O.

    # ------------------------------------------------------------------
    # K2000 options
    # ------------------------------------------------------------------
    @Slot()
    def _open_k2000_options(self) -> None:
        """Modal K2000-options editor.  Applies the returned options
        to the live instrument immediately when connected.  When not
        connected, the values are stored and applied on the next
        Connect click.
        """
        try:
            from dlp_k2000_options import (
                K2000Options, open_k2000_options_dialog,
            )
        except Exception as exc:  # pragma: no cover — defensive
            append_log(self,
                       f"K2000 options unavailable: {type(exc).__name__}: "
                       f"{exc}", "warn")
            return
        current = getattr(self, "_k2000_options", None) or K2000Options()
        new = open_k2000_options_dialog(current, parent=self)
        if new is None:
            return
        self._k2000_options = new
        self._apply_k2000_options_to_live()

    def _apply_k2000_options_to_live(self) -> None:
        """Best-effort application of ``self._k2000_options`` to the
        live ``self.k2000`` handle.  Silent no-op when not connected
        (the next Connect click will pick up the stored options).
        """
        if self.k2000 is None:
            return
        try:
            from dlp_k2000_options import apply_k2000_options
            msg = apply_k2000_options(self.k2000, self._k2000_options)
            if msg:
                append_log(self, msg, "info")
        except Exception as exc:  # pragma: no cover — defensive
            append_log(self,
                       f"K2000 options apply failed: "
                       f"{type(exc).__name__}: {exc}", "warn")

    # ------------------------------------------------------------------
    # Plot settings
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Triple-Probe (non-modal window)
    # ------------------------------------------------------------------
    @Slot()
    def _open_triple_window(self) -> None:
        """Open the singleton Triple-Probe window.  Requires both SMU
        and K2000 to be connected.  While Triple is running the Sweep
        Start button is disabled to prevent parallel hardware use.
        """
        if self.smu is None or self.k2000 is None:
            append_log(self,
                        "Triple aborted: SMU and K2000 must both be connected.",
                        "warn")
            return
        # Refuse to open while a sweep is currently running.
        if self.btnStop.isEnabled():
            append_log(self,
                        "Triple aborted: stop the running sweep first.",
                        "warn")
            return
        # Generalised LP-measurement window (formerly dlp_triple_window).
        # The LP measurement sub-window now lives in its own module.
        # In simulation mode we pre-tune the K2000 readout and inject
        # a sign-correct probe current so the Triple-Probe analysis
        # produces plausible demo numbers (Te ≈ 4 eV, n_e ≈ 1e17 m⁻³).
        from fake_b2901 import FakeB2901
        from fake_b2901_v2 import FakeB2901v2
        from fake_keithley_2000 import FakeKeithley2000
        if (isinstance(self.smu, (FakeB2901, FakeB2901v2))
                and isinstance(self.k2000, FakeKeithley2000)):
            try:
                # V_d13 ≈ 3 V → Te = V_d13 / ln 2 ≈ 4.33 eV.
                self.k2000.set_voltage_for_test(3.0)
            except Exception:
                pass
            self._lp_sim_current_a = -3.0e-4
        else:
            self._lp_sim_current_a = None

        # Pull gas mix from the central Experiment dialog so the
        # Triple analysis honours mixtures, not just one species.
        gas_label, mi_kg, mi_rel_unc = self._build_lp_gas_context()
        # Probe area is owned by Probe Params… — the LP window only
        # displays it (no second editable area down there).
        area_m2 = self._build_lp_probe_area_m2()
        # Build the shared ion-composition context.  This is what
        # makes Triple consume the *same* Experiment-dialog
        # settings that Single and Double already use.  The Triple
        # window writes it into its CSV header (audit trail); the
        # numerical effect on the per-tick n_e is already carried
        # by the mi_kg value above.
        exp_params = getattr(
            self, "_experiment_params", {}) or {}
        ion_ctx = {
            "ion_composition_preset": str(
                exp_params.get("ion_composition_preset", "custom")),
            "ion_composition_mode": str(
                exp_params.get("ion_composition_mode", "molecular")),
            "x_atomic": float(exp_params.get("x_atomic", 0.0)),
            "x_atomic_unc": float(
                exp_params.get("x_atomic_unc", 0.0)),
            "mi_rel_unc": float(mi_rel_unc or 0.0),
        }
        # Per-gas overrides (may be empty) — carried through so the
        # Triple CSV header's audit trail records the exact
        # per-molecular-gas assumption, not just the global fallback.
        _pg = exp_params.get("per_gas_composition") or {}
        if isinstance(_pg, dict) and _pg:
            ion_ctx["per_gas_composition"] = dict(_pg)

        from dlp_lp_window import show_or_raise as _open_lp
        win = _open_lp(self, self.smu, self.k2000,
                        sim_current_a=self._lp_sim_current_a,
                        gas_mix_label=gas_label,
                        mi_kg=mi_kg,
                        area_m2=area_m2,
                        ion_composition_context=ion_ctx)
        # Re-connect the running_changed signal (safe to disconnect first
        # in case the singleton was raised, not freshly created).
        try:
            win.running_changed.disconnect(self._on_triple_running_changed)
        except (RuntimeError, TypeError):
            pass
        win.running_changed.connect(self._on_triple_running_changed)

    # ------------------------------------------------------------------
    # Method-mode → instrument defaults
    # ------------------------------------------------------------------
    #: Default SMU configuration per method mode.
    #
    # * Single — a single probe collects current against a grounded
    #   reference; GRO is the natural output-low mode and 2-wire is
    #   sufficient because the sense-lead wiring is rarely present
    #   for this geometry.
    # * Double — the historic primary workflow: floating I-V sweep
    #   between two equally-sized tips; 4-wire keeps the sweep V
    #   accurate against probe-lead resistance.
    # * Triple — physically *requires* a floating SMU (the model
    #   breaks otherwise); 4-wire / Remote Sense gives a clean V_d12
    #   readback at the sondle wiring interface.  If the bench has
    #   no sense leads the user must manually flip Remote Sense off
    #   in Instrument Options — the dialog warns about that case.
    METHOD_MODE_DEFAULTS: dict = {
        "single": {"output_low": "GRO", "remote_sense": False},
        "double": {"output_low": "FLO", "remote_sense": True},
        "triple": {"output_low": "FLO", "remote_sense": True},
    }

    #: Per-method simulation IV model.  Single uses the asymmetric
    #: sigmoid single-probe form (negative ion saturation, dominant
    #: positive electron branch).  Double and Triple share the
    #: symmetric tanh double-Langmuir form — Triple's analysis
    #: pipeline injects its own sim_current_a at runtime so the SMU
    #: curve shape is irrelevant there.
    METHOD_MODE_SIM_MODELS: dict = {
        "single": "single_probe",
        "double": "double_langmuir",
        "triple": "double_langmuir",
    }

    @Slot(object, bool)
    def _on_method_button_toggled(self, button, checked: bool) -> None:
        """Translate a Method-button toggle into the instrument-mode
        defaults defined in :attr:`METHOD_MODE_DEFAULTS`."""
        if not checked:
            return
        mode_by_button = {
            self.btnMethodSingle: "single",
            self.btnMethodDouble: "double",
            self.btnMethodTriple: "triple",
        }
        mode = mode_by_button.get(button)
        if mode is None:
            return
        self._apply_method_mode(mode)

    def _apply_method_mode(self, mode: str) -> None:
        """Set the SMU-relevant defaults for the chosen method mode.

        Updates the persistent ``self._instrument_opts`` dict so the
        next Connect / Re-Apply uses the new defaults.  If the SMU is
        already connected the change is also pushed live via the
        existing ``apply_instrument_options`` chain so the operator
        does not have to reconnect.

        Also writes the method-specific simulation model into
        ``self._sim_options`` and (when a sim SMU is already running)
        switches the live FakeB2901v2 instance to that model so the
        next Read produces the correct IV curve without a reconnect.
        """
        defaults = self.METHOD_MODE_DEFAULTS.get(mode)
        sim_model = self.METHOD_MODE_SIM_MODELS.get(mode)
        if defaults is None or sim_model is None:
            return
        opts = getattr(self, "_instrument_opts", None)
        if not isinstance(opts, dict):
            return
        opts.update(defaults)

        # Sim-model wiring — kept separate from instrument opts since
        # it travels via the simulation-options dict (the only path
        # consumed by V2's sim-connect branch).
        sim_opts = getattr(self, "_sim_options", None)
        if isinstance(sim_opts, dict):
            sim_opts["model"] = sim_model
        from fake_b2901_v2 import FakeB2901v2
        if isinstance(self.smu, FakeB2901v2):
            self.smu.model = sim_model
            # Sheath defaults are model-specific (single_probe needs
            # a much smaller value than double_langmuir, otherwise
            # the saturation plateau gets swamped — see the
            # _DEFAULT_SHEATH_S table).  Without this resync a live
            # method swap would inherit the previous model's sheath
            # and visually break the new model's curve.
            self.smu.sheath_conductance = FakeB2901v2._DEFAULT_SHEATH_S.get(
                sim_model, 5.0e-5)

        live = self.smu is not None and hasattr(
            self.smu, "enable_output_protection")
        if live:
            try:
                apply_instrument_options(self.smu, opts)
            except Exception as exc:
                append_log(self,
                           f"Method '{mode}': live apply failed: {exc}",
                           "warn")
        append_log(self,
                   f"Method '{mode}' defaults: "
                   f"output_low={defaults['output_low']}, "
                   f"remote_sense={defaults['remote_sense']}, "
                   f"sim_model={sim_model}"
                   + ("  (applied live)" if live else "  (queued)"),
                   "ok")

    # ------------------------------------------------------------------
    # SMU connect — inject the per-method sim model
    # ------------------------------------------------------------------
    def _toggle_connect(self):
        """Override: ensure the sim path picks up the method-specific
        IV model.  V2's sim-connect branch reads ``self._sim_options``
        verbatim, so we write the active method's model in just before
        delegating.  Real-hardware connect is unaffected.
        """
        if (self.smu is None
                and getattr(self, "chkSim", None) is not None
                and self.chkSim.isChecked()):
            sim_opts = getattr(self, "_sim_options", None)
            if isinstance(sim_opts, dict):
                sim_opts["model"] = self._current_sim_model()
        super()._toggle_connect()

    def _current_sim_model(self) -> str:
        """Return the sim IV model implied by the active method button.

        Defaults to the symmetric double-Langmuir form so unknown
        states never produce the asymmetric single-probe curve by
        accident.
        """
        try:
            if self.btnMethodSingle.isChecked():
                return "single_probe"
            if self.btnMethodTriple.isChecked():
                return "double_langmuir"
        except Exception:
            pass
        return "double_langmuir"

    def _build_lp_probe_area_m2(self) -> float:
        """Resolve the probe area in m² from ``self._probe_params``.

        Honours an explicit ``electrode_area_mm2`` if the user pinned
        one; otherwise computes the geometric area from geometry +
        length + radius.  Falls back to the documented default if
        anything is missing.
        """
        from dlp_triple_analysis import DEFAULT_AREA_M2
        try:
            from dlp_probe_dialog import compute_electrode_area
            params = getattr(self, "_probe_params", None) or {}
            explicit = params.get("electrode_area_mm2")
            if explicit is not None and float(explicit) > 0:
                return float(explicit) * 1e-6
            mm2 = compute_electrode_area(
                params.get("geometry", "cylindrical"),
                float(params.get("electrode_length_mm", 0.0)),
                float(params.get("electrode_radius_mm", 0.0)),
            )
            if mm2 > 0:
                return mm2 * 1e-6
        except Exception:
            pass
        return float(DEFAULT_AREA_M2)

    def _build_lp_gas_context(self):
        """Read the gas mix from the parent Experiment dialog state
        (``self._experiment_params['gases']``) and return:

        * ``label`` — short human string like ``"Ar 0.1 + Xe 0.05 sccm"``
          or ``"Argon (Ar)"`` if nothing usable is configured;
        * ``mi_kg`` — flow-weighted mean ion mass via the existing
          ``effective_ion_mass_kg`` helper, or ``None`` for the
          Argon fallback (worker resolves ``Argon (Ar)`` then).
        """
        try:
            from dlp_experiment_dialog import (
                effective_ion_mass_kg_with_unc,
            )
            params = getattr(self, "_experiment_params", None) or {}
            gases = params.get("gases", []) or []
            entries = [(g.get("gas", ""), float(g.get("flow_sccm", 0)))
                       for g in gases
                       if g.get("gas") and float(g.get("flow_sccm", 0)) > 0]
        except Exception:
            entries = []
        if not entries:
            return "Argon (Ar)", None, 0.0
        label = " + ".join(f"{name} {flow:g}" for name, flow in entries)
        label += " sccm"
        # Forward the operator's ion-composition choice so Single
        # honours the exact same assumption as Double — previously
        # Single silently used the neutral (molecular) mass.  The
        # per-gas dict (when present) lets each molecular gas carry
        # its own regime; any gas without an entry falls back to the
        # legacy global triple.
        mode = str(params.get("ion_composition_mode", "molecular"))
        x_at = float(params.get("x_atomic", 0.0))
        x_at_unc = float(params.get("x_atomic_unc", 0.0))
        per_gas = params.get("per_gas_composition", {}) or {}
        if not isinstance(per_gas, dict):
            per_gas = {}
        try:
            mi_kg, mi_rel_unc = effective_ion_mass_kg_with_unc(
                [{"gas": n, "flow_sccm": f} for n, f in entries],
                mode=mode, x_atomic=x_at, x_atomic_unc=x_at_unc,
                per_gas_composition=per_gas)
        except Exception:
            mi_kg, mi_rel_unc = None, 0.0
        return label, mi_kg, float(mi_rel_unc)

    @Slot(bool)
    def _on_triple_running_changed(self, running: bool) -> None:
        """Mutex sweep ↔ triple: while triple runs, lock the Sweep
        Start button so the operator cannot launch a parallel sweep
        on the same SMU."""
        try:
            self.btnStart.setEnabled(not running)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cleaning method
    # ------------------------------------------------------------------
    @Slot()
    def _open_cleaning_dialog(self) -> None:
        """Open the modal probe-cleaning dialog.

        Requires a connected SMU.  In simulation mode we feed the
        dialog a fixed fake current of 0.777 A so the live readout
        looks plausible without touching the FakeB2901's I-V model.
        """
        if self.smu is None:
            append_log(self, "Cleaning aborted: SMU not connected.",
                        "warn")
            return
        from dlp_cleaning_dialog import CleaningDialog
        from fake_b2901 import FakeB2901
        from fake_b2901_v2 import FakeB2901v2

        sim_current = 0.777 if isinstance(
            self.smu, (FakeB2901, FakeB2901v2)) else None
        prev_low = str(self._instrument_opts.get("output_low", "GRO")).upper()
        dlg = CleaningDialog(
            self.smu,
            parent=self,
            sim_current_a=sim_current,
            prev_output_low=prev_low,
        )
        dlg.exec()

    @Slot()
    def _open_plot_settings(self) -> None:
        """Open the modal Plot Settings dialog and apply on Ok."""
        from dlp_plot_settings_dialog import PlotSettingsDialog
        dlg = PlotSettingsDialog(self.ax, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                dlg.apply_to_axes(self.ax)
                self.canvas.draw_idle()
            except Exception as exc:
                append_log(self, f"Plot settings apply failed: {exc}",
                            "warn")

    # ------------------------------------------------------------------
    # Method-aware Analyze dispatch
    # ------------------------------------------------------------------
    def _current_active_method(self) -> str:
        """Return ``"single"``, ``"double"``, or ``"triple"`` for the
        currently-checked method button.  Falls back to Double on
        any unexpected state."""
        try:
            if self.btnMethodSingle.isChecked():
                return "single"
            if self.btnMethodTriple.isChecked():
                return "triple"
        except Exception:
            pass
        return "double"

    @Slot()
    def _stamp_dataset_method(self) -> None:
        """Capture the active method at sweep-start time so the
        Analyze dispatcher can detect later mismatches."""
        self._dataset_method = self._current_active_method()

    @Slot()
    def _run_analysis_dispatch(self) -> None:
        """Method-aware Analyze entry point.  Refuses to run
        double-fit logic on single-probe data and vice-versa.

        Decision tree:
          * empty buffer        → delegate to V2 (it logs "Not enough data")
          * dataset method None → confirm dialog (loaded CSV / restored)
          * mismatch            → critical dialog, blocked
          * Triple              → log hint, do nothing
          * Single              → run new single-probe pipeline
          * Double              → V2 fit pipeline (unchanged)
        """
        active = self._current_active_method()
        if not self._v_ist:
            # Let V2 emit its own "Not enough data" message.
            self._run_analysis()
            return
        dataset = self._dataset_method
        if dataset is None:
            if not self._confirm_unknown_dataset_method(active):
                return
            dataset = active
            self._dataset_method = dataset
        if dataset != active:
            self._block_method_mismatch(dataset, active)
            return
        if active == "single":
            self._run_single_analysis()
            return
        if active == "triple":
            append_log(self,
                       "Triple analysis runs in the LP measurement "
                       "window (Methods → Triple), not via the main "
                       "Analyze button.", "info")
            return
        # Default branch: Double — V2's pipeline, untouched.
        self._run_analysis()

    def _confirm_unknown_dataset_method(self, active: str) -> bool:
        """Modal Yes/Cancel dialog for the loaded-CSV / restored-state
        case where the dataset has no acquisition method tag."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Dataset method unknown")
        box.setText(
            "The dataset on hand was not tagged with an acquisition "
            "method (loaded from CSV or restored from a session).\n\n"
            f"Active method: {active}\n\n"
            f"Run analysis assuming the dataset matches the active method?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _block_method_mismatch(self, dataset: str, active: str) -> None:
        """Hard block (Critical dialog + log entry) when the dataset
        method does not match the active method.  No override path —
        the user must either switch back to ``dataset`` or re-acquire."""
        from PySide6.QtWidgets import QMessageBox
        append_log(self,
                   f"Analyze blocked: dataset method '{dataset}' "
                   f"≠ active method '{active}'.", "error")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Method mismatch — analyze refused")
        box.setText(
            f"Acquisition method ({dataset}) does not match the active "
            f"method ({active}).\n\n"
            "Analyzing single-probe data with double-probe logic (or "
            "vice-versa) yields meaningless numbers.\n\n"
            "Resolve by:\n"
            f"  • switching back to {dataset} (analyze existing data), or\n"
            f"  • re-acquiring under {active}.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @Slot()
    def _run_single_analysis(self) -> None:
        """Run the dedicated single-probe pipeline on the current
        sweep buffer and surface results in the same log/HTML path
        that the Double analysis uses."""
        import numpy as np
        if len(self._v_ist) < 10:
            append_log(self, "Not enough data for analysis.", "warn")
            return
        V = np.array(self._v_ist, dtype=float)
        I = np.array(self._i_mean, dtype=float)
        # Compliance + direction buffers travel into the analysis
        # so clipped points are excluded from fits and bidirectional
        # sweeps get a hysteresis check.  Both are silently no-ops
        # if the buffers are empty or length-mismatched.
        compliance = list(self._compliance) if self._compliance else None
        directions = list(self._directions) if self._directions else None
        area_m2 = self._build_lp_probe_area_m2()
        gas_label, m_i_kg, m_i_rel_unc = self._build_lp_gas_context()
        from dlp_single_analysis import (analyze_single_iv,
                                          format_single_result_html)
        from DoubleLangmuir_measure_v2 import _append_html_block
        opts = self._single_analysis_options
        # Compliance-mode gate: "include_all" honours operator's
        # explicit choice not to filter — the legacy pre-hardening
        # path.  "exclude_clipped" is the (transparent) default.
        comp_for_call = (compliance
                          if opts.compliance_mode == "exclude_clipped"
                          else None)
        result = analyze_single_iv(
            V, I, area_m2=area_m2, m_i_kg=m_i_kg, gas_label=gas_label,
            compliance=comp_for_call, directions=directions,
            robust_te_fit=opts.robust_te_fit,
            te_window_factor=opts.te_window_factor,
            hysteresis_threshold_pct=opts.hysteresis_threshold_pct,
            bootstrap_enabled=opts.bootstrap_enabled,
            bootstrap_n_iters=opts.bootstrap_n_iters,
            v_p_method=getattr(opts, "v_p_method", "auto"),
            # Forward the ion-mass relative uncertainty coming from
            # the Experiment-dialog ion-composition mode (0.0 for
            # molecular / atomic, >0 for mixed / unknown on a gas
            # with an atomic-ion entry).  Single now carries the
            # same n_e CI scope-tag taxonomy as Double.
            m_i_rel_unc=float(m_i_rel_unc))
        self._last_single_analysis = result
        if result["ok"]:
            te = result["te_eV"]; vf = result["v_float_V"]
            ne = result["n_e_m3"]
            ne_str = (f"n_e={ne:.2e} m^-3" if ne is not None else "n_e=n/a")
            # Append CI to the operator log line when present so the
            # uncertainty is visible without opening the HTML block.
            ci = result.get("te_ci_eV")
            ci_method = result.get("te_ci_method", "disabled")
            if ci_method == "bootstrap" and ci is not None:
                ci_str = f", T_e 95%CI=[{ci[0]:.2f}, {ci[1]:.2f}] eV"
            elif ci_method == "unavailable":
                ci_str = ", T_e CI=n/a (bootstrap)"
            else:
                ci_str = ""
            append_log(self,
                       f"Single analysis: V_f={vf:+.2f} V, "
                       f"T_e={te:.2f} eV{ci_str}, {ne_str}.", "ok")
        else:
            warns = "; ".join(result["warnings"]) or "unknown reason"
            append_log(self,
                       f"Single analysis incomplete: {warns}.", "warn")
        _append_html_block(self, format_single_result_html(result))
        # Draw V_f / V_p markers on the IV plot.
        self._draw_single_overlays(result)

        # Persist the options that produced this result next to the
        # measurement CSV (if any), so a re-analysis later is
        # auditable.
        self._write_analysis_sidecar(
            method="single",
            options_obj=opts,
            single_result=result,
        )

    # ------------------------------------------------------------------
    # Analysis-options sidecar
    # ------------------------------------------------------------------
    def _write_analysis_sidecar(self, *, method: str, options_obj,
                                  fit_dict=None, plasma_dict=None,
                                  single_result=None,
                                  extra: dict | None = None) -> None:
        """Write (or quietly skip) the options-sidecar JSON next to
        the most recently saved/loaded measurement CSV.

        The sidecar captures the options dataclass (``to_dict``) plus
        a compact analysis summary (Te, uncertainty, R², status).  A
        missing sidecar is NEVER fatal — the analysis numbers are
        already in the log + history.  Errors are surfaced as a
        warn-level log line so the operator knows reproducibility
        info was not saved, without interrupting the analysis flow.
        """
        csv_path = getattr(self, "_last_csv_path", None)
        if csv_path is None:
            return  # No CSV on disk yet — nothing to attach the
                    # sidecar to.  First write happens on first save.
        try:
            from analysis_options_sidecar import write_sidecar
            opts_dict = (options_obj.to_dict()
                          if hasattr(options_obj, "to_dict") else {})
            summary: dict = {}
            fit_model = None
            if method == "double" and fit_dict:
                from dlp_fit_models import FitStatus
                summary = {
                    "fit_status": fit_dict.get("fit_status", FitStatus.OK),
                    "Te_eV":      fit_dict.get("Te_eV"),
                    "Te_err_eV":  fit_dict.get("Te_err_eV"),
                    "Te_ci95_lo_eV":  fit_dict.get("Te_ci95_lo_eV"),
                    "Te_ci95_hi_eV":  fit_dict.get("Te_ci95_hi_eV"),
                    "Te_ci_method":   fit_dict.get("Te_ci_method"),
                    "R2":         fit_dict.get("R2"),
                    "NRMSE":      fit_dict.get("NRMSE"),
                    "grade":      fit_dict.get("grade"),
                    "I_sat_A":    fit_dict.get("I_sat_fit_A"),
                    "I_sat_ci95_lo_A": fit_dict.get("I_sat_ci95_lo_A"),
                    "I_sat_ci95_hi_A": fit_dict.get("I_sat_ci95_hi_A"),
                    "I_sat_ci_method": fit_dict.get("I_sat_ci_method"),
                    "fit_warning_reason":
                        fit_dict.get("fit_warning_reason"),
                }
                fit_model = fit_dict.get("model_key")
                if plasma_dict:
                    summary["n_i_m3"] = plasma_dict.get("n_i_m3")
                    summary["n_i_ci95_lo_m3"] = plasma_dict.get(
                        "n_i_ci95_lo_m3")
                    summary["n_i_ci95_hi_m3"] = plasma_dict.get(
                        "n_i_ci95_hi_m3")
                    summary["n_i_ci_method"] = plasma_dict.get(
                        "n_i_ci_method")
                    # Scope caveat for n_i CI — never omitted so the
                    # reader always knows it is fit-only.
                    summary["n_i_ci_note"] = plasma_dict.get(
                        "n_i_ci_note", "fit_only")
                    # Record the ion-composition mode and the
                    # derived ion-mix rel-unc so a later re-analysis
                    # can tell whether the width reflects a known
                    # molecular ion, a known atomic ion, or the
                    # operator-declared "unknown" widening.
                    exp_params = getattr(
                        self, "_experiment_params", {}) or {}
                    summary["ion_composition_mode"] = \
                        exp_params.get(
                            "ion_composition_mode", "molecular")
                    summary["n_i_ci_ion_mix_rel_unc"] = \
                        plasma_dict.get(
                            "n_i_ci_ion_mix_rel_unc", 0.0)
                    # Mixed-mode inputs are persisted regardless of
                    # mode so a later re-analysis of the CSV has the
                    # operator's belief written down explicitly.
                    summary["x_atomic"] = float(
                        exp_params.get("x_atomic", 0.0))
                    summary["x_atomic_unc"] = float(
                        exp_params.get("x_atomic_unc", 0.0))
                    # Preset key — persisted so a later reader can
                    # cite the regime by stable name rather than
                    # reconstructing it from the (mode, x, Δx)
                    # triple.  "custom" means "the operator set
                    # the fields manually".
                    summary["ion_composition_preset"] = str(
                        exp_params.get(
                            "ion_composition_preset", "custom"))
                    # Per-gas composition overrides (may be empty).
                    # Persisted verbatim so a later reader sees the
                    # exact per-molecular-gas assumption in force at
                    # the time of the run.
                    _pg = exp_params.get("per_gas_composition") or {}
                    if isinstance(_pg, dict) and _pg:
                        summary["per_gas_composition"] = dict(_pg)
                comp_info = getattr(self, "_last_compliance_info", None)
                if comp_info:
                    summary["compliance_info"] = comp_info
            elif method == "single" and single_result:
                exp_params = getattr(
                    self, "_experiment_params", {}) or {}
                summary = {
                    "ok":       bool(single_result.get("ok")),
                    "Te_eV":    single_result.get("te_eV"),
                    "V_f_V":    single_result.get("v_float_V"),
                    "V_p_V":    single_result.get("v_plasma_V"),
                    "n_e_m3":   single_result.get("n_e_m3"),
                    "te_ci_eV": single_result.get("te_ci_eV"),
                    # n_e CI + scope — Single now carries the same
                    # scope-tag taxonomy as Double's n_i_ci_note.
                    "n_e_ci95_lo_m3":
                        single_result.get("n_e_ci95_lo_m3"),
                    "n_e_ci95_hi_m3":
                        single_result.get("n_e_ci95_hi_m3"),
                    "n_e_ci_method":
                        single_result.get("n_e_ci_method"),
                    "n_e_ci_note":
                        single_result.get("n_e_ci_note"),
                    # Ion-composition inputs the operator selected,
                    # persisted verbatim so a later re-analysis of
                    # the CSV knows the assumption in effect.
                    "ion_composition_mode":
                        exp_params.get(
                            "ion_composition_mode", "molecular"),
                    "x_atomic":
                        float(exp_params.get("x_atomic", 0.0)),
                    "x_atomic_unc":
                        float(exp_params.get("x_atomic_unc", 0.0)),
                    "ion_composition_preset":
                        str(exp_params.get(
                            "ion_composition_preset", "custom")),
                    # Per-gas composition overrides persisted for
                    # Single too — same reader-facing shape as the
                    # Double / Triple sidecars.  Omitted when empty.
                    **({
                        "per_gas_composition": dict(
                            exp_params.get("per_gas_composition") or {})
                    } if (exp_params.get("per_gas_composition")
                           and isinstance(
                               exp_params.get("per_gas_composition"),
                               dict)) else {}),
                    "n_e_ci_m_i_rel_unc":
                        single_result.get(
                            "n_e_ci_m_i_rel_unc", 0.0),
                    # Bidirectional diagnostic — persisted so a
                    # future re-analysis has the fwd/rev drift
                    # picture too.  Absent / None on monodirectional
                    # sweeps; the audit is explicit either way.
                    "bidirectional_mode_used":
                        bool(single_result.get(
                            "bidirectional_mode_used")),
                    "n_bidirectional_merged":
                        int(single_result.get(
                            "n_bidirectional_merged", 0)),
                    "branch_analysis_status":
                        single_result.get("branch_analysis_status"),
                    "Te_eV_fwd": single_result.get("te_eV_fwd"),
                    "Te_eV_rev": single_result.get("te_eV_rev"),
                    "V_f_V_fwd":
                        single_result.get("v_float_V_fwd"),
                    "V_f_V_rev":
                        single_result.get("v_float_V_rev"),
                    "branch_delta_pct_te":
                        single_result.get("branch_delta_pct_te"),
                }
            if extra:
                summary["extra"] = extra
            sc = write_sidecar(
                csv_path, method=method, options=opts_dict,
                fit_model=fit_model, analysis_summary=summary)
            append_log(self,
                       f"Analysis options saved: {sc.name}", "info")
        except Exception as exc:
            append_log(self,
                       f"Analysis sidecar write failed: "
                       f"{type(exc).__name__}: {exc}", "warn")

    # ------------------------------------------------------------------
    # CSV write_csv wrapper — class-level, weak-ref based
    # ------------------------------------------------------------------
    _csv_method_refs: list = []  # weakref.ref to live LPMainWindow instances

    @classmethod
    def _most_recent_dataset_method(cls) -> str:
        """Best-effort lookup of the most-recent live LPMainWindow's
        ``_dataset_method``.  Drops dead weakrefs as it walks.
        Returns ``"double"`` as a defensive fallback when no live
        instance carries a tag — matches the historic default."""
        method = "double"
        live = []
        for ref in cls._csv_method_refs:
            inst = ref()
            if inst is not None:
                live.append(ref)
                cur = getattr(inst, "_dataset_method", None)
                if cur:
                    method = cur
        cls._csv_method_refs = live
        return method

    @classmethod
    def _install_csv_method_wrapper(cls) -> None:
        """Install the once-only V2-module patches for ``write_csv``
        and ``make_csv_path``.  Both wrappers consult the weakref
        registry rather than capturing any instance strongly — keeps
        test teardown clean.

          * ``write_csv``     — injects the ``Method`` meta key
          * ``make_csv_path`` — routes the save into ``<base>/<method>/``
                                with the unified ``LP_<ts>_<method>.csv``
                                naming scheme.
        """
        import DoubleLangmuir_measure_v2 as _v2_mod
        if not getattr(_v2_mod.write_csv, "_lp_method_tagged", False):
            _orig_write = _v2_mod.write_csv
            def _wrapped_write(path, meta, *args, **kwargs):
                if isinstance(meta, dict) and "Method" not in meta:
                    meta["Method"] = cls._most_recent_dataset_method()
                return _orig_write(path, meta, *args, **kwargs)
            _wrapped_write._lp_method_tagged = True
            _v2_mod.write_csv = _wrapped_write
        if not getattr(_v2_mod.make_csv_path, "_lp_method_routed", False):
            from dlp_save_paths import make_lp_csv_path_for_method
            def _wrapped_path(folder, prefix="DLP", method=None):
                m = method or cls._most_recent_dataset_method()
                return make_lp_csv_path_for_method(folder, m)
            _wrapped_path._lp_method_routed = True
            _v2_mod.make_csv_path = _wrapped_path

    @classmethod
    def _csv_method_register(cls, instance) -> None:
        import weakref
        cls._csv_method_refs.append(weakref.ref(instance))

    # ------------------------------------------------------------------
    # Compact Double-probe analysis output + compliance-aware filter
    # ------------------------------------------------------------------
    def _run_analysis(self) -> None:
        """Override of V2's analyze slot:

          1. Pre-filter clipped points: snapshot ``_v_ist``/``_i_mean``
             and swap in compliance-filtered copies so V2's analysis
             does not see compliance-hit samples.  V2 reads those
             buffers exactly once (lines 925-926 of V2's _run_analysis)
             and works on local arrays from then on, so the swap is
             rigorously bounded.  The originals are always restored
             in ``finally``, so plotting / saving / hysteresis sees
             the full record.
          2. Suppress V2's verbose HTML during the super call by
             module-patching ``_append_html_block`` to a no-op (kept
             from the previous compact-output iteration), then emit
             one short summary block instead.
          3. Run hysteresis detection on the FULL (restored) buffer
             and append a non-blocking warning line on divergence.
        """
        import numpy as _np
        import DoubleLangmuir_measure_v2 as _v2_mod
        from dlp_single_analysis import detect_hysteresis

        # ── Stage 1: hand compliance + bootstrap settings to V2 ──
        # Compliance is now handled inside compute_double_analysis:
        # LP just tells V2 which mode to use and lets the analysis
        # layer own the decision.  This removes the old buffer-swap
        # contortions and makes the clipping summary available on
        # self._last_compliance_info after the super call.
        d_opts = getattr(self, "_double_analysis_options", None)
        self._exclude_clipped_in_fit = (
            d_opts is None
            or d_opts.compliance_mode == "exclude_clipped")
        self._bootstrap_te_ci = bool(
            getattr(d_opts, "bootstrap_enabled", False))
        self._bootstrap_te_n_iters = int(
            getattr(d_opts, "bootstrap_n_iters", 200))
        # Off by default — V2 reads this via getattr with default
        # False so the V2-standalone entry point also suppresses the
        # extra window unless something deliberately opts in.
        self._show_analysis_log = bool(
            getattr(d_opts, "show_analysis_log", False))
        # n_i uncertainty-budget inputs.  Both defaults are 0 %
        # (fit-only) so operators who do not touch the new fields in
        # the Double options dialog see the pre-existing CI label.
        self._ni_probe_area_rel_unc_pct = float(getattr(
            d_opts, "probe_area_rel_unc_pct", 0.0))
        self._ni_ion_mass_rel_unc_pct = float(getattr(
            d_opts, "ion_mass_rel_unc_pct", 0.0))

        # ── Stage 2: super call with HTML suppression ─────────────
        original_aphb = _v2_mod._append_html_block
        _v2_mod._append_html_block = lambda window, html: None
        try:
            super()._run_analysis()
        finally:
            _v2_mod._append_html_block = original_aphb

        fit = (getattr(self, "_last_model_fit", None)
               or getattr(self, "_last_fit", None))
        plasma = getattr(self, "_last_plasma", None)
        cmp_list = getattr(self, "_last_comparison", None)
        if fit is None and not cmp_list:
            return

        # ── Stage 3: hysteresis on full restored data ─────────────
        # Threshold honours the Double-probe options dialog so the
        # operator can tune drift-warning sensitivity per method
        # — Single's threshold no longer leaks into Double's check.
        d_thresh = (5.0 if d_opts is None
                    else float(d_opts.hysteresis_threshold_pct))
        try:
            if self._v_ist and self._i_mean and self._directions:
                hyst = detect_hysteresis(_np.array(self._v_ist),
                                          _np.array(self._i_mean),
                                          list(self._directions),
                                          threshold_pct=d_thresh)
                if hyst.get("flagged"):
                    pct = hyst.get("max_diff_pct") or 0.0
                    append_log(self,
                               f"Double analyze: fwd/rev branches "
                               f"diverge {pct:.1f}% — possible "
                               "plasma drift during sweep.", "warn")
        except Exception:
            pass

        comp_info = getattr(self, "_last_compliance_info", None)
        compact = _format_compact_double(fit, plasma, cmp_list,
                                           compliance_info=comp_info)
        if compact:
            original_aphb(self, compact)

        # Log the compliance summary so it is visible in the
        # acquisition log too, not just inside the HTML block.
        if comp_info and int(comp_info.get("n_flagged", 0)) > 0:
            action = comp_info.get("action", "n/a")
            frac = float(comp_info.get("clipped_fraction", 0.0))
            n_fl = int(comp_info.get("n_flagged", 0))
            n_to = int(comp_info.get("n_total", 0))
            if action == "excluded_from_fit":
                append_log(self,
                           f"Double analyze: excluded {n_fl}/{n_to} "
                           f"compliance-flagged point(s) "
                           f"({frac:.1%}).", "info")
            elif action == "retained_in_fit":
                append_log(self,
                           f"Double analyze: {n_fl}/{n_to} clipped "
                           f"point(s) retained in fit ({frac:.1%}) "
                           "— T_e may be biased.", "warn")

        # ── Stage 4: write options sidecar next to the CSV ───────
        # Reproducibility: "which knobs produced these numbers?"
        # answered by a JSON file next to the measurement.  Skipped
        # silently when no CSV has been saved / loaded yet — the
        # analysis still runs, we just have nothing to attach to.
        self._write_analysis_sidecar(
            method="double",
            options_obj=d_opts,
            fit_dict=fit,
            plasma_dict=plasma,
            extra={"compliance_info": comp_info} if comp_info else None,
        )

    # ------------------------------------------------------------------
    # Mid-sweep method-button lock
    # ------------------------------------------------------------------
    def _set_sweep_ui(self, running: bool) -> None:
        """Override of V1's UI-state toggle: delegate to base then
        also lock the Single/Double/Triple buttons.  Catches every
        lifecycle path (start, done, fail, stopped) because V2's
        sweep slots all funnel through ``_set_sweep_ui``."""
        super()._set_sweep_ui(running)
        self._lock_method_buttons_during_sweep(running)

    def _lock_method_buttons_during_sweep(self, running: bool) -> None:
        """While a sweep is running, refuse Single/Double/Triple
        switches.  Re-enable on sweep end so the dataset_method tag
        stays consistent with what was actually acquired."""
        for btn in (getattr(self, "btnMethodSingle", None),
                    getattr(self, "btnMethodDouble", None),
                    getattr(self, "btnMethodTriple", None)):
            if btn is None:
                continue
            try:
                btn.setEnabled(not running)
            except Exception:
                pass
        if running:
            append_log(self,
                       "Method buttons locked during sweep — switching "
                       "would mix dataset methods.", "info")

    # ------------------------------------------------------------------
    # Single-probe plot overlays (V_f, V_p)
    # ------------------------------------------------------------------
    def _clear_single_overlays(self) -> None:
        """Remove any V_f / V_p markers from the plot.  Called at
        every Start click so a previous Single analysis does not
        bleed into a fresh sweep."""
        if not getattr(self, "_single_overlay_lines", None):
            return
        for line in self._single_overlay_lines:
            try:
                line.remove()
            except Exception:
                pass
        self._single_overlay_lines.clear()
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def _draw_single_overlays(self, result: dict) -> None:
        """Draw V_f (solid green) and V_p (dashed magenta for medium
        confidence, dotted for low) as vertical lines on the IV plot.
        Skip silently if the underlying axes aren't available."""
        ax = getattr(self, "ax", None)
        canvas = getattr(self, "canvas", None)
        if ax is None or canvas is None:
            return
        self._clear_single_overlays()
        v_f = result.get("v_float_V")
        if v_f is not None:
            line = ax.axvline(v_f, color="#3a8", linestyle="-",
                              linewidth=1.4,
                              label=f"V_f = {v_f:+.2f} V")
            self._single_overlay_lines.append(line)
        v_p = result.get("v_plasma_V")
        if v_p is not None:
            conf = result.get("v_plasma_confidence", "low")
            # Style scales with confidence so the operator gets a
            # quick visual read on how trustworthy the V_p is:
            #   high   → solid (matches V_f's emphasis)
            #   medium → dashed
            #   low    → dotted
            style = {"high": "-", "medium": "--"}.get(conf, ":")
            method = result.get("v_p_method", "n/a")
            line = ax.axvline(v_p, color="#c5c", linestyle=style,
                              linewidth=1.4,
                              label=(f"V_p = {v_p:+.2f} V "
                                     f"({method}, {conf})"))
            self._single_overlay_lines.append(line)
        try:
            ax.legend(fontsize=8)
            canvas.draw_idle()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Load CSV (with Method tag) — UI + handler
    # ------------------------------------------------------------------
    def _install_load_csv_button(self) -> None:
        """Inject a small 'Load CSV…' button into the plot-header row
        next to the existing 'Plot…' button.  Done at runtime so V2
        doesn't need to know about it."""
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton("Load CSV…")
        btn.setToolTip("Load a previously saved sweep CSV and "
                       "populate the analysis buffer.  The Method "
                       "tag in the file is read back into the "
                       "dataset state automatically.")
        btn.setMaximumWidth(110)
        btn.clicked.connect(self._open_load_csv_dialog)
        self.btnLoadCsv = btn
        # Try to insert it into the same QHBoxLayout as the Plot…
        # button.  Fall back gracefully if the layout shape changes.
        plot_btn = getattr(self, "btnPlotSettings", None)
        if plot_btn is None or plot_btn.parent() is None:
            return
        parent_layout = plot_btn.parent().layout()
        if parent_layout is None:
            return
        for i in range(parent_layout.count()):
            item = parent_layout.itemAt(i)
            inner = item.layout() if item is not None else None
            if inner is None:
                continue
            for j in range(inner.count()):
                if inner.itemAt(j).widget() is plot_btn:
                    inner.insertWidget(j, btn)
                    return

    def _load_csv_with_method_tag(self, path: str) -> dict:
        """Load a CSV via V1's :meth:`load_csv_dataset` and propagate
        the ``Method`` header into ``self._dataset_method`` so the
        analyze dispatcher does not have to ask for confirmation on
        sauber getaggte Daten."""
        meta = self.load_csv_dataset(path)
        method = (meta.get("Method") or "").strip().lower()
        if method in ("single", "double", "triple"):
            self._dataset_method = method
            append_log(self,
                       f"CSV loaded: {len(self._v_ist)} pts, "
                       f"Method={method}.", "ok")
        else:
            self._dataset_method = None
            append_log(self,
                       f"CSV loaded: {len(self._v_ist)} pts, "
                       "no Method tag — Analyze will ask for "
                       "confirmation.", "warn")
        return meta

    # ------------------------------------------------------------------
    # Persistence — single-probe analysis options ride along the
    # existing JSON config used for sim / probe / experiment opts.
    # ------------------------------------------------------------------
    def get_config(self) -> dict:
        cfg = super().get_config()
        cfg["single_analysis_options"] = (
            self._single_analysis_options.to_dict())
        cfg["double_analysis_options"] = (
            self._double_analysis_options.to_dict())
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        from dlp_single_options import SingleAnalysisOptions
        from dlp_double_options import DoubleAnalysisOptions
        sa = cfg.get("single_analysis_options")
        if sa:
            self._single_analysis_options = (
                SingleAnalysisOptions.from_dict(sa))
        da = cfg.get("double_analysis_options")
        if da:
            self._double_analysis_options = (
                DoubleAnalysisOptions.from_dict(da))

    # ------------------------------------------------------------------
    # Mode-aware Fit Model… dispatch
    # ------------------------------------------------------------------
    @Slot()
    def _open_fit_model_dispatch(self) -> None:
        """Route the Fit Model… click to the dialog matching the
        active method.  Single opens Single-probe options, Double
        keeps the existing FitModelDialog, Triple shows a small
        info dialog."""
        active = self._current_active_method()
        if active == "single":
            self._open_single_analysis_options_dialog()
            return
        if active == "triple":
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("Triple-probe analysis")
            box.setText(
                "Triple-probe analysis runs a closed-form\n"
                "Te / n_e computation per sample tick — there\n"
                "is no swept fit-model to choose.\n\n"
                "Open the Triple measurement window via\n"
                "Methods \u2192 Triple to see live results.")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
            return
        # Default: Double — combined dialog presents the model
        # selector AND the Double-only operator knobs (compliance
        # mode, hysteresis threshold) in one place so the operator
        # never has to wonder which dialog owns which setting.
        from dlp_double_options import open_double_options_dialog
        result = open_double_options_dialog(
            self._fit_model, self._double_analysis_options,
            parent=self)
        if result is None:
            return
        new_model_key, new_opts = result
        self._fit_model = new_model_key
        self._double_analysis_options = new_opts
        from dlp_fit_models import MODELS
        label = MODELS.get(new_model_key, {}).get("label", new_model_key)
        append_log(self,
                   f"Double-probe analysis options updated: "
                   f"model={label}, "
                   f"compliance={new_opts.compliance_mode}, "
                   f"hyst-thresh={new_opts.hysteresis_threshold_pct:.1f}%.",
                   "ok")

    @Slot()
    def _open_single_analysis_options_dialog(self) -> None:
        """Open the Single-probe analysis options dialog and persist
        the result on this window."""
        from dlp_single_options import open_single_options_dialog
        new_opts = open_single_options_dialog(
            self._single_analysis_options, parent=self)
        if new_opts is None:
            return
        self._single_analysis_options = new_opts
        append_log(self,
                   "Single-probe analysis options updated: "
                   f"window={new_opts.te_window_factor:.1f}\u00d7Te, "
                   f"robust={new_opts.robust_te_fit}, "
                   f"compliance={new_opts.compliance_mode}, "
                   f"hyst-thresh={new_opts.hysteresis_threshold_pct:.1f}%.",
                   "ok")

    @Slot()
    def _open_load_csv_dialog(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Load sweep CSV", "",
            "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            self._load_csv_with_method_tag(path)
        except Exception as exc:
            append_log(self, f"CSV load failed: {exc}", "error")

    # ------------------------------------------------------------------
    # Legacy data migration (UI trigger for paths.migrate_legacy_lp_data)
    # ------------------------------------------------------------------
    def _install_migrate_legacy_button(self) -> None:
        """Inject a 'Migrate Legacy Data…' action button into the
        plot-header row, next to the Load-CSV button.  Always
        visible — empty-legacy is handled by the click handler with
        a friendly info dialog rather than a hidden button (avoids
        UI state that depends on filesystem snapshots)."""
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton("Migrate Legacy Data\u2026")
        btn.setToolTip(
            "One-time migration of historical CSVs from the old "
            "'double_langmuir' folder into the new 'lp_measurements' "
            "folder.  Confirms before any filesystem change; offers "
            "Copy (safe) and Move (consolidate) modes.")
        btn.setMaximumWidth(160)
        btn.clicked.connect(self._open_migrate_legacy_dialog)
        self.btnMigrateLegacy = btn
        plot_btn = getattr(self, "btnPlotSettings", None)
        if plot_btn is None or plot_btn.parent() is None:
            return
        parent_layout = plot_btn.parent().layout()
        if parent_layout is None:
            return
        for i in range(parent_layout.count()):
            item = parent_layout.itemAt(i)
            inner = item.layout() if item is not None else None
            if inner is None:
                continue
            for j in range(inner.count()):
                if inner.itemAt(j).widget() is plot_btn:
                    inner.insertWidget(j, btn)
                    return

    def _announce_legacy_data_if_present(self) -> None:
        """One-line log hint when ``<base>/double_langmuir/`` still
        contains items.  Never modifies state, never blocks startup,
        never auto-migrates — purely informational."""
        from paths import legacy_lp_data_dir
        legacy = legacy_lp_data_dir()
        if not legacy.exists() or not legacy.is_dir():
            return
        try:
            n = sum(1 for _ in legacy.iterdir())
        except OSError:
            return
        if n <= 0:
            return
        append_log(self,
                   f"Legacy data folder detected ({n} item(s) under "
                   f"'{legacy}').  Use 'Migrate Legacy Data…' next "
                   "to the Plot… button to consolidate.", "info")

    @Slot()
    def _open_migrate_legacy_dialog(self) -> None:
        """Confirm + run-migration flow.  Reuses
        :func:`paths.migrate_legacy_lp_data` — no new migration
        logic.  Shown order of buttons puts Cancel first (Esc-safe)
        and Copy as the default Yes-button (loss-free)."""
        from PySide6.QtWidgets import QMessageBox
        from paths import legacy_lp_data_dir, lp_measurements_data_dir
        legacy = legacy_lp_data_dir()
        new_base = lp_measurements_data_dir()
        items = []
        if legacy.exists() and legacy.is_dir():
            try:
                items = list(legacy.iterdir())
            except OSError as exc:
                append_log(self,
                           f"Migrate legacy: cannot read '{legacy}': "
                           f"{exc}", "error")
                return
        if not items:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle("No legacy data found")
            box.setText(
                f"No historical data found under:\n  {legacy}\n\n"
                "Nothing to migrate.")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
            return

        copy_btn_text = "Copy (safe — keep legacy)"
        move_btn_text = "Move (consolidate — empty legacy)"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Migrate legacy data?")
        box.setText(
            f"Found <b>{len(items)} item(s)</b> in the historic "
            f"folder:<br><code>{legacy}</code><br><br>"
            f"Migrate them into the new base folder:<br>"
            f"<code>{new_base}</code><br><br>"
            "<b>Copy</b> keeps the legacy tree untouched — "
            "recommended for the first run.<br>"
            "<b>Move</b> empties the legacy tree as items succeed.")
        box.setTextFormat(Qt.TextFormat.RichText)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        copy_btn = box.addButton(
            copy_btn_text, QMessageBox.ButtonRole.AcceptRole)
        move_btn = box.addButton(
            move_btn_text, QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(copy_btn)
        box.setEscapeButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn or clicked is None:
            append_log(self, "Migrate legacy: cancelled.", "info")
            return
        copy_mode = clicked is copy_btn
        self._run_legacy_migration(copy_mode=copy_mode)

    def _run_legacy_migration(self, *, copy_mode: bool) -> None:
        """Invoke :func:`paths.migrate_legacy_lp_data` and surface
        the outcome via log + a compact done-dialog.  Dialog
        construction is delegated to :meth:`_show_migration_error`
        and :meth:`_show_migration_done` so tests can intercept the
        feedback path without touching QMessageBox internals."""
        from paths import migrate_legacy_lp_data
        mode = "copy" if copy_mode else "move"
        try:
            n = migrate_legacy_lp_data(copy=copy_mode)
        except Exception as exc:
            append_log(self,
                       f"Migrate legacy ({mode}) failed: {exc}",
                       "error")
            self._show_migration_error(mode, str(exc))
            return
        append_log(self,
                   f"Migrate legacy ({mode}): {n} item(s) processed.",
                   "ok")
        self._show_migration_done(mode, n)

    def _show_migration_error(self, mode: str, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Migration failed")
        box.setText(
            f"Legacy data migration ({mode}) raised an error:\n\n"
            f"{message}\n\nLegacy data was left untouched.  See "
            "the log for details.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _show_migration_done(self, mode: str, n: int) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Migration done")
        box.setText(
            f"Migrated <b>{n} item(s)</b> ({mode}).<br><br>"
            "Existing items in the destination were skipped "
            "(idempotent).  You can re-run the migration safely "
            "if more legacy data shows up later.")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @Slot()
    def _read_k2000_voltage(self) -> None:
        if self.k2000 is None:
            append_log(self, "K2000 not connected.", "warn")
            return
        try:
            v = self.k2000.read_voltage()
        except Exception as exc:
            append_log(self, f"K2000 read failed: {exc}", "error")
            self.lblK2000Value.setText("ERR")
            return
        self.lblK2000Value.setText(f"{v:+.6f} V")
        append_log(self, f"K2000 V = {v:+.6f} V", "info")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        try:
            if self.k2000 is not None:
                self.k2000.close()
        except Exception:
            pass
        super().closeEvent(event)


# ============================================================================
# Entry point
# ============================================================================
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    app = QApplication(sys.argv)
    _ensure_valid_app_font()
    win = LPMainWindow()
    # Maximised — keeps title bar / window controls visible (we
    # explicitly do *not* want full-screen mode here).
    win.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
