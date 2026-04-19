# Langmuir Probe Measurement

A Windows desktop application for Langmuir-probe plasma diagnostics on
a lab bench with a Keysight **B2901 / B2910BL** SMU and a
**Keithley 2000** multimeter.  Written in Python 3.13 / PySide6,
frozen with PyInstaller, distributed as a single **Inno Setup**
installer.

---

## What the software does

* Guides a lab operator through **Single**, **Double**, **Triple** and
  **Cleaning** workflows for a Langmuir probe.
* Acquires an I–V sweep from the SMU, reads reference voltages from
  the K2000, fits the data with a transparent pipeline, and writes a
  versioned CSV + a per-analysis JSON sidecar.
* Reports **explicit fit status**, **95 % confidence intervals** for
  T<sub>e</sub>, I<sub>sat</sub> and n<sub>i</sub> (fit-only), and
  **classified VISA errors** with remediation hints.
* Runs fully offline on a bench PC — no cloud dependencies.

## Main measurement modes

| Mode | Primary output | Typical use |
|------|----------------|-------------|
| Single | T<sub>e</sub>, V<sub>f</sub>, V<sub>p</sub>, n<sub>e</sub> | Grounded plasmas with a usable wall reference |
| Double | T<sub>e</sub>, I<sub>sat</sub>, n<sub>i</sub> | RF / magnetised / floating-reference plasmas |
| Triple | T<sub>e</sub>, n<sub>e</sub> (live) | Fast transients, live monitoring |
| Cleaning | — | In-situ probe-tip reconditioning |

## Hardware context

* **Keysight B2901A/B or B2910BL** source-measure unit (voltage source + current measurement).
* **Keithley 2000** 6.5-digit DMM for reference voltage.
* **GPIB-USB adapter** (Keysight 82357B or NI GPIB-USB-HS), and/or RS232 for the K2000.
* Physical probe head with matched cables (Single / Double / Triple).

## Quickstart — from a clone

```bat
python -m pip install -U pip
python -m pip install numpy scipy pandas matplotlib PySide6 pyvisa pyserial pyfiglet colorama pyinstaller

rem run from source
python LPmeasurement.py

rem run the test suite
pytest

rem produce the installer
build.bat
```

`build.bat` runs a pre-build environment check, PyInstaller, and (if
Inno Setup 6 is installed) the installer compile, producing
`installer_output\LangmuirMeasure_v3.0_setup.exe`.

## Runtime prerequisites (target PC)

1. **Microsoft Visual C++ 2015–2022 x64 runtime** (`vc_redist.x64.exe`) — usually already present on Windows 10 / 11.  Staging it next to the .iss lets the installer chain it silently.
2. **A system VISA library**: *Keysight IO Libraries Suite* (recommended) **or** *NI-VISA*.  Installs `visa32.dll` / `visa64.dll` and the GPIB driver for your USB adapter.  The installer warns if neither is found.
3. **USB-to-RS232 adapter driver** (FTDI, Prolific, etc.) — only if the K2000 is wired through a USB converter.

Full checklist: [`docs/INSTALL_prereqs.md`](docs/INSTALL_prereqs.md).

## Main entry point

* Source run: `python LPmeasurement.py`
* Frozen app: `LangmuirMeasure.exe` (Start menu shortcut after install)

## Build / installer layout

```
LPmeasurement.py                  main window + method dispatcher
dlp_*.py                          pure analysis + option dialogs
keysight_b2901.py / keithley_2000.py  instrument drivers
fake_b2901*.py / fake_keithley_2000.py  sim stand-ins for tests
visa_errors.py                    VISA error classification
interface_discovery.py            Tools → Interface Discovery window
visa_persistence.py               last-used resource cache
analysis_options_sidecar.py       per-analysis JSON sidecar
dlp_csv_schema.py                 versioned CSV banner
LangmuirMeasure.spec              PyInstaller spec (REQUIRED_LOCAL)
LangmuirMeasure_setup.iss         Inno Setup script
build.bat                         one-shot build driver
tools/check_langmuir_build_env.py pre-build import-sanity check
tests/                            ~1000-test pytest suite
docs/                             user manual + install checklist
```

## Status and scope

Current release: **v3.0**, centred on `LPmeasurement.py`.  Single /
Double / Triple analysis paths are production-grade with explicit
status, uncertainty, and compliance reporting.  Known limitations
and future work are tracked in the [developer handbook](docs/LangmuirMeasure_Documentation.md)
(Section D.8).

The legacy V2 standalone window is shipped only as shared widget
code for LP; it is not the primary user-facing workflow.

## Full documentation

* **End-user manual (bilingual EN + DE):**
  [`docs/LangmuirMeasure_Documentation.docx`](docs/LangmuirMeasure_Documentation.docx)
  (outline: [`docs/LangmuirMeasure_Documentation.md`](docs/LangmuirMeasure_Documentation.md)).
* **Prerequisite checklist:** [`docs/INSTALL_prereqs.md`](docs/INSTALL_prereqs.md).
* **Per-analysis help:** in-app "Help" buttons on the Single and Double
  options dialogs.

## License / contact

Internal project of I. Physikalisches Institut, JLU Giessen.  Issues,
feature requests, and patches via the repository's issue tracker.
