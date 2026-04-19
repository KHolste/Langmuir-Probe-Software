# -*- mode: python ; coding: utf-8 -*-
# LangmuirMeasure.spec — PyInstaller freeze for the Langmuir-Probe
# Measurement application.
#
# Entry point : LPmeasurement.py  (formerly DLPMainWindowV3)
# Output exe  : dist\LangmuirMeasure\LangmuirMeasure.exe
#
# Build with:
#   python -m PyInstaller LangmuirMeasure.spec
# or via the bundled driver:
#   build.bat

block_cipher = None

# ---------------------------------------------------------------------------
# Local project modules reachable from LPmeasurement.py.  Listed as hidden
# imports so any lazy/runtime-only "from foo import bar" is still pulled
# into the freeze, and as REQUIRED_LOCAL so the pre-build sanity loop can
# fail fast when a module was moved or renamed.
# ---------------------------------------------------------------------------
REQUIRED_LOCAL = [
    # Entry point and its direct V2/V3 dependencies.
    'LPmeasurement',
    'DoubleLangmuir_measure',
    'DoubleLangmuir_measure_v2',
    'DoubleLangmuir_measure_v3',
    'DoubleLangmuirAnalysis_v2',
    # Shared infrastructure.
    'analysis_history',
    'analysis_log_window',
    'analysis_options_sidecar',
    'clipping_heuristic',
    'dlp_csv_schema',
    'paths',
    'theme',
    'utils',
    'visa_errors',
    'visa_persistence',
    # Instrument drivers (real + fakes).
    'fake_b2901',
    'fake_b2901_v2',
    'fake_keithley_2000',
    'keithley_2000',
    'keysight_b2901',
    # DLP dialog / analysis / options family used by the LP window.
    'dlp_cleaning_dialog',
    'dlp_double_analysis',
    'dlp_double_help',
    'dlp_double_options',
    'dlp_experiment_dialog',
    'dlp_k2000_options',
    'dlp_fit_models',
    'dlp_instrument_dialog',
    'dlp_lp_plot_settings_dialog',
    'dlp_lp_window',
    'dlp_plot_settings_dialog',
    'dlp_probe_dialog',
    'dlp_save_paths',
    'dlp_sim_dialog',
    'dlp_single_analysis',
    'dlp_single_help',
    'dlp_single_options',
    'dlp_triple_analysis',
    'dlp_triple_dataset',
    'dlp_triple_help',
    'dlp_triple_window',
    'dlp_triple_worker',
    'interface_discovery',
]

HIDDEN = [
    # Qt
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    # Matplotlib
    'matplotlib.backends.backend_qtagg',
    'matplotlib.backends.backend_agg',
    # Scientific
    'numpy',
    'pandas',
    'scipy.optimize',
    'scipy.signal',
    'scipy.special',
    # VISA / serial instrumentation
    'pyvisa',
    'serial',
    'serial.tools',
    'serial.tools.list_ports',
    # CLI helpers used by utils.py
    'pyfiglet',
    'pyfiglet.fonts',
    'colorama',
    'configparser',
] + list(REQUIRED_LOCAL)

# ---------------------------------------------------------------------------
# Build-time hardening.  Abort the freeze early when the build env is
# missing a required local module or a required runtime library — a
# silent bundle that crashes on Connect/Analyse is much worse than a
# loud build error here.
# ---------------------------------------------------------------------------
import importlib
import os
import sys


def _spec_log(msg):
    print('[spec] ' + msg, flush=True)


# 1. pyvisa_py is an optional pure-Python fallback backend.  Only bundle
#    it if the build env actually has it; otherwise the target machine
#    must supply NI-VISA or Keysight IO Libraries.
try:
    importlib.import_module('pyvisa_py')
    HIDDEN.append('pyvisa_py')
    _spec_log('pyvisa_py detected — bundled as VISA fallback backend.')
except ImportError:
    _spec_log('pyvisa_py NOT installed in build env — skipping. '
              'Target machine must provide NI-VISA or Keysight IO '
              'Libraries Suite for real hardware.')


# 2. REQUIRED_LOCAL sanity check.
_failed = []
for _name in REQUIRED_LOCAL:
    try:
        importlib.import_module(_name)
    except Exception as exc:
        _failed.append((_name, repr(exc)))

if _failed:
    _spec_log('FATAL: required local modules not importable from the '
              'build env:')
    for _name, _why in _failed:
        _spec_log('  - %s: %s' % (_name, _why))
    raise SystemExit(
        '[spec] aborting build — fix the missing imports above and '
        'rerun.  A frozen build without these modules would break at '
        'runtime (Connect, Analyse-Log, VISA persistence).')


# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
DATAS = []

# pyfiglet fonts are loaded via pkg_resources at runtime.
try:
    import pyfiglet as _pf
    _pf_fonts = os.path.join(os.path.dirname(_pf.__file__), 'fonts')
    if os.path.isdir(_pf_fonts):
        DATAS.append((_pf_fonts, 'pyfiglet/fonts'))
except ImportError:
    _spec_log('WARNING: pyfiglet not in build env — banner may fail at '
              'runtime.')

# Ship default JSON configs next to the exe so a first launch has
# sensible defaults even before the user writes to %LOCALAPPDATA%.
for _cfg in ('langmuir_config.json', 'dlp_config.json'):
    if os.path.isfile(_cfg):
        DATAS.append((_cfg, '.'))


# ---------------------------------------------------------------------------
# Analysis / PYZ / EXE / COLLECT
# ---------------------------------------------------------------------------
a = Analysis(
    ['LPmeasurement.py'],
    pathex=['.'],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LangmuirMeasure',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console=False — shipped as a windowed GUI; flip to True temporarily
    # when debugging freeze-only crashes to expose Python tracebacks.
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LangmuirMeasure',
)
