"""
Instrument-options dialog for the Double-Langmuir-Probe Monitor v2.

Configures Keysight B2901 SMU settings: measurement speed (NPLC),
compliance, output protection, and current measurement range.  Designed
for fast I-V sweeps in magnetron/sputtering environments where probe
coating limits acquisition time.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QSettings, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)
from keysight_b2901 import KeysightB2901PSU

log = logging.getLogger(__name__)

# Iteration 4d – persistent UI preferences (currently only the
# remote-sense wiring-warning suppression).  Kept on the same
# QSettings scope as the rest of the JLU-IPI tools so a user that
# silenced the warning in one window stays silenced everywhere.
_QSETTINGS_ORG = "JLU-IPI"
_QSETTINGS_APP = "DLP"
_KEY_SUPPRESS_RSEN_WARN = "suppress_remote_sense_warning"


def is_remote_sense_warning_suppressed() -> bool:
    """Return whether the user previously ticked 'do not show again'."""
    s = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    return bool(s.value(_KEY_SUPPRESS_RSEN_WARN, False, type=bool))


def set_remote_sense_warning_suppressed(suppressed: bool) -> None:
    """Persist the user's 'do not show again' decision (or unset it)."""
    s = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    s.setValue(_KEY_SUPPRESS_RSEN_WARN, bool(suppressed))

# ── defaults ─────────────────────────────────────────────────────────

DEFAULT_INSTRUMENT_OPTIONS: dict = {
    "speed_preset": "Fast (0.1)",
    "output_protection": True,
    "autorange": True,
    "current_range_A": None,   # None = autorange; otherwise discrete value
    "compliance_A": 0.010,     # 10 mA – matches old default of spnCompl=10 mA

    # Iteration 2 – conservative defaults: existing workflows stay as is
    # until the user explicitly enables the new features.
    "custom_nplc_enabled": False,
    "custom_nplc": 0.1,
    "autozero": "ON",           # B2901 default on *RST
    "source_delay_s": 0.0,
    "hw_avg_enabled": False,
    "hw_avg_count": 4,
    "hw_avg_mode": "REP",

    # Iteration 4a – hardware setup + comfort.  Defaults match the
    # existing hardcoded connect() behaviour so the visible workflow
    # stays unchanged until the user opts in.
    "output_low": "GRO",
    "beep": False,

    # Iteration 4c – 4-wire / remote-sense.  Default OFF (2-wire) so
    # setups without proper sense leads are not silently broken by an
    # open voltage-measure loop on first connect after upgrade.
    "remote_sense": False,
}

# presets for quick setup – defaults stay safe (autorange, protected)
INSTRUMENT_PRESETS: dict[str, dict] = {
    "Fast (magnetron)": {
        "speed_preset": "Fast (0.1)",
        "output_protection": True,
        "autorange": True,
        "autozero": "ON",
        "hw_avg_enabled": False,
        "source_delay_s": 0.0,
    },
    "Very fast (noisy)": {
        "speed_preset": "Very fast (0.01)",
        "output_protection": True,
        "autorange": True,
        "autozero": "OFF",
        "hw_avg_enabled": False,
        "source_delay_s": 0.0,
    },
    "Precise (slow)": {
        "speed_preset": "Slow (10)",
        "output_protection": True,
        "autorange": True,
        "autozero": "ON",
        "hw_avg_enabled": True,
        "hw_avg_count": 4,
        "hw_avg_mode": "REP",
        "source_delay_s": 0.005,
    },
}

# Display labels for the discrete current ranges supported by the B2900
# family (mirrors KeysightB2901PSU.CURRENT_RANGES_A so any change to the
# driver propagates to the dialog).
_RANGE_LABELS: dict[str, float] = {
    "1 \u00b5A":   1e-6,
    "10 \u00b5A":  10e-6,
    "100 \u00b5A": 100e-6,
    "1 mA":        1e-3,
    "10 mA":       10e-3,
    "100 mA":      100e-3,
    "1 A":         1.0,
    "1.5 A":       1.5,
    "3 A":         3.0,
}


# ── helpers (testable in isolation) ──────────────────────────────────


def get_nplc(opts: dict) -> float:
    """Return the effective NPLC value.

    If ``custom_nplc_enabled`` is truthy, the user-supplied
    ``custom_nplc`` wins over the preset.  Otherwise the value is taken
    from the preset dictionary in the driver.
    """
    if opts.get("custom_nplc_enabled"):
        try:
            return float(opts.get("custom_nplc", 0.1))
        except (TypeError, ValueError):
            return 0.1
    label = opts.get("speed_preset", "Fast (0.1)")
    return KeysightB2901PSU.SPEED_PRESETS.get(label, 0.1)


def normalize_options(opts: dict | None) -> dict:
    """Return a fully-populated options dict (filling defaults).

    Pure data layer – no Qt dependency – so callers can unit-test the
    behaviour without instantiating the dialog.
    """
    out = dict(DEFAULT_INSTRUMENT_OPTIONS)
    if opts:
        out.update(opts)
    # legacy entries: ``autorange`` boolean implies current_range_A=None
    if out.get("autorange"):
        out["current_range_A"] = None
    # Coerce enum-like values into the canonical upper-case form so the
    # dialog, presets and driver all speak the same dialect.
    az = str(out.get("autozero", "ON")).upper()
    out["autozero"] = az if az in ("OFF", "ON", "ONCE") else "ON"
    mode = str(out.get("hw_avg_mode", "REP")).upper()
    out["hw_avg_mode"] = mode if mode in ("REP", "MOV") else "REP"
    try:
        out["hw_avg_count"] = max(1, min(100, int(out.get("hw_avg_count", 4))))
    except (TypeError, ValueError):
        out["hw_avg_count"] = 4
    try:
        out["source_delay_s"] = max(0.0, float(out.get("source_delay_s", 0.0)))
    except (TypeError, ValueError):
        out["source_delay_s"] = 0.0
    # Iteration 4a normalisations.
    ol = str(out.get("output_low", "GRO")).upper()
    out["output_low"] = ol if ol in ("GRO", "FLO") else "GRO"
    out["beep"] = bool(out.get("beep", False))
    # Iteration 4c – remote-sense flag.
    out["remote_sense"] = bool(out.get("remote_sense", False))
    return out


# Sane bounds for user-visible numeric fields.  Kept here so the
# validator and the dialog agree on the same limits.
NPLC_MIN, NPLC_MAX = 0.001, 100.0
SOURCE_DELAY_MAX_S = 10.0
HW_AVG_COUNT_MAX = 100


def validate_options(opts: dict) -> list[str]:
    """Return a list of human-readable warnings / errors for *opts*.

    Empty list = nothing to flag.  Used both by the dialog (to disable
    the OK button or warn the user) and by tests.
    """
    msgs: list[str] = []
    compliance = float(opts.get("compliance_A", 0.0) or 0.0)
    if compliance <= 0:
        msgs.append("Compliance must be greater than 0 A.")
    if not opts.get("autorange", True):
        rng = opts.get("current_range_A")
        if rng is None or float(rng) <= 0:
            msgs.append("Manual current range must be > 0 A "
                        "when autorange is disabled.")
        elif compliance > float(rng):
            msgs.append(
                f"Compliance ({compliance:.4g} A) exceeds the selected "
                f"current range ({float(rng):.4g} A). The SMU will reject "
                f"the configuration.")
    if opts.get("custom_nplc_enabled"):
        try:
            v = float(opts.get("custom_nplc"))
        except (TypeError, ValueError):
            v = float("nan")
        if not (NPLC_MIN <= v <= NPLC_MAX):
            msgs.append(
                f"Custom NPLC must be in [{NPLC_MIN}, {NPLC_MAX}].")
    try:
        sd = float(opts.get("source_delay_s", 0.0))
        if sd < 0 or sd > SOURCE_DELAY_MAX_S:
            msgs.append(
                f"Source delay must be in [0, {SOURCE_DELAY_MAX_S}] s.")
    except (TypeError, ValueError):
        msgs.append("Source delay must be a number.")
    if opts.get("hw_avg_enabled"):
        try:
            c = int(opts.get("hw_avg_count", 0))
        except (TypeError, ValueError):
            c = 0
        if not (1 <= c <= HW_AVG_COUNT_MAX):
            msgs.append(
                f"HW averaging count must be in [1, {HW_AVG_COUNT_MAX}].")
    return msgs


def check_error_queue(smu, *, max_reads: int = 5) -> list[str]:
    """Drain the SCPI error queue via ``:SYST:ERR?``.

    Returns a list of non-zero error strings the instrument reported.
    Iteration 4d added this as a generic post-apply sanity check so a
    silently-rejected SCPI command (e.g. ``:SYST:RSEN`` on firmware
    that does not implement it but also does not raise on the VISA
    layer) still leaves a trace in the application log.

    Defensive contract:
    * No-op when the smu has no ``_query`` (Fakes, mocks).
    * Stops at the first ``+0,"No error"`` response.
    * Bails out without raising on any query exception (returns what
      it has collected so far).
    * Caps the number of reads at ``max_reads`` to avoid hanging on a
      misbehaving instrument that never reports an empty queue.
    """
    query = getattr(smu, "_query", None)
    if not callable(query):
        return []
    errors: list[str] = []
    for _ in range(max_reads):
        try:
            raw = query(":SYST:ERR?")
        except Exception:
            return errors
        if raw is None:
            break
        text = str(raw).strip()
        if not text:
            break
        head = text.split(",", 1)[0].strip()
        if head in ("0", "+0", "-0"):
            break
        errors.append(text)
    return errors


def _call_defensive(smu, method_name: str, *args, **kwargs) -> None:
    """Invoke ``smu.<method_name>(*args, **kwargs)`` if available.

    Swallow and log any exception – a missing method or an unsupported
    SCPI command on a specific model must not abort the rest of the
    configuration.  Used for the new iteration-2 setters so that older
    driver or simulation instances keep working.
    """
    fn = getattr(smu, method_name, None)
    if not callable(fn):
        log.debug("%s: method not present – skipping.", method_name)
        return
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        log.warning("%s failed: %s", method_name, exc)


def apply_instrument_options(smu: KeysightB2901PSU, opts: dict) -> None:
    """Apply instrument options to a connected SMU.

    Call this AFTER smu.connect() to override the defaults set during
    initialization.  Each SCPI write is wrapped defensively so a
    model-specific refusal does not abort the whole configuration.

    Order rationale: NPLC → source delay → auto-zero → averaging → range
    → compliance.  Averaging sits *after* NPLC because the filter counts
    NPLC-long integrations; range sits *before* compliance so the SMU
    validates the limit against the active range.
    """
    opts = normalize_options(opts)

    # 1. NPLC (always supported) – uses the effective value, which may
    #    come from the custom spin instead of the preset.
    try:
        smu.set_nplc(get_nplc(opts))
    except Exception as exc:
        log.warning("set_nplc failed: %s", exc)

    # 2. Source delay (iteration 2) – additive to the software settle.
    _call_defensive(smu, "set_source_delay",
                    float(opts.get("source_delay_s", 0.0)))

    # 3. Auto-zero (iteration 2) – "ON" is the factory default.
    _call_defensive(smu, "set_autozero", str(opts.get("autozero", "ON")))

    # 4. Hardware averaging (iteration 2) – orthogonal to software avg.
    _call_defensive(smu, "set_averaging",
                    bool(opts.get("hw_avg_enabled", False)),
                    count=int(opts.get("hw_avg_count", 1)),
                    mode=str(opts.get("hw_avg_mode", "REP")))

    # 5. Output protection (auto-disable on compliance)
    try:
        smu.enable_output_protection(bool(opts.get("output_protection", True)))
    except Exception as exc:
        log.warning("enable_output_protection failed: %s", exc)

    # 6. Current range (autorange OR fixed) – set BEFORE compliance so
    #    the SMU has the right range context to validate the limit.
    rng = None if opts.get("autorange", True) else opts.get("current_range_A")
    try:
        if hasattr(smu, "set_current_range"):
            smu.set_current_range(rng)
        else:
            # Defensive fallback for older driver / FakeB2901 instances:
            # write SCPI directly via the private helper if available.
            writer = getattr(smu, "_write", None)
            if callable(writer):
                if rng is None:
                    writer(":SENS:CURR:RANG:AUTO ON")
                else:
                    writer(":SENS:CURR:RANG:AUTO OFF")
                    writer(f":SENS:CURR:RANG {float(rng):.6g}")
    except Exception as exc:
        log.warning("current range configuration failed: %s", exc)

    # 7. Compliance value (the actual current limit)
    try:
        compliance = float(opts.get("compliance_A", 0.010))
        if compliance > 0:
            smu.set_current_limit(compliance)
    except Exception as exc:
        log.warning("set_current_limit failed: %s", exc)

    # 8. Hardware setup / comfort (iteration 4a) – applied last so they
    #    reliably override the hardcoded defaults from connect().
    _call_defensive(smu, "set_output_low", str(opts.get("output_low", "GRO")))
    _call_defensive(smu, "set_beep", bool(opts.get("beep", False)))

    # 9. 4-wire / remote sense (iteration 4c) – opt-in.  Stays at the
    #    end so it survives every preceding configuration step and a
    #    later *RST + re-apply still toggles back to the user's choice.
    _call_defensive(smu, "set_remote_sense",
                    bool(opts.get("remote_sense", False)))

    # 10. Post-apply health check (iteration 4d) – drain the SCPI
    #     error queue once and surface any silently-rejected command.
    #     Defensive: skipped on Fakes / mocks without ``_query``.
    try:
        errs = check_error_queue(smu)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("error-queue check raised: %s", exc)
        errs = []
    for e in errs:
        log.warning("SMU reported SCPI error after apply: %s", e)


def estimate_sweep_time(opts: dict, n_points: int,
                         settle_s: float) -> float:
    """Estimate total sweep time in seconds.

    Rough estimate: (NPLC/50 + settle) * n_points + overhead.
    """
    nplc = get_nplc(opts)
    t_meas = nplc / 50.0  # seconds per measurement @ 50 Hz
    return (t_meas + settle_s) * n_points + 0.5  # 0.5 s overhead


# ── dialog ───────────────────────────────────────────────────────────


class InstrumentOptionsDialog(QDialog):
    """Dialog for Keysight B2901 instrument settings."""

    def __init__(self, opts: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Instrument Options")
        self.setMinimumWidth(380)
        self._opts = normalize_options(opts)

        # Small-display hardening: build inside a QScrollArea so the
        # form scrolls when the screen height cannot fit it; the
        # button box (added at the bottom of __init__) is pinned
        # outside the scroll via _scroll_top so OK/Cancel stays
        # reachable on small laptops.
        from utils import setup_scrollable_dialog
        layout, _scroll_top = setup_scrollable_dialog(self)

        # ── Quick Setup ─────────────────────────────────────────────
        grp_pre = QGroupBox("Quick Setup")
        fl_pre = QFormLayout(grp_pre)
        self.cmbPreset = QComboBox()
        self.cmbPreset.addItems(list(INSTRUMENT_PRESETS.keys()))
        self.cmbPreset.currentTextChanged.connect(self._apply_preset)
        fl_pre.addRow("Preset:", self.cmbPreset)
        layout.addWidget(grp_pre)

        # ── Two-column band ─────────────────────────────────────────
        # Left column: everyday measurement / safety settings.
        # Right column: timing, hardware-setup and diagnostics.
        # Quick Setup (above) and Hint/Warn (below) stay full-width.
        cols_row = QHBoxLayout()
        cols_row.setSpacing(8)
        self._col_left = QVBoxLayout()
        self._col_right = QVBoxLayout()
        self._col_left.setSpacing(6)
        self._col_right.setSpacing(6)
        cols_row.addLayout(self._col_left, 1)
        cols_row.addLayout(self._col_right, 1)
        layout.addLayout(cols_row)

        # ── Measurement (NPLC) ──────────────────────────────────────
        grp_speed = QGroupBox("Measurement")
        fl_speed = QFormLayout(grp_speed)
        self.cmbSpeed = QComboBox()
        self.cmbSpeed.addItems(list(KeysightB2901PSU.SPEED_PRESETS.keys()))
        idx = list(KeysightB2901PSU.SPEED_PRESETS.keys()).index(
            self._opts.get("speed_preset", "Fast (0.1)"))
        self.cmbSpeed.setCurrentIndex(idx)
        self.cmbSpeed.setToolTip(
            "NPLC (Number of Power Line Cycles) controls integration "
            "time per measurement.\nLower = faster but noisier.")
        self.cmbSpeed.currentTextChanged.connect(self._update_estimate)
        fl_speed.addRow("Speed (NPLC):", self.cmbSpeed)

        # Custom NPLC – free-form override.  When enabled, the preset
        # combo is greyed out so the user can see at a glance which
        # value actually wins.
        custom_row = QWidget()
        custom_lay = QHBoxLayout(custom_row)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(6)
        self.chkCustomNplc = QCheckBox("Custom")
        self.chkCustomNplc.setChecked(
            bool(self._opts.get("custom_nplc_enabled", False)))
        self.chkCustomNplc.setToolTip(
            "Use a free-form NPLC value instead of the preset.\n"
            "When enabled, the preset selection above is ignored.")
        self.spnCustomNplc = QDoubleSpinBox()
        self.spnCustomNplc.setDecimals(3)
        self.spnCustomNplc.setRange(NPLC_MIN, NPLC_MAX)
        self.spnCustomNplc.setSingleStep(0.1)
        self.spnCustomNplc.setValue(
            float(self._opts.get("custom_nplc", 0.1)))
        self.spnCustomNplc.setToolTip(
            f"NPLC in [{NPLC_MIN}, {NPLC_MAX}].  Values outside the "
            f"presets are experimental – verify stability on hardware.")
        custom_lay.addWidget(self.chkCustomNplc)
        custom_lay.addWidget(self.spnCustomNplc, 1)
        fl_speed.addRow("Custom NPLC:", custom_row)
        self.chkCustomNplc.toggled.connect(self._on_custom_nplc_toggled)
        self.spnCustomNplc.valueChanged.connect(self._update_estimate)

        # Auto-Zero selector (Off / On / Once)
        self.cmbAutoZero = QComboBox()
        for s in KeysightB2901PSU.AUTOZERO_STATES:
            self.cmbAutoZero.addItem(s)
        az_idx = self.cmbAutoZero.findText(
            str(self._opts.get("autozero", "ON")).upper())
        if az_idx >= 0:
            self.cmbAutoZero.setCurrentIndex(az_idx)
        self.cmbAutoZero.setToolTip(
            "Auto-zero behaviour:\n"
            "• OFF – fastest, may drift.\n"
            "• ON  – periodic re-zero (factory default).\n"
            "• ONCE – re-zero once now, then hold.")
        fl_speed.addRow("Auto-zero:", self.cmbAutoZero)

        self.lblEstimate = QLabel()
        self.lblEstimate.setStyleSheet("color: #8890a0; font-size: 11px;")
        fl_speed.addRow("Est. time:", self.lblEstimate)
        self._col_left.addWidget(grp_speed)

        # ── Timing & Filter (iteration 2) ───────────────────────────
        grp_timing = QGroupBox("Timing && Filter")
        fl_timing = QFormLayout(grp_timing)

        self.spnSourceDelay = QDoubleSpinBox()
        self.spnSourceDelay.setDecimals(4)
        self.spnSourceDelay.setRange(0.0, SOURCE_DELAY_MAX_S)
        self.spnSourceDelay.setSingleStep(0.001)
        self.spnSourceDelay.setSuffix(" s")
        self.spnSourceDelay.setValue(
            float(self._opts.get("source_delay_s", 0.0)))
        self.spnSourceDelay.setToolTip(
            "Hardware source delay between set-voltage and measure.\n"
            "Adds to the software settle time in the main window.")
        self.spnSourceDelay.valueChanged.connect(self._refresh_warnings)
        fl_timing.addRow("Source delay:", self.spnSourceDelay)

        self.chkHwAvg = QCheckBox("Enable hardware averaging")
        self.chkHwAvg.setChecked(
            bool(self._opts.get("hw_avg_enabled", False)))
        self.chkHwAvg.setToolTip(
            "Enable the SMU's internal averaging filter.\n"
            "Acts in addition to the software averaging in the main "
            "window (effective N = software_N \u00D7 hardware count).")
        self.chkHwAvg.toggled.connect(self._on_hw_avg_toggled)
        fl_timing.addRow(self.chkHwAvg)

        self.spnHwAvgCount = QSpinBox()
        self.spnHwAvgCount.setRange(1, HW_AVG_COUNT_MAX)
        self.spnHwAvgCount.setValue(
            int(self._opts.get("hw_avg_count", 4)))
        self.spnHwAvgCount.setToolTip(
            "Number of samples the hardware filter averages per point.")
        self.spnHwAvgCount.valueChanged.connect(self._refresh_warnings)
        fl_timing.addRow("Count:", self.spnHwAvgCount)

        self.cmbHwAvgMode = QComboBox()
        for m in KeysightB2901PSU.AVERAGING_MODES:
            self.cmbHwAvgMode.addItem(m)
        mode_idx = self.cmbHwAvgMode.findText(
            str(self._opts.get("hw_avg_mode", "REP")).upper())
        if mode_idx >= 0:
            self.cmbHwAvgMode.setCurrentIndex(mode_idx)
        self.cmbHwAvgMode.setToolTip(
            "REP – repeating average (clears on each trigger).\n"
            "MOV – moving-window average (running).")
        fl_timing.addRow("Mode:", self.cmbHwAvgMode)
        self._col_right.addWidget(grp_timing)

        # ── Range ───────────────────────────────────────────────────
        grp_range = QGroupBox("Current Range")
        fl_range = QFormLayout(grp_range)
        self.chkAutorange = QCheckBox("Autorange")
        self.chkAutorange.setChecked(bool(self._opts.get("autorange", True)))
        self.chkAutorange.setToolTip(
            "Automatic current range selection.\n"
            "ON: best resolution, slight delay on range change.\n"
            "OFF: pin a fixed range below.")
        self.chkAutorange.toggled.connect(self._on_autorange_toggled)
        fl_range.addRow(self.chkAutorange)

        self.cmbRange = QComboBox()
        for label in _RANGE_LABELS:
            self.cmbRange.addItem(label, _RANGE_LABELS[label])
        # preselect the smallest range that is >= configured value
        cur_rng = self._opts.get("current_range_A")
        if cur_rng is not None:
            idx_r = next((i for i, v in enumerate(_RANGE_LABELS.values())
                          if v >= float(cur_rng)),
                         len(_RANGE_LABELS) - 1)
            self.cmbRange.setCurrentIndex(idx_r)
        else:
            # default fixed range = 10 mA (matches default compliance)
            self.cmbRange.setCurrentIndex(
                list(_RANGE_LABELS.values()).index(10e-3))
        self.cmbRange.setToolTip(
            "Fixed current measurement range. Only relevant when "
            "autorange is OFF.")
        self.cmbRange.currentTextChanged.connect(self._refresh_warnings)
        self._lblRange = QLabel("Manual range:")
        fl_range.addRow(self._lblRange, self.cmbRange)
        self._col_left.addWidget(grp_range)

        # ── Protection ──────────────────────────────────────────────
        grp_prot = QGroupBox("Protection")
        fl_prot = QFormLayout(grp_prot)
        self.spnCompliance = QDoubleSpinBox()
        self.spnCompliance.setDecimals(4)
        self.spnCompliance.setRange(0.0001, 3.0)
        self.spnCompliance.setSingleStep(0.001)
        self.spnCompliance.setSuffix(" A")
        self.spnCompliance.setValue(
            float(self._opts.get("compliance_A", 0.010)))
        self.spnCompliance.setToolTip(
            "Current compliance limit.\n"
            "Hardware ceiling on the B2901 is 3 A DC.")
        self.spnCompliance.valueChanged.connect(self._refresh_warnings)
        fl_prot.addRow("Compliance:", self.spnCompliance)

        self.chkProtection = QCheckBox("Auto-disable output on compliance")
        self.chkProtection.setChecked(
            bool(self._opts.get("output_protection", True)))
        self.chkProtection.setToolTip(
            "Auto-disable output if the compliance limit is hit.\n"
            "Recommended ON for magnetron / plasma environments.")
        fl_prot.addRow(self.chkProtection)
        self._col_left.addWidget(grp_prot)

        # ── Advanced (iteration 4a) ─────────────────────────────────
        grp_adv = QGroupBox("Advanced")
        fl_adv = QFormLayout(grp_adv)

        self.lblAdvHint = QLabel(
            "Hardware setup & recovery – change only if you know what "
            "you are doing.")
        self.lblAdvHint.setStyleSheet("color: #8890a0; font-size: 10px;")
        self.lblAdvHint.setWordWrap(True)
        fl_adv.addRow(self.lblAdvHint)

        # IDN (read-only, filled from the parent's lblIdn to avoid
        # issuing an extra SCPI query just to open the dialog).
        self.lblIdnValue = QLabel("(not connected)")
        self.lblIdnValue.setTextInteractionFlags(
            self.lblIdnValue.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lblIdnValue.setWordWrap(True)
        fl_adv.addRow("Instrument:", self.lblIdnValue)

        # Output-low topology (GRO / FLO)
        self.cmbOutputLow = QComboBox()
        for m in KeysightB2901PSU.OUTPUT_LOW_MODES:
            self.cmbOutputLow.addItem(m)
        ol_idx = self.cmbOutputLow.findText(
            str(self._opts.get("output_low", "GRO")).upper())
        if ol_idx >= 0:
            self.cmbOutputLow.setCurrentIndex(ol_idx)
        self.cmbOutputLow.setToolTip(
            "Low-terminal topology.\n"
            "GRO – tied to chassis ground (default, correct for most "
            "DLP setups).\n"
            "FLO – floating (only for genuinely floating measurements).\n"
            "Wrong choice + wrong wiring produces unreliable data.")
        fl_adv.addRow("Output low:", self.cmbOutputLow)

        # Beep
        self.chkBeep = QCheckBox("Enable front-panel beep")
        self.chkBeep.setChecked(bool(self._opts.get("beep", False)))
        self.chkBeep.setToolTip(
            "Re-enable the SMU beeper (the driver silences it at "
            "connect by default).")
        fl_adv.addRow(self.chkBeep)

        # Remote sense (iteration 4c) – 4-wire measurement.
        self.chkRemoteSense = QCheckBox("Remote sense (4-wire)")
        self.chkRemoteSense.setChecked(
            bool(self._opts.get("remote_sense", False)))
        self.chkRemoteSense.setToolTip(
            "Enable 4-wire / Kelvin sense measurement.\n\n"
            "WIRING: requires Sense+ and Sense- leads physically "
            "connected to the probe, in addition to Force+/Force-.\n"
            "Enabling this without proper sense leads produces an "
            "open voltage-measure loop and unreliable readings.\n\n"
            "Default: OFF (2-wire) — the SMU measures voltage at the "
            "Force terminals only.")
        # Iter 4d: confirm only on user clicks (clicked, not toggled),
        # so the initial setChecked above never triggers the warning.
        self.chkRemoteSense.clicked.connect(self._on_remote_sense_clicked)
        fl_adv.addRow(self.chkRemoteSense)

        # Reset button
        self.btnReset = QPushButton("Reset instrument (*RST)\u2026")
        self.btnReset.setToolTip(
            "Factory-reset the SMU and re-apply the current settings.\n"
            "Disabled while a sweep is running or when no SMU is "
            "connected.")
        self.btnReset.clicked.connect(self._on_reset_clicked)
        fl_adv.addRow(self.btnReset)

        self._col_right.addWidget(grp_adv)
        # Push column tops to the same baseline so the visual rhythm
        # stays clean even when one column has fewer / shorter groups.
        self._col_left.addStretch(1)
        self._col_right.addStretch(1)

        # ── Hints / validation messages ─────────────────────────────
        self.lblHint = QLabel(
            "Compliance is the SMU current limit; output protection "
            "decides whether the output is auto-disabled when that "
            "limit is hit.")
        self.lblHint.setStyleSheet("color: #e0b050; font-size: 10px;")
        self.lblHint.setWordWrap(True)
        layout.addWidget(self.lblHint)

        self.lblWarn = QLabel("")
        self.lblWarn.setStyleSheet("color: #ff8855; font-size: 10px;")
        self.lblWarn.setWordWrap(True)
        layout.addWidget(self.lblWarn)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._btn_ok = btns.button(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        # Pinned outside the scroll area so OK/Cancel stays visible
        # on small displays even when the form scrolls.
        _scroll_top.addWidget(btns)

        self._on_autorange_toggled(self.chkAutorange.isChecked())
        self._on_custom_nplc_toggled(self.chkCustomNplc.isChecked())
        self._on_hw_avg_toggled(self.chkHwAvg.isChecked())
        self._refresh_advanced_state()
        self._update_estimate()
        self._refresh_warnings()

    # ── slots ────────────────────────────────────────────────────────

    def _apply_preset(self, name: str):
        p = INSTRUMENT_PRESETS.get(name)
        if not p:
            return
        idx = list(KeysightB2901PSU.SPEED_PRESETS.keys()).index(
            p["speed_preset"])
        self.cmbSpeed.setCurrentIndex(idx)
        self.chkProtection.setChecked(p.get("output_protection", True))
        self.chkAutorange.setChecked(p.get("autorange", True))
        # Iteration 2 fields – only overridden when the preset defines them.
        if "autozero" in p:
            az_idx = self.cmbAutoZero.findText(str(p["autozero"]).upper())
            if az_idx >= 0:
                self.cmbAutoZero.setCurrentIndex(az_idx)
        if "hw_avg_enabled" in p:
            self.chkHwAvg.setChecked(bool(p["hw_avg_enabled"]))
        if "hw_avg_count" in p:
            self.spnHwAvgCount.setValue(int(p["hw_avg_count"]))
        if "hw_avg_mode" in p:
            m_idx = self.cmbHwAvgMode.findText(str(p["hw_avg_mode"]).upper())
            if m_idx >= 0:
                self.cmbHwAvgMode.setCurrentIndex(m_idx)
        if "source_delay_s" in p:
            self.spnSourceDelay.setValue(float(p["source_delay_s"]))
        # Presets never prescribe "custom NPLC" – leave the user's
        # checkbox alone so the preset does not clobber expert tuning.
        self._refresh_warnings()

    @Slot(bool)
    def _on_custom_nplc_toggled(self, on: bool) -> None:
        """Custom NPLC wins over the preset when checked."""
        self.cmbSpeed.setEnabled(not on)
        self.spnCustomNplc.setEnabled(on)
        self._update_estimate()
        self._refresh_warnings()

    @Slot(bool)
    def _on_hw_avg_toggled(self, on: bool) -> None:
        self.spnHwAvgCount.setEnabled(on)
        self.cmbHwAvgMode.setEnabled(on)
        self._refresh_warnings()

    @Slot(bool)
    def _on_autorange_toggled(self, on: bool) -> None:
        # the manual range row is only useful when autorange is OFF
        self.cmbRange.setEnabled(not on)
        self._lblRange.setEnabled(not on)
        self._refresh_warnings()

    def _update_estimate(self):
        opts = self.get_options()
        settle = 0.2
        parent = self.parent()
        if parent and hasattr(parent, "spnSettle"):
            settle = parent.spnSettle.value()
        n_pts = 200
        if parent and hasattr(parent, "spnVstart"):
            v_range = abs(parent.spnVstop.value() - parent.spnVstart.value())
            step = max(parent.spnVstep.value(), 0.01)
            n_pts = int(v_range / step) + 1
        t = estimate_sweep_time(opts, n_pts, settle)
        self.lblEstimate.setText(f"~{t:.1f} s for {n_pts} points "
                                  f"(settle {settle:.0f} ms)")

    def _refresh_warnings(self) -> None:
        msgs = validate_options(self.get_options())
        self.lblWarn.setText(" • ".join(msgs))
        if hasattr(self, "_btn_ok") and self._btn_ok is not None:
            self._btn_ok.setEnabled(not msgs)

    # ── data accessor ───────────────────────────────────────────────

    # ── Advanced helpers (iteration 4a) ─────────────────────────────

    def _parent_smu(self):
        """Return the SMU currently attached to the parent window, if any."""
        parent = self.parent()
        return getattr(parent, "smu", None) if parent is not None else None

    def _is_sweep_running(self) -> bool:
        """Proxy for the parent's sweep state.

        The base DLP window enables ``btnStop`` only while a sweep is
        actively running (see ``_set_sweep_ui`` in
        ``DoubleLangmuir_measure.py``).  When the parent is missing or
        has no such button, we fall back to "idle" so the Reset button
        is still usable in tests / stand-alone dialogs.
        """
        parent = self.parent()
        btn_stop = getattr(parent, "btnStop", None)
        if btn_stop is None:
            return False
        try:
            return bool(btn_stop.isEnabled())
        except Exception:
            return False

    def _refresh_advanced_state(self) -> None:
        """Update the Advanced group to reflect the current hardware
        context (IDN text, Reset-button enablement)."""
        parent = self.parent()
        # IDN: prefer the parent's existing label (no extra SCPI traffic).
        idn_text = ""
        lbl = getattr(parent, "lblIdn", None) if parent is not None else None
        if lbl is not None:
            try:
                idn_text = str(lbl.text()).strip()
            except Exception:
                idn_text = ""
        self.lblIdnValue.setText(idn_text or "(not connected)")

        # Reset: only enabled when connected and idle.
        smu = self._parent_smu()
        can_reset = (smu is not None) and (not self._is_sweep_running())
        self.btnReset.setEnabled(can_reset)
        if smu is None:
            self.btnReset.setToolTip(
                "Connect the SMU to enable factory reset.")
        elif self._is_sweep_running():
            self.btnReset.setToolTip(
                "Stop the sweep before resetting the instrument.")
        else:
            self.btnReset.setToolTip(
                "Factory-reset the SMU and re-apply the current settings.")

    @Slot(bool)
    def _on_remote_sense_clicked(self, checked: bool) -> None:
        """Confirm wiring before letting the user enable 4-wire sense.

        Disabling never needs confirmation; only an OFF→ON transition
        triggers the wiring-warning dialog.  Cancelling reverts the
        checkbox in-place (without re-firing this slot).  A persistent
        'do not show again' check (QSettings) lets repeat users opt
        out of the dialog while keeping the warning in place for new
        sessions / new operators by default.
        """
        if not checked:
            return
        if is_remote_sense_warning_suppressed():
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Enable Remote Sense (4-wire)?")
        box.setText("Remote sense is about to be enabled.")
        box.setInformativeText(
            "Requires Sense+ and Sense- leads physically connected to "
            "the probe in addition to Force+/Force-.\n\n"
            "Without proper sense leads the SMU sees an open voltage-"
            "measure loop and returns OFL / unreliable readings.\n\n"
            "Continue only if your wiring is verified.")
        suppress_cb = QCheckBox("Do not show this warning again")
        box.setCheckBox(suppress_cb)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        result = box.exec()
        if result != QMessageBox.StandardButton.Ok:
            # Revert without re-triggering this slot.
            self.chkRemoteSense.blockSignals(True)
            try:
                self.chkRemoteSense.setChecked(False)
            finally:
                self.chkRemoteSense.blockSignals(False)
            return
        if suppress_cb.isChecked():
            try:
                set_remote_sense_warning_suppressed(True)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Could not persist remote-sense warning "
                            "suppression: %s", exc)

    @Slot()
    def _on_reset_clicked(self) -> None:
        """Confirm, reset, re-apply – with defensive logging."""
        smu = self._parent_smu()
        if smu is None or self._is_sweep_running():
            # UI guard should have prevented this, but be defensive.
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Reset SMU")
        box.setText(
            "Reset the instrument to factory defaults (*RST)?\n\n"
            "The output will switch OFF and all settings will be "
            "cleared.  Your current instrument options will be "
            "re-applied automatically afterwards.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        # 1. Reset – defensive (unsupported models fall through to _write).
        try:
            if hasattr(smu, "factory_reset"):
                smu.factory_reset()
            else:
                writer = getattr(smu, "_write", None)
                if callable(writer):
                    for cmd in ("*RST", "*CLS"):
                        try:
                            writer(cmd)
                        except Exception as exc:
                            log.warning("%s failed: %s", cmd, exc)
        except Exception as exc:
            log.warning("factory_reset failed: %s", exc)

        # 2. Re-apply current options so the user's settings survive.
        try:
            apply_instrument_options(smu, self.get_options())
        except Exception as exc:
            log.warning("apply_instrument_options after reset failed: %s",
                        exc)

        # 3. Update UI state: after *RST the SMU's output is OFF, but
        #    the VISA session is still open – connection indicator stays
        #    as is; we just refresh the Advanced group.
        self._refresh_advanced_state()

        # 4. Surface a human-readable log entry in the main window so
        #    the action is traceable in the acquisition log.
        try:
            from utils import append_log
            append_log(self.parent(),
                        "SMU reset (*RST) – instrument options re-applied.",
                        "warn")
        except Exception:
            pass

    def get_options(self) -> dict:
        autorange = self.chkAutorange.isChecked()
        rng = None
        if not autorange:
            rng = self.cmbRange.currentData()
            if rng is None:
                # editable combo or unknown label – best-effort parse
                rng = _RANGE_LABELS.get(self.cmbRange.currentText())
        return {
            "speed_preset": self.cmbSpeed.currentText(),
            "output_protection": self.chkProtection.isChecked(),
            "autorange": autorange,
            "current_range_A": float(rng) if rng is not None else None,
            "compliance_A": float(self.spnCompliance.value()),
            "custom_nplc_enabled": self.chkCustomNplc.isChecked(),
            "custom_nplc": float(self.spnCustomNplc.value()),
            "autozero": self.cmbAutoZero.currentText().upper(),
            "source_delay_s": float(self.spnSourceDelay.value()),
            "hw_avg_enabled": self.chkHwAvg.isChecked(),
            "hw_avg_count": int(self.spnHwAvgCount.value()),
            "hw_avg_mode": self.cmbHwAvgMode.currentText().upper(),
            "output_low": self.cmbOutputLow.currentText().upper(),
            "beep": self.chkBeep.isChecked(),
            "remote_sense": self.chkRemoteSense.isChecked(),
        }
