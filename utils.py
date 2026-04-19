"""
Buehler RPA – allgemeine Hilfsfunktionen.

Ausgelagert aus BuehlerRPAmain.py (Phase 7 Refactoring).
"""
from __future__ import annotations

import html
import os
import platform
import serial
import time
from datetime import datetime

from pyfiglet import figlet_format
from colorama import init

from PySide6.QtWidgets import (
    QDialog, QFrame, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt


# ---------------------------------------------------------------------------
# Logging in das GUI-Textfeld
# ---------------------------------------------------------------------------

def _find_log_widget(window):
    try:
        return window.findChild(QTextEdit, "txtLog")
    except Exception:
        return None


def append_log(window, text: str, level: str = "info") -> None:
    widget = _find_log_widget(window)
    if widget is None:
        return

    # Theme-aware log colors: read from active theme if available on window
    _fallback = {
        "log_info": "#ffffff", "log_ok": "#00ff7f",
        "log_warn": "#ffd166", "log_error": "#ff6b6b",
        "log_stamp": "#888888",
    }
    t = getattr(window, "_theme", None) or _fallback
    _level_map = {"info": "log_info", "ok": "log_ok", "warn": "log_warn", "error": "log_error"}
    color = t.get(_level_map.get(level, "log_info"), _fallback["log_info"])
    stamp_color = t.get("log_stamp", _fallback["log_stamp"])

    stamp = datetime.now().strftime("%H:%M:%S")
    safe_text = html.escape(str(text))
    line = (
        f'<span style="color:{stamp_color}">[{stamp}]</span> '
        f'<span style="color:{color}">{safe_text}</span>'
    )

    widget.append(line)

    doc = widget.document()
    max_blocks = 500
    while doc.blockCount() > max_blocks:
        cursor = widget.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deleteChar()


# ---------------------------------------------------------------------------
# LED-Indikatoren
# ---------------------------------------------------------------------------

def set_led(frame: QFrame, color: str, size_px: int = 16):
    r = size_px // 2
    frame.setStyleSheet(f"""
        background-color: {color};
        border-radius: {r}px;
        border: 1px solid black;
    """)


def _make_led(parent=None) -> QFrame:
    """Create a small round LED indicator frame."""
    f = QFrame(parent)
    f.setFixedSize(16, 16)
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def _set_led(led: QFrame, color: str) -> None:
    led.setStyleSheet(
        f"background-color: {color}; border-radius: 8px; border: 1px solid #333;")


# ---------------------------------------------------------------------------
# GUI-Helfer
# ---------------------------------------------------------------------------

def _vsep() -> QFrame:
    """Thin vertical separator line for toolbars."""
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep


# ---------------------------------------------------------------------------
# Scrollable-dialog helper (small-display hardening)
# ---------------------------------------------------------------------------

def setup_scrollable_dialog(
    dialog: "QDialog",
    *,
    max_height_ratio: float = 0.9,
):
    """Build a scroll-wrapped dialog skeleton.  Call FIRST in __init__.

    Returns ``(content_layout, button_layout)``:

    * ``content_layout``  — a fresh ``QVBoxLayout`` belonging to an
      inner ``QWidget`` that lives inside a ``QScrollArea`` (both
      scrollbar policies = ``AsNeeded``).  All form widgets / group
      boxes go in here.
    * ``button_layout``   — the dialog's top-level ``QVBoxLayout``,
      below the scroll area.  The ``QDialogButtonBox`` (OK/Cancel)
      goes here so it stays visible while the form scrolls.

    Caps ``dialog.maximumHeight()`` at ``max_height_ratio`` of the
    available screen height so the dialog never spawns taller than
    the user's monitor on small laptops.

    Construction-time hardening (vs. an end-of-__init__ wrapper)
    avoids reparenting widgets after they have already been registered
    with the dialog — a pattern that triggered an access violation on
    PySide6 + Win64 in earlier prototypes.
    """
    top = QVBoxLayout(dialog)
    top.setContentsMargins(6, 6, 6, 6)
    top.setSpacing(6)

    # Create QScrollArea with the dialog as parent so Qt manages the
    # whole sub-tree as one and Python attribute holds are unnecessary.
    scroll = QScrollArea(dialog)
    scroll.setObjectName("dlpScrollWrap")
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setFrameShape(QFrame.Shape.NoFrame)

    inner = QWidget(scroll)
    scroll.setWidget(inner)
    top.addWidget(scroll, 1)

    content = QVBoxLayout(inner)

    try:
        screen = dialog.screen() if hasattr(dialog, "screen") else None
        if screen is not None:
            avail = screen.availableGeometry().height()
            if avail > 0:
                dialog.setMaximumHeight(int(avail * max_height_ratio))
    except Exception:
        pass

    # First-show hardening: when the dialog is finally shown, the
    # inner scroll-area widget has been fully populated by the
    # caller, so its sizeHint is now meaningful.  We use that hint
    # to ensure the dialog opens *at least* large enough to display
    # every widget without scrollbar clipping — instead of relying
    # on Qt's small default exec() size that often hides the OK
    # button on dense forms.
    _install_first_show_resize(dialog, scroll, inner, max_height_ratio)

    return content, top


def _install_first_show_resize(dialog, scroll, inner, max_height_ratio):
    """Install a one-shot showEvent hook that resizes the dialog to
    fit its populated content the first time it is shown.

    Implemented by monkey-patching ``dialog.showEvent`` while keeping
    the original method for chained dispatch.  The resize runs only
    once; subsequent shows respect whatever size the user dragged
    the dialog to.
    """
    original_show = dialog.showEvent
    fired = {"done": False}

    def _patched_show(event):
        try:
            original_show(event)
        finally:
            if fired["done"]:
                return
            fired["done"] = True
            try:
                # Refresh inner widget's sizeHint after population.
                inner.adjustSize()
                hint_w = inner.sizeHint().width()
                hint_h = inner.sizeHint().height()
                # Account for the scroll area's frame + dialog
                # margins + the button box height (~50 px).
                pad_w = 40
                pad_h = 70
                want_w = max(dialog.minimumWidth(), hint_w + pad_w,
                             dialog.width())
                want_h = max(dialog.minimumHeight(), hint_h + pad_h,
                             dialog.height())
                # Cap height by the screen-derived maximum so the
                # dialog never spawns taller than the monitor.
                screen = (dialog.screen()
                          if hasattr(dialog, "screen") else None)
                if screen is not None:
                    avail = screen.availableGeometry().height()
                    if avail > 0:
                        want_h = min(want_h,
                                      int(avail * max_height_ratio))
                dialog.resize(want_w, want_h)
            except Exception:
                pass

    dialog.showEvent = _patched_show


# ---------------------------------------------------------------------------
# Konsole / Banner
# ---------------------------------------------------------------------------

def print_banner() -> None:
    init()

    GREEN = "\033[92m"
    CYAN = "\033[96m"
    RESET = "\033[0m"

    banner = figlet_format("Buehler RPA", font="slant")

    subtitle = """
Version 1.0  (basierend auf Ref4EP v3.1)
JLU Giessen – IPI
"""

    print(GREEN + banner + RESET)
    print(CYAN + subtitle + RESET)
    print("-" * 80)


def clear_console():
    if platform.system() == "Windows":
        os.system("cls")
    else:
        os.system("clear")


def clear_console2():
    # Robust: cls/clear
    os.system("cls" if platform.system() == "Windows" else "clear")


# ---------------------------------------------------------------------------
# Plot helpers — clean axis ticks for scientific plots
# ---------------------------------------------------------------------------

def apply_clean_axis_format(ax) -> None:
    """Disable offset and scientific-notation badges on linear axes.

    Removes the cryptic ``+1e-3`` / ``×10⁶`` text that Matplotlib
    likes to render above or beside tick labels, so the axis values
    are directly readable without mental arithmetic.

    Log-scale axes are skipped — they rely on their log formatter.

    Safe to call repeatedly and on partially-built axes.
    """
    for axis_name in ("x", "y"):
        try:
            scale = (ax.get_xscale() if axis_name == "x"
                     else ax.get_yscale())
            if scale != "linear":
                continue
            ax.ticklabel_format(axis=axis_name, useOffset=False,
                                style="plain")
        except (AttributeError, TypeError, ValueError):
            # Some formatters (e.g. category, date) reject these
            # kwargs — leave them alone in that case.
            pass


# ---------------------------------------------------------------------------
# Diagnose-Helfer
# ---------------------------------------------------------------------------

def probe_port(port="COM16", baud=115200, term="\r\n"):
    with serial.Serial(port, baudrate=baud, timeout=0.3) as ser:
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        def tx(cmd):
            ser.write((cmd + term).encode("ascii", errors="replace"))
            ser.flush()

        def rx_window(t=0.8):
            t0 = time.time()
            lines = []
            while time.time() - t0 < t:
                raw = ser.readline()
                if raw:
                    lines.append(raw)
            return lines

        # 1) Terminator setzen (FuG Probus)
        tx("Y0")
        time.sleep(0.05)
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        # 2) IDN?
        tx("*IDN?")
        lines = rx_window(1.0)

        print(f"--- {port} @ {baud} ---")
        if not lines:
            print("NO RESPONSE")
        else:
            for r in lines:
                print(repr(r), "->", r.decode(errors="ignore").strip())


def myjob():
    from devices.psu_fug import FugPSU
    ion = ppa_up = ppa_low = einzel = None
    try:
        ion = FugPSU(mode="tcp", host="192.168.1.93", tcp_port=2101, timeout=2.0)
        ion.connect()
        print("ion energy: ", ion.idn())

        ppa_up = FugPSU(mode="visa", visa_resource="GPIB0::9::INSTR", timeout=2.0)
        ppa_up.connect()
        print("PPA upper plate: ", ppa_up.idn())

        ppa_low = FugPSU(mode="serial", port="COM16", baudrate=9600, timeout=1.0)
        ppa_low.connect()
        print("PPA lower plate: ", ppa_low.idn())

        time.sleep(0.2)

        einzel = FugPSU(mode="tcp", host="192.168.1.91", tcp_port=2101, timeout=2.0)
        einzel.connect()
        print("einzel lens: ", einzel.idn())

    finally:
        for dev in (einzel, ppa_low, ppa_up, ion):
            if dev:
                dev.close()
