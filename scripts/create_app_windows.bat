@echo off
REM scripts/create_app_windows.bat — creates a STORM shortcut on the Windows desktop.
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
SET "SHORTCUT=%USERPROFILE%\Desktop\STORM.lnk"

IF NOT EXIST "%LAUNCHER%" (
    echo ERROR: launch_storm.bat not found in %SCRIPTS_DIR%
    pause
    EXIT /B 1
)

echo Creating STORM shortcut on Desktop...

powershell -Command "& { ^
    $ws = New-Object -ComObject WScript.Shell; ^
    $s  = $ws.CreateShortcut('%SHORTCUT%'); ^
    $s.TargetPath      = '%LAUNCHER%'; ^
    $s.WorkingDirectory = '%PROJECT_DIR%'; ^
    $s.WindowStyle     = 0; ^
    if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%,0' }; ^
    $s.Save() ^
}"

IF ERRORLEVEL 1 (
    echo ERROR: Could not create shortcut.
    pause
    EXIT /B 1
)

echo.
echo Done.  STORM shortcut created on your Desktop.
echo.
IF NOT EXIST "%ICON%" (
    echo Note: no storm.ico found — the shortcut will use the default icon.
    echo       To add one, save a 1024x1024 PNG as storm.png then run:
    echo           python scripts\create_icon.py
    echo       Then re-run this script.
    echo.
)
pause
