"""
Buehler RPA – Theme-Definitionen und Qt-Stylesheet-Generator.

Ausgelagert aus BuehlerRPAmain.py (Phase 2 Refactoring).
"""
from __future__ import annotations

DARK_THEME = {
    "bg":        "#0b0d12",
    "panel":     "#161a24",
    "card":      "#1e2230",
    "border":    "#363d54",
    "accent":    "#4f8ef7",
    "accent2":   "#00d4aa",
    "accent3":   "#f7a14f",
    "danger":    "#f74f6e",
    "text":      "#e4e7f0",
    "text_sec":  "#9399b2",
    "led_red":   "#ff1744",
    "led_green": "#00e676",
    "led_grey":  "#37404f",
    "lcd_bg":    "#0d0d0d",
    "lcd_fg":    "#ff8c00",
    # Log-Farben (gedeckt, lesbar auf dunklem Hintergrund)
    "log_info":  "#b0b4c0",
    "log_ok":    "#5ccf8a",
    "log_warn":  "#e0b050",
    "log_error": "#f06060",
    "log_stamp": "#606478",
    # Matplotlib-Plot-Farben
    "plot_bg":   "#10131a",
    "plot_fg":   "#d0d4e0",
    "plot_grid": "#2e354a",
    "plot_fig":  "#161a24",
    "plot_done": "#4f8ef7",
    "plot_ahead": "#f7a14f",
    "plot_cursor": "#4f8ef7",
    "plot_deriv": "#e74c3c",
    "plot_crosshair": "#9399b2",
    "plot_tooltip_bg": "#1e2230",
    "plot_tooltip_border": "#363d54",
    "input_bg": "#131720",
}

LIGHT_THEME = {
    "bg":        "#eaecf0",
    "panel":     "#ffffff",
    "card":      "#f2f3f6",
    "border":    "#b8bcc8",
    "accent":    "#0062cc",
    "accent2":   "#1a7a3a",
    "accent3":   "#b8860b",
    "danger":    "#c62828",
    "text":      "#1a1a1e",
    "text_sec":  "#50546a",
    "led_red":   "#c62828",
    "led_green": "#1d8c3e",
    "led_grey":  "#a0a0a6",
    "lcd_bg":    "#111111",
    "lcd_fg":    "#0062cc",
    # Log-Farben (gedeckt, kontrastreich auf weißem Hintergrund)
    "log_info":  "#4a4a50",
    "log_ok":    "#1a6b35",
    "log_warn":  "#7a5500",
    "log_error": "#b71c1c",
    "log_stamp": "#80848e",
    # Matplotlib-Plot-Farben
    "plot_bg":   "#ffffff",
    "plot_fg":   "#1a1a1e",
    "plot_grid": "#cdd0d8",
    "plot_fig":  "#f2f3f6",
    "plot_done": "#0062cc",
    "plot_ahead": "#b8860b",
    "plot_cursor": "#0062cc",
    "plot_deriv": "#c62828",
    "plot_crosshair": "#a0a0a6",
    "plot_tooltip_bg": "#ffffff",
    "plot_tooltip_border": "#b8bcc8",
}


def build_stylesheet(t: dict) -> str:
    """Erzeugt ein komplettes Qt-Stylesheet aus einem Theme-Dictionary."""
    return f"""
/* ── Basis ───────────────────────────────────────────────────────── */
QWidget {{
    background-color: {t['bg']};
    color: {t['text']};
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
    font-size: 12px;
}}
QMainWindow, QDialog {{ background-color: {t['bg']}; }}

/* ── GroupBox: Hierarchie durch Fläche + Border ─────────────────── */
QGroupBox {{
    background-color: {t['card']};
    border: 1px solid {t['border']};
    border-radius: 7px;
    margin-top: 20px;
    padding: 10px 8px 6px 8px;
    font-weight: 600; font-size: 11px;
    color: {t['accent']}; letter-spacing: 0.5px;
    text-transform: uppercase;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; padding: 0 6px;
    background-color: {t['card']};
    color: {t['accent']};
}}

/* ── Buttons ────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {t['panel']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 14px;
    color: {t['text']};
    font-weight: 500;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: {t['card']};
    border-color: {t['accent']};
}}
QPushButton:pressed {{ background-color: {t['bg']}; border-color: {t['accent']}; }}
QPushButton:disabled {{
    color: #505570;
    border-color: #282d3c;
    background-color: {t['bg']};
}}
QPushButton:checked {{
    background-color: {t['accent']};
    color: #ffffff; font-weight: 600;
    border-color: {t['accent']};
}}

/* ── Labels ─────────────────────────────────────────────────────── */
QLabel {{ background: transparent; color: {t['text']}; }}

/* ── Eingabefelder ──────────────────────────────────────────────── */
QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox {{
    background-color: {t.get('input_bg', t['panel'])};
    border: 1px solid {t['border']};
    border-radius: 4px;
    padding: 4px 7px;
    color: {t['text']};
    selection-background-color: {t['accent']};
    min-height: 22px;
}}
QDoubleSpinBox:focus, QSpinBox:focus,
QLineEdit:focus, QComboBox:focus {{
    border-color: {t['accent']};
    border-width: 2px;
    padding: 3px 6px;
}}
QDoubleSpinBox:disabled, QSpinBox:disabled,
QLineEdit:disabled, QComboBox:disabled {{
    color: #505570; background-color: {t['bg']}; border-color: #282d3c;
}}
QDoubleSpinBox:read-only, QLineEdit:read-only {{
    background-color: {t['bg']}; color: {t['text_sec']};
}}

/* ── SpinBox Stepper ───────────────────────────────────────────── */
QDoubleSpinBox::up-button, QSpinBox::up-button,
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    background-color: {t['panel']}; border: 1px solid {t['border']}; width: 18px;
}}
QDoubleSpinBox::up-button, QSpinBox::up-button {{
    border-top-right-radius: 3px; subcontrol-position: top right; subcontrol-origin: border;
}}
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    border-bottom-right-radius: 3px; subcontrol-position: bottom right; subcontrol-origin: border;
}}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{
    background-color: {t['card']}; border-color: {t['accent']};
}}
QDoubleSpinBox::up-button:pressed, QSpinBox::up-button:pressed,
QDoubleSpinBox::down-button:pressed, QSpinBox::down-button:pressed {{
    background-color: {t['accent']};
}}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {t['text_sec']}; width: 0; height: 0;
}}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {t['text_sec']}; width: 0; height: 0;
}}
QDoubleSpinBox::up-arrow:hover, QSpinBox::up-arrow:hover {{ border-bottom-color: {t['text']}; }}
QDoubleSpinBox::down-arrow:hover, QSpinBox::down-arrow:hover {{ border-top-color: {t['text']}; }}

QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {t['panel']}; border: 1px solid {t['border']};
    selection-background-color: {t['accent']}; color: {t['text']};
    outline: none;
}}

/* ── Checkboxen & Radio: klar erkennbar in beiden Themes ───────── */
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: 7px;
    color: {t['text']};
    padding: 3px 0;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border-radius: 3px;
    border: 2px solid {t['text_sec']};
    background: {t['bg']};
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t['text']};
    background: {t['card']};
}}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
    border-color: {t['accent']};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {t['accent']};
    border-color: {t['accent']};
}}
QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {{
    border-color: {t['text']};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {t['border']};
    background: {t['panel']};
}}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background-color: {t['text_sec']};
    border-color: {t['text_sec']};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {t['text_sec']};
}}

/* ── Log-Textfeld ───────────────────────────────────────────────── */
QTextEdit {{
    background-color: {t['panel']};
    border: 1px solid {t['border']};
    border-radius: 4px; padding: 5px;
    color: {t['text']};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 11px;
    line-height: 1.4;
}}

/* ── Statusleiste ───────────────────────────────────────────────── */
QStatusBar {{
    background-color: {t['panel']};
    border-top: 1px solid {t['border']};
    color: {t['text_sec']}; font-size: 11px;
}}

QSplitter::handle {{ background-color: {t['border']}; }}

/* ── Scrollbar ──────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {t['bg']}; width: 8px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t['border']}; border-radius: 4px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Menüleiste ─────────────────────────────────────────────────── */
QMenuBar {{
    background: {t['panel']}; color: {t['text']};
    border-bottom: 1px solid {t['border']}; padding: 2px 4px;
}}
QMenuBar::item:selected {{
    background: {t['accent']}; color: #fff; border-radius: 3px;
}}
QMenu {{
    background: {t['panel']}; color: {t['text']};
    border: 1px solid {t['border']};
}}
QMenu::item:selected {{ background: {t['accent']}; color: #fff; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 3px 6px; }}

/* ── Tooltips ───────────────────────────────────────────────────── */
QToolTip {{
    background-color: {t['card']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 4px;
    padding: 4px 7px; font-size: 11px;
}}

/* ── Fortschrittsbalken ─────────────────────────────────────────── */
QProgressBar {{
    background-color: {t['panel']}; border: 1px solid {t['border']};
    border-radius: 4px; text-align: center; color: {t['text']};
}}
QProgressBar::chunk {{
    background-color: {t['accent']}; border-radius: 3px;
}}

/* ── Listbox ────────────────────────────────────────────────────── */
QListWidget {{
    background-color: {t['panel']}; border: 1px solid {t['border']};
    border-radius: 4px; color: {t['text']}; outline: none;
}}
QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
QListWidget::item:selected {{
    background: {t['accent']}; color: #fff;
}}
QListWidget::item:hover:!selected {{ background: {t['card']}; }}
"""
