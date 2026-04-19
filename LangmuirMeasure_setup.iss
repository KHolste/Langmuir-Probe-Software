; LangmuirMeasure_setup.iss
; Inno Setup 6 installer script for the Langmuir-Probe Measurement
; application.
;
; Prerequisites:
;   1. Freeze the app with PyInstaller first:
;        python -m PyInstaller LangmuirMeasure.spec
;   2. Inno Setup 6: https://jrsoftware.org/isinfo.php
;   3. Compile via:
;        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" LangmuirMeasure_setup.iss
;      or run the bundled driver:
;        build.bat
;
; Optional prerequisite staging (see docs\INSTALL_prereqs.md):
;   * Drop "vc_redist.x64.exe" next to this .iss file to bundle and
;     silently chain the Microsoft VC++ 2015-2022 runtime.  If the file
;     is absent the installer simply skips that step.
;   * A system VISA backend (Keysight IO Libraries Suite or NI-VISA)
;     CANNOT be bundled — licensing forbids redistribution.  The
;     installer detects it and warns the operator if it is missing.

#define AppName      "Langmuir Probe Measurement"
#define AppVersion   "3.0"
#define AppPublisher "I. Physikalisches Institut, JLU Giessen"
#define AppURL       "https://www.uni-giessen.de/ipi"
#define AppExeName   "LangmuirMeasure.exe"
#define SourceDir    "dist\LangmuirMeasure"

; Detect at compile time whether vc_redist.x64.exe was staged next to
; this .iss file.  The preprocessor check keeps the installer valid
; with or without the redistributable present.
#define HaveVCRedist FileExists(AddBackslash(SourcePath) + "vc_redist.x64.exe")

[Setup]
; Stable AppId — keep this fixed across releases so upgrades overwrite
; the previous install instead of producing a second Add/Remove entry.
AppId={{C1D4E2F3-5A6B-4789-B0C1-2D3E4F5A6B7C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\LangmuirMeasure
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=LangmuirMeasure_v{#AppVersion}_setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
; Icon used by the installer binary itself (shown in Explorer and
; in the setup wizard's title bar).  The same .ico file is embedded
; into LangmuirMeasure.exe by the PyInstaller spec, so the installed
; app, Start-menu shortcut, desktop shortcut, and Add/Remove Programs
; entry all share one consistent visual.
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german";  MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#if HaveVCRedist
; Optional: bundled Microsoft VC++ 2015-2022 x64 runtime.  Staged to
; {tmp} and deleted after install so the final %ProgramFiles% tree
; stays clean.
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall
#endif
; Operator-facing prerequisite checklist is copied into the install
; folder so field support can refer to it without the build tree.
Source: "docs\INSTALL_prereqs.md"; DestDir: "{app}\docs"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
#if HaveVCRedist
; Chain VC++ redistributable BEFORE launching the frozen app so Qt /
; numpy / scipy DLLs find their CRT dependencies on first run.  /norestart
; is required: triggering a reboot from a silent install surprises
; lab operators.
Filename: "{tmp}\vc_redist.x64.exe"; \
    Parameters: "/install /quiet /norestart"; \
    StatusMsg: "Installing Microsoft VC++ 2015-2022 runtime..."; \
    Flags: runhidden waituntilterminated
#endif
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; App-private caches live under %LOCALAPPDATA%\JLU-IPI — clean them up
; on uninstall so no stale VISA cache / analysis history is left behind.
Type: filesandordirs; Name: "{localappdata}\JLU-IPI"

; ============================================================================
; [Code] — runtime environment checks
; ============================================================================
; The Langmuir-Measurement app talks to the Keysight B2901 SMU and the
; Keithley 2000 DMM through PyVISA, which requires a system-wide VISA
; library (visa32.dll / visa64.dll, registered under
; HKLM\SOFTWARE\IVI Foundation\VISA).  The setup itself installs fine
; without it, but GPIB connectivity will fail at first Connect click.
; We warn the operator rather than hard-block so an offline install on
; a machine without internet access can still proceed.
; ----------------------------------------------------------------------------
[Code]
function IsVisaInstalled: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{sys}\visa32.dll')) or
    FileExists(ExpandConstant('{sys}\visa64.dll')) or
    RegKeyExists(HKLM, 'SOFTWARE\IVI Foundation\VISA') or
    RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\IVI Foundation\VISA');
end;

function InitializeSetup(): Boolean;
var
  Msg: String;
begin
  Result := True;
  if not IsVisaInstalled then
  begin
    Msg :=
      'No system VISA backend was detected on this PC.' + #13#10 + #13#10 +
      'The Langmuir Measurement application uses VISA to talk to the ' +
      'Keysight B2901 SMU and the Keithley 2000 DMM (GPIB / USB / LAN).' + #13#10 + #13#10 +
      'Installation can continue, but GPIB connections will fail until ' +
      'one of the following is installed:' + #13#10 +
      '  - Keysight IO Libraries Suite (recommended)' + #13#10 +
      '  - NI-VISA' + #13#10 + #13#10 +
      'See docs\INSTALL_prereqs.md (bundled with this installer) for ' +
      'the full prerequisite checklist.' + #13#10 + #13#10 +
      'Continue installing anyway?';
    if MsgBox(Msg, mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
      Result := False;
  end;
end;
