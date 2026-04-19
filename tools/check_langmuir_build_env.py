"""Pre-build sanity check for the Langmuir-Measurement freeze.

Run this manually (or via build.bat) before kicking off PyInstaller to
verify that every local module the LP path needs, plus the required
runtime libraries, are importable from the current Python environment.
Failure here always means the resulting PyInstaller bundle would be
broken at runtime - much cheaper to catch *before* the build.

The same loop runs inside ``LangmuirMeasure.spec`` so a forgotten
manual check still cannot ship a silently broken build, but having a
standalone entry-point keeps the failure message human-readable in
day-to-day operations.

Exit codes:
    0  - all required modules importable, build env looks healthy.
    1  - at least one required module missing; details on stderr.
"""
from __future__ import annotations

import importlib
import os
import sys

# Mirror of LangmuirMeasure.spec REQUIRED_LOCAL (kept in sync by
# tests/test_langmuir_build_spec.py).
REQUIRED_LOCAL = [
    'LPmeasurement',
    'DoubleLangmuir_measure',
    'DoubleLangmuir_measure_v2',
    'DoubleLangmuir_measure_v3',
    'DoubleLangmuirAnalysis_v2',
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
    'fake_b2901',
    'fake_b2901_v2',
    'fake_keithley_2000',
    'keithley_2000',
    'keysight_b2901',
    'dlp_cleaning_dialog',
    'dlp_double_analysis',
    'dlp_double_help',
    'dlp_double_options',
    'dlp_experiment_dialog',
    'dlp_experiment_help',
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
    'ion_composition_presets',
]

# Runtime libraries the freeze depends on.
RUNTIME_LIBS = [
    ('PySide6.QtWidgets', 'required'),
    ('matplotlib.backends.backend_qtagg', 'required'),
    ('numpy', 'required'),
    ('pandas', 'required'),
    ('scipy.optimize', 'required'),
    ('pyvisa', 'required'),
    ('pyvisa_py', 'optional VISA fallback'),
    ('serial', 'required'),
    ('pyfiglet', 'required'),
    ('colorama', 'required'),
]


def _ensure_repo_on_syspath() -> None:
    """Allow running this from the tools/ folder without PYTHONPATH."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def main() -> int:
    _ensure_repo_on_syspath()

    print('=== Langmuir-Measurement build-env sanity check ===')
    print('Python:', sys.version.replace('\n', ' '))
    print('Platform:', sys.platform)
    print()

    print('[1/2] Required local modules:')
    failed: list[tuple[str, str]] = []
    for name in REQUIRED_LOCAL:
        try:
            importlib.import_module(name)
            print(f'  OK   {name}')
        except Exception as exc:
            failed.append((name, repr(exc)))
            print(f'  FAIL {name}: {exc}', file=sys.stderr)

    print()
    print('[2/2] Runtime libraries:')
    for name, status in RUNTIME_LIBS:
        try:
            importlib.import_module(name)
            print(f'  OK   {name}  ({status})')
        except Exception as exc:
            level = 'WARN' if status.startswith('optional') else 'FAIL'
            stream = sys.stderr if level == 'FAIL' else sys.stdout
            print(f'  {level} {name}  ({status}): {exc}', file=stream)
            if level == 'FAIL':
                failed.append((name, repr(exc)))

    print()
    if failed:
        print(f'FAILED: {len(failed)} required import(s) missing - '
              f'fix and rerun before building.', file=sys.stderr)
        return 1
    print('Build env OK - safe to run PyInstaller / build.bat.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
