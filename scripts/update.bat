@echo off
REM scripts/update.bat — pull the latest STORM code and update the conda environment.
REM Run from the project root before a chase day (double-click or run from terminal).

SETLOCAL ENABLEDELAYEDEXPANSION

FOR %%I IN ("%~dp0..") DO SET "PROJECT_DIR=%%~fI"
SET "CONDA_BAT="

echo ════════════════════════════════════════════════
echo   STORM Updater
echo ════════════════════════════════════════════════

REM ── 1. Pull latest code ────────────────────────────────────────────────────────

echo.
echo Pulling latest code from GitHub...

where git >nul 2>&1
IF ERRORLEVEL 1 (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('git is not installed or not on PATH.`n`nInstall Git for Windows from https://git-scm.com', 'STORM Update Error', 'OK', 'Error')"
    EXIT /B 1
)

cd /d "%PROJECT_DIR%"
git pull
IF ERRORLEVEL 1 (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('git pull failed.`n`nThis usually means you have local uncommitted changes conflicting with the update.`n`nCheck ''git status'' and resolve any conflicts, then re-run this script.', 'STORM Update Error', 'OK', 'Error')"
    EXIT /B 1
)

REM ── 2. Find conda ──────────────────────────────────────────────────────────────

echo.
echo Updating conda environment...

FOR %%D IN (
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%LOCALAPPDATA%\miniforge3"
    "%LOCALAPPDATA%\miniconda3"
    "%LOCALAPPDATA%\anaconda3"
    "%PROGRAMDATA%\miniforge3"
    "%PROGRAMDATA%\miniconda3"
    "%PROGRAMDATA%\anaconda3"
    "C:\ProgramData\miniforge3"
    "C:\ProgramData\miniconda3"
    "C:\ProgramData\anaconda3"
    "C:\miniforge3"
    "C:\miniconda3"
    "C:\anaconda3"
) DO (
    IF EXIST "%%~D\condabin\conda.bat" (
        SET "CONDA_BAT=%%~D\condabin\conda.bat"
        GOTO :found_conda
    )
)

echo WARNING: conda not found -- skipping environment update.
echo          Run setup_windows.bat to set up the environment.
GOTO :skip_conda

:found_conda
CALL "%CONDA_BAT%" activate base 2>nul
CALL "%CONDA_BAT%" env update -f "%PROJECT_DIR%\envs\storm_windows.yml" --prune
IF ERRORLEVEL 1 (
    echo WARNING: conda env update failed. Check the output above.
) ELSE (
    echo conda environment up to date.
)

:skip_conda

REM ── 3. Recreate Desktop shortcut (picks up any launcher changes) ───────────────

echo.
echo Updating Desktop shortcut...
CALL "%PROJECT_DIR%\scripts\create_app_windows.bat" >nul 2>&1

echo.
echo ════════════════════════════════════════════════
echo   Update complete! Launch STORM normally.
echo ════════════════════════════════════════════════
pause
