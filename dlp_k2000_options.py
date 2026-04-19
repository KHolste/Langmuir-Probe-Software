"""User-facing instrument options for the Keithley 2000 DMM.

Why a separate module?
----------------------
The main window already exposes transport / VISA / Sim / Connect /
Read controls for the K2000, but the scientifically-meaningful
integration knobs (autorange, manual range, NPLC integration time)
have so far been reachable only from code.  The Triple-probe
workflow in particular benefits from an operator-accessible
autorange-OFF plus a fixed voltage range, because autorange latency
shows up as jitter on fast sample ticks.

Scope kept deliberately narrow — just the three knobs the current
driver (:class:`keithley_2000.Keithley2000DMM`) already supports and
that the fake (:class:`fake_keithley_2000.FakeKeithley2000`) can
mirror for tests.  No trigger-mode, no filter/averaging, no
scanner-card support: the driver does not implement those and this
pass is explicitly not a driver-extension pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

#: Fixed DC-voltage ranges the K2000 accepts natively, in volts.
#: Matches the device's front-panel range selector.  Selecting a
#: range that is too small for the sampled signal triggers OVRFLW on
#: the instrument — the UI clamps the spinner to these discrete
#: values so the operator cannot type an out-of-range number by
#: accident.
K2000_RANGES_V: tuple[float, ...] = (0.1, 1.0, 10.0, 100.0, 1000.0)

#: NPLC presets offered in the dialog.  0.01 is the fastest usable
#: value on the K2000 (~200 us per sample at 50 Hz mains); 10 is the
#: slowest and gives roughly 6.5-digit resolution.  The default 1.0
#: matches the driver's existing default.
K2000_NPLC_PRESETS: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0)


@dataclass
class K2000Options:
    """Operator-facing K2000 instrument knobs.

    Defaults reproduce the current shipping behaviour:

      * ``autorange=True``, ``range_V=None``  — driver picks range.
      * ``nplc=1.0``                          — ~20 ms per reading at 50 Hz.

    ``range_V`` is only applied when ``autorange`` is False; keeping
    it around across toggles means flipping autorange off restores
    the last manual range without the operator re-typing.
    """
    autorange: bool = True
    range_V: float = 10.0
    nplc: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "K2000Options":
        if not data:
            return cls()
        defaults = asdict(cls())
        merged = {**defaults, **{k: v for k, v in data.items()
                                   if k in defaults}}
        merged["autorange"] = bool(merged.get("autorange", True))
        try:
            r = float(merged.get("range_V", 10.0))
        except (TypeError, ValueError):
            r = 10.0
        # Clamp to the device's fixed range grid so a stray config
        # value never produces an OVRFLW at first Read.
        merged["range_V"] = min(K2000_RANGES_V,
                                 key=lambda x: abs(x - r))
        try:
            n = float(merged.get("nplc", 1.0))
        except (TypeError, ValueError):
            n = 1.0
        # Clamp to driver-documented window [0.01, 10] so the
        # spinner cannot send a value the instrument would reject.
        merged["nplc"] = max(0.01, min(10.0, n))
        return cls(**merged)


def apply_k2000_options(instrument, opts: K2000Options) -> Optional[str]:
    """Apply *opts* to an open K2000 instrument handle.

    Works uniformly against the real :class:`keithley_2000.Keithley2000DMM`
    and the :class:`fake_keithley_2000.FakeKeithley2000` because both
    implement ``set_voltage_range`` and ``set_nplc``.

    Returns an operator-facing summary string (suitable for
    ``append_log``) on success, or ``None`` if ``instrument`` is
    falsy (not connected — caller should log that separately).
    Never raises: individual setter failures are caught and returned
    as a warning string so the GUI callsite does not have to.
    """
    if instrument is None:
        return None
    errors: list[str] = []
    try:
        instrument.set_voltage_range(
            None if opts.autorange else float(opts.range_V))
    except Exception as exc:
        errors.append(f"range: {type(exc).__name__}: {exc}")
    try:
        instrument.set_nplc(float(opts.nplc))
    except Exception as exc:
        errors.append(f"NPLC: {type(exc).__name__}: {exc}")
    if errors:
        return "K2000 options applied with errors: " + "; ".join(errors)
    if opts.autorange:
        return f"K2000 options applied: autorange ON, NPLC={opts.nplc:g}"
    return (f"K2000 options applied: range={opts.range_V:g} V, "
            f"NPLC={opts.nplc:g}")


# ---------------------------------------------------------------------------
# Qt dialog — lazy imports keep the dataclass + helpers importable in
# headless contexts.
# ---------------------------------------------------------------------------
def open_k2000_options_dialog(
    options: K2000Options, parent=None
) -> K2000Options | None:
    """Modal wrapper.  Returns the updated options on OK, or None
    on Cancel — same idiom as the Single / Double options dialogs.
    """
    dlg = K2000OptionsDialog(options, parent=parent)
    if dlg.exec() == dlg.DialogCode.Accepted:
        return dlg.get_options()
    return None


class K2000OptionsDialog:
    """Compact dialog for the three operator-meaningful K2000 knobs.

    Layout:
      * Autorange checkbox (default ON).
      * Fixed-range combo — enabled only when autorange is OFF, so
        the operator cannot accidentally set a range that has no
        effect.  Entries are the device's native range grid.
      * NPLC combo + custom-value fallback via a slim spin box,
        because NPLC is the knob Triple-probe users most often
        want to retune for per-run speed/noise tradeoffs.
    """

    def __init__(self, options: K2000Options, parent=None):
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
            QCheckBox, QComboBox, QDoubleSpinBox, QLabel,
            QDialogButtonBox,
        )
        self._opts = options
        self._dlg = QDialog(parent)
        self._dlg.setWindowTitle("K2000 options")
        self._dlg.setMinimumWidth(380)
        self.DialogCode = QDialog.DialogCode

        try:
            from utils import setup_scrollable_dialog
            layout, scroll_top = setup_scrollable_dialog(self._dlg)
        except Exception:
            layout = QVBoxLayout(self._dlg)
            scroll_top = layout

        grp = QGroupBox("Measurement")
        form = QFormLayout(grp)

        self.chkAutorange = QCheckBox("Autorange ON")
        self.chkAutorange.setChecked(bool(options.autorange))
        self.chkAutorange.setToolTip(
            "Let the K2000 pick the voltage range automatically.\n"
            "Off: the fixed range below is used. Recommended for "
            "Triple-probe live measurement because autorange "
            "adds latency and occasional range-switch glitches.")
        form.addRow("Autorange:", self.chkAutorange)

        self.cmbRange = QComboBox()
        for r in K2000_RANGES_V:
            self.cmbRange.addItem(f"{r:g} V", r)
        # Pre-select closest match to the stored range.
        best = min(K2000_RANGES_V,
                    key=lambda x: abs(x - float(options.range_V)))
        idx = K2000_RANGES_V.index(best)
        self.cmbRange.setCurrentIndex(idx)
        self.cmbRange.setToolTip(
            "Fixed voltage range applied when autorange is OFF.\n"
            "Pick the smallest range that still covers the expected "
            "signal — the K2000's resolution scales with range.")
        form.addRow("Fixed range:", self.cmbRange)
        self.cmbRange.setEnabled(not self.chkAutorange.isChecked())
        # When the operator toggles autorange, grey/ungrey the range
        # combo so the dialog never suggests a fixed range is in
        # effect while autorange is on.
        self.chkAutorange.toggled.connect(
            lambda on: self.cmbRange.setEnabled(not on))

        self.spnNplc = QDoubleSpinBox()
        self.spnNplc.setRange(0.01, 10.0)
        self.spnNplc.setDecimals(2)
        self.spnNplc.setSingleStep(0.1)
        self.spnNplc.setSuffix(" NPLC")
        self.spnNplc.setValue(float(options.nplc))
        self.spnNplc.setToolTip(
            "Integration time in power-line cycles.\n"
            "Lower is faster but noisier (0.01 ≈ 200 us at 50 Hz, "
            "~4.5 digits).\n"
            "Higher is slower but quieter (10 ≈ 200 ms, ~6.5 digits).\n"
            "Typical Triple-probe live use: 0.1..1.0.")
        form.addRow("Integration:", self.spnNplc)

        # Quick-pick shortcut buttons for the documented presets —
        # saves the operator from spinning the spinner to a typical
        # value they already know they want.
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Presets:"))
        for n in K2000_NPLC_PRESETS:
            btn = QLabel(f"{n:g}")
            btn.setStyleSheet(
                "color:#9adcff; padding:2px 6px; border:1px solid #38404a;"
                " border-radius:3px;")
            btn.setCursor(type(btn.cursor())(
                __import__("PySide6.QtCore", fromlist=["Qt"])
                .Qt.CursorShape.PointingHandCursor))
            btn.mousePressEvent = (
                lambda _e, _n=n: self.spnNplc.setValue(float(_n)))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        form.addRow("", _wrap(preset_row))
        scroll_top.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._dlg.accept)
        btns.rejected.connect(self._dlg.reject)
        scroll_top.addWidget(btns)

    def exec(self):
        return self._dlg.exec()

    def get_options(self) -> K2000Options:
        r = float(self.cmbRange.currentData())
        return K2000Options(
            autorange=bool(self.chkAutorange.isChecked()),
            range_V=r,
            nplc=float(self.spnNplc.value()),
        )


def _wrap(hbox):
    """Wrap an HBoxLayout in a QWidget so it can sit in a QFormLayout
    row — Qt's QFormLayout takes widgets, not layouts, in the second
    column."""
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(hbox)
    return w


__all__ = [
    "K2000_RANGES_V", "K2000_NPLC_PRESETS",
    "K2000Options", "apply_k2000_options",
    "open_k2000_options_dialog", "K2000OptionsDialog",
]
