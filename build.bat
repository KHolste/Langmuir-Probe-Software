@echo off
REM build.bat - Langmuir Probe Measurement - Build + Installer
REM =============================================================
REM Run from the project root (folder that contains LPmeasurement.py).
REM Build-env prereqs:
REM   pip install pyinstaller PySide6 matplotlib numpy scipy pandas ^
REM               pyvisa pyserial pyfiglet colorama
REM
REM Optional: `pip install pyvisa-py` to bundle the pure-Python VISA
REM fallback.  With it, RS232 (ASRL) and LAN (TCPIP) paths work on a
REM target PC that has no system VISA installed; GPIB still requires
REM a vendor VISA backend (Keysight IO Libraries Suite or NI-VISA)
REM because pyvisa_py has no Windows GPIB driver.
REM
REM Optional: drop "vc_redist.x64.exe" next to LangmuirMeasure_setup.iss
REM before running this script to chain the Microsoft VC++ 2015-2022
REM runtime into the installer (silent install).  See
REM docs\INSTALL_prereqs.md for details.

setlocal ENABLEEXTENSIONS

echo.
echo =============================================================
echo   Langmuir Probe Measurement - Build
echo   JLU Giessen - I. Physikalisches Institut
echo =============================================================
echo.

REM ---------- Step 1: Pre-flight build-env check ------------------
echo [1/4] Checking build environment...
python tools\check_langmuir_build_env.py
if errorlevel 1 (
    echo.
    echo ERROR: Build-env check failed. Fix missing modules and rerun.
    pause
    exit /b 1
)

REM ---------- Step 2: Clean previous freeze ----------------------
echo.
echo [2/4] Cleaning previous build...
if exist build\LangmuirMeasure rmdir /s /q build\LangmuirMeasure
if exist dist\LangmuirMeasure  rmdir /s /q dist\LangmuirMeasure

REM ---------- Step 3: PyInstaller ---------------------------------
echo.
echo [3/4] Running PyInstaller...
python -m PyInstaller --noconfirm LangmuirMeasure.spec
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed.
    pause
    exit /b 1
)

REM ---------- Step 4: Inno Setup ----------------------------------
echo.
echo [4/4] Building installer...
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if not exist %ISCC% (
    echo.
    echo Inno Setup 6 not found - skipping installer step.
    echo Frozen app is ready at:
    echo   dist\LangmuirMeasure\LangmuirMeasure.exe
    echo.
    echo To build the installer later:
    echo   1. Install Inno Setup 6: https://jrsoftware.org/isinfo.php
    echo   2. Run: ISCC.exe LangmuirMeasure_setup.iss
    goto :end
)

if not exist installer_output mkdir installer_output
%ISCC% LangmuirMeasure_setup.iss
if errorlevel 1 (
    echo.
    echo ERROR: Inno Setup compilation failed.
    pause
    exit /b 1
)

echo.
echo =============================================================
echo   SUCCESS
echo   Frozen app : dist\LangmuirMeasure\LangmuirMeasure.exe
echo   Installer  : installer_output\LangmuirMeasure_v3.0_setup.exe
echo =============================================================

:end
echo.
pause
endlocal
