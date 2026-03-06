@echo off
REM scripts/update.bat — pull the latest STORM code.
REM Run from the project root before a chase day (double-click or run from terminal).

FOR %%I IN ("%~dp0..") DO SET "PROJECT_DIR=%%~fI"

echo ════════════════════════════════════════════════
echo   STORM Updater
echo ════════════════════════════════════════════════
echo.

where git >nul 2>&1
IF ERRORLEVEL 1 (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('git is not installed or not on PATH.`n`nInstall Git for Windows from https://git-scm.com', 'STORM Update Error', 'OK', 'Error')"
    EXIT /B 1
)

cd /d "%PROJECT_DIR%"
echo Pulling latest code from GitHub...
git pull
IF ERRORLEVEL 1 (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('git pull failed.`n`nThis usually means you have local uncommitted changes conflicting with the update.`n`nCheck ''git status'' and resolve any conflicts, then re-run this script.', 'STORM Update Error', 'OK', 'Error')"
    EXIT /B 1
)

echo.
echo Updating Desktop shortcut...
CALL "%PROJECT_DIR%\scripts\create_app_windows.bat" >nul 2>&1

echo.
echo ════════════════════════════════════════════════
echo   Update complete! Launch STORM normally.
echo ════════════════════════════════════════════════
pause
