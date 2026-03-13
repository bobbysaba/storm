@echo off
REM setup_windows.bat — one-time setup for STORM on Windows.
REM Run from the project root after cloning (double-click or run from terminal):
REM
REM What it does:
REM   1. Installs Miniforge (if conda is not already available)
REM   2. Creates the 'storm' conda environment from storm_windows.yml
REM   3. Builds the storm icon and creates STORM shortcuts (Desktop + Start Menu, attempt taskbar pin)

SETLOCAL ENABLEDELAYEDEXPANSION

SET "PROJECT_DIR=%~dp0"
SET "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
SET "CONDA_BAT="

REM ── 1. Find existing conda installation ───────────────────────────────────────

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
        SET "CONDA_BASE=%%~D"
        GOTO :found_conda
    )
)

REM ── conda not found — download and install Miniforge silently ─────────────────

echo Conda not found. Downloading Miniforge installer...
SET "INSTALLER=%TEMP%\miniforge_installer.exe"
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe' -OutFile '%INSTALLER%'"

IF NOT EXIST "%INSTALLER%" (
    echo ERROR: Failed to download Miniforge installer.
    echo Please install Miniforge manually from: https://github.com/conda-forge/miniforge
    pause
    EXIT /B 1
)

echo Installing Miniforge silently to %USERPROFILE%\miniforge3 ...
"%INSTALLER%" /S /InstallationType=JustMe /RegisterPython=0 /D=%USERPROFILE%\miniforge3
DEL /F /Q "%INSTALLER%"

SET "CONDA_BAT=%USERPROFILE%\miniforge3\condabin\conda.bat"
SET "CONDA_BASE=%USERPROFILE%\miniforge3"
echo Miniforge installed.

:found_conda
echo Using conda at: %CONDA_BASE%
CALL "%CONDA_BAT%" activate base 2>nul

REM ── 2. Create the storm environment ──────────────────────────────────────────

CALL "%CONDA_BAT%" env list | findstr /B "storm " >nul 2>&1
IF %ERRORLEVEL% EQU 0 (
    echo storm environment already exists -- skipping creation.
) ELSE (
    echo Creating storm conda environment (this may take a few minutes^)...
    CALL "%CONDA_BAT%" env create -f "%PROJECT_DIR%\envs\storm_windows.yml"
    IF ERRORLEVEL 1 (
        echo ERROR: Failed to create storm environment.
        pause
        EXIT /B 1
    )
    echo storm environment created.
)

REM ── 3. Create shortcuts (Desktop + Start Menu, attempt taskbar pin) ──────────

echo Creating shortcuts (Desktop + Start Menu, attempt taskbar pin)...
CALL "%PROJECT_DIR%\scripts\create_app_windows.bat"

echo.
echo ════════════════════════════════════════════════
echo   Setup complete!
echo   Launch STORM from the Desktop shortcut or run:
echo     conda activate storm ^&^& python main.py
echo ════════════════════════════════════════════════
pause
