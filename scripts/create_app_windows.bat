@echo off
REM scripts/create_app_windows.bat — creates a STORM shortcut on the Desktop and attempts to pin to taskbar.
REM Run once from the project root after cloning the repo.
REM
REM Requirements:
REM   - storm.ico in the project root (run: python scripts/create_icon.py)
REM   - PowerShell available (built into Windows 7+)

REM Resolve project root (one level above scripts/) and scripts dir
FOR %%I IN ("%~dp0..") DO SET "PROJECT_DIR=%%~fI"
SET "SCRIPTS_DIR=%~dp0"

SET "LAUNCHER=%SCRIPTS_DIR%launch_storm.bat"
SET "ICON=%PROJECT_DIR%\storm.ico"
SET "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\STORM.lnk"
SET "STARTMENU_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\STORM.lnk"

IF NOT EXIST "%LAUNCHER%" (
    echo ERROR: launch_storm.bat not found in %SCRIPTS_DIR%
    EXIT /B 1
)

echo Creating STORM shortcuts on Desktop and Start Menu...

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$targets = @('%DESKTOP_SHORTCUT%','%STARTMENU_SHORTCUT%'); " ^
  "foreach ($t in $targets) { " ^
  "  $s = $ws.CreateShortcut($t); " ^
  "  $s.TargetPath = '%LAUNCHER%'; " ^
  "  $s.WorkingDirectory = '%PROJECT_DIR%'; " ^
  "  $s.WindowStyle = 1; " ^
  "  if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%,0' }; " ^
  "  $s.Save(); " ^
  "} " ^
  "$pinned = $false; " ^
  "try { " ^
  "  $shell = New-Object -ComObject Shell.Application; " ^
  "  $folder = $shell.Namespace((Split-Path '%STARTMENU_SHORTCUT%')); " ^
  "  $item = $folder.ParseName((Split-Path '%STARTMENU_SHORTCUT%' -Leaf)); " ^
  "  if ($item) { " ^
  "    $verb = $item.Verbs() | Where-Object { $_.Name -match 'Pin to Taskbar' -or $_.Name -match 'Taskbar' } | Select-Object -First 1; " ^
  "    if ($verb) { $verb.DoIt(); $pinned = $true } " ^
  "  } " ^
  "} catch { } " ^
  "if (-not $pinned) { Write-Host 'Note: Taskbar pin was not applied automatically. You can pin STORM from the Start Menu shortcut.' }"

IF ERRORLEVEL 1 (
    echo ERROR: Could not create shortcut.
    EXIT /B 1
)

echo.
echo Done.  STORM shortcuts created on Desktop and Start Menu.
echo.
IF NOT EXIST "%ICON%" (
    echo Note: no storm.ico found — the shortcut will use the default icon.
    echo       To add one, save a 1024x1024 PNG as storm.png then run:
    echo           python scripts\create_icon.py
    echo       Then re-run this script.
    echo.
)
