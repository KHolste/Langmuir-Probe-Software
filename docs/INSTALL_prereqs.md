# Langmuir Probe Measurement — Target-PC Prerequisite Checklist

The PyInstaller bundle produced by `build.bat` contains the Python
runtime, Qt 6, NumPy / SciPy / pandas, PyVISA, and the full LP
measurement application. It does **not** contain the system-level
VISA / GPIB stack — Keysight's and NI's licenses forbid bundling
those installers inside a third-party setup.

This checklist lists every component that must exist on the target
Windows lab PC for the installed application to connect to real
instruments.

---

## 1. Microsoft Visual C++ 2015-2022 x64 runtime

- **Required.** Qt 6, NumPy, SciPy and pandas DLLs are built against
  this CRT.
- Modern Windows 10 / 11 usually ships it. If the first launch of
  `LangmuirMeasure.exe` fails with a message about `MSVCP140.dll` or
  `VCRUNTIME140.dll`, install it.
- **Redistributable filename:** `vc_redist.x64.exe` (Microsoft-signed,
  redistribution permitted).
- **Installer integration:** stage `vc_redist.x64.exe` next to
  `LangmuirMeasure_setup.iss` before running `build.bat`. The `.iss`
  file detects it at compile time and chains a silent install.

## 2. System VISA backend

The application's default transport is GPIB via PyVISA. PyVISA needs
a shared-library VISA implementation on the target PC. Pick **one**:

### Option A — Keysight IO Libraries Suite (recommended for JLU-IPI)
- Free from https://www.keysight.com/find/iosuite
- Matches the Keysight B2901 SMU natively and brings the Keysight
  Connection Expert (useful for first-time bench diagnostics).
- Installs `visa32.dll` / `visa64.dll`, registers
  `HKLM\SOFTWARE\IVI Foundation\VISA`, and installs the GPIB driver
  for Keysight 82357B / USB-GPIB adapters.
- ~400 MB download, requires a reboot.

### Option B — NI-VISA
- Free from https://www.ni.com/en-us/support/downloads/drivers/
- Preferred if the bench uses an NI GPIB-USB-HS adapter.
- Installs the same `visa32.dll` / `visa64.dll` entry points plus
  NI-488.2 drivers (kernel-level GPIB stack).
- ~1.5 GB download.

> The installer automatically checks for a system VISA at install
> time and warns the operator if neither is present. Installation is
> not blocked — RS232 and LAN connections may still work if the
> build bundled `pyvisa_py` (see section 3) — but a first **GPIB**
> Connect click will fail without a real VISA library.

## 3. Optional: PyVISA pure-Python fallback (build-time choice)

`pyvisa_py` is a pure-Python VISA backend that PyInstaller can bundle
into the frozen app. With it, the target PC can open:

| Resource type | Works via `pyvisa_py` alone? |
|---|---|
| `ASRL…::INSTR` (RS232) | ✅ yes, uses pyserial |
| `TCPIP…::INSTR` (raw sockets) | ✅ yes |
| `USB…::INSTR` (USBTMC) | ⚠️  requires `pyusb` + libusb |
| `GPIB…::INSTR` | ❌ no — no Windows GPIB driver |

To include it, run `pip install pyvisa-py` in the build environment
**before** running `build.bat`. The spec picks it up automatically
and logs `pyvisa_py detected — bundled as VISA fallback backend.`

This is useful for bench checkouts (K2000 over RS232 only) and for
smoke-testing an installer on a machine that has no GPIB adapter at
all. It is **not** a substitute for Keysight IO Libraries / NI-VISA
on the production Langmuir PC.

## 4. GPIB adapter driver

- Provided by whichever VISA backend was installed in section 2:
  - Keysight IO Libraries → supports Keysight 82357 / USB-GPIB adapters.
  - NI-VISA + NI-488.2 → supports NI GPIB-USB-HS and similar.
- **Not bundled, not chainable** — ships only with the vendor's VISA
  installer.

## 5. RS232 / serial

- Uses PyVISA's ASRL transport (or, for the `utils.probe_port`
  diagnostic only, raw pyserial). No additional driver beyond
  Windows' built-in COM port support is required.
- If the K2000 is connected through a USB ↔ RS232 adapter, install
  that adapter's vendor driver (FTDI VCP, Prolific, etc.) exactly
  once; it enumerates as a standard COM port after that.

## 6. Python on the target PC

- **Not required.** PyInstaller freezes a complete Python 3.13
  interpreter into the installation. The target PC does not need
  Python installed or on `PATH`.

---

## Operator install sequence

1. *(If offered by build tree)* Run **`vc_redist.x64.exe`** (skip if
   Windows already has it — the installer is a no-op in that case).
2. Run **Keysight IO Libraries Suite** (or NI-VISA) — reboot when
   prompted.
3. Plug in the GPIB-USB adapter and verify it appears in the
   vendor's connection tool (Keysight Connection Expert / NI MAX).
4. Run **`LangmuirMeasure_v3.0_setup.exe`**. If step 2 was skipped
   the installer will warn but still proceed.
5. Launch **Langmuir Probe Measurement** from the Start menu. On
   first Connect, the VISA scan should list the B2901 (typically
   `GPIB0::23::INSTR`) and the K2000 (`GPIB0::9::INSTR`). Selecting
   a resource and clicking Connect should return an `*IDN?` reply.
6. If Connect fails with `VI_ERROR_LIBRARY_NFOUND` → step 2 is
   missing. If it fails with `VI_ERROR_RSRC_NFOUND` → adapter /
   address mismatch; re-check in step 3.

## What the installer does vs. does not do

| Task | Handled by installer? |
|------|-----------------------|
| Copy frozen app to `%ProgramFiles%\LangmuirMeasure` | ✅ |
| Create Start-menu / desktop shortcuts | ✅ |
| Chain `vc_redist.x64.exe` (if staged) | ✅ |
| Detect missing VISA and warn | ✅ |
| Install Keysight IO Libraries / NI-VISA | ❌ (license) |
| Install GPIB kernel driver | ❌ (ships with VISA) |
| Install USB-RS232 adapter driver | ❌ (vendor-specific) |
| Clean `%LOCALAPPDATA%\JLU-IPI` on uninstall | ✅ |
