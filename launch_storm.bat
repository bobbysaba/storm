@echo off
REM launch_storm.bat — activates the storm conda environment and launches STORM.
REM This file is called by the desktop shortcut created by create_app_windows.bat.

SET "PROJECT_DIR=%~dp0"
SET "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

REM ── Find conda ────────────────────────────────────────────────────────────────
SET "CONDA_BAT="
FOR %%D IN (
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%LOCALAPPDATA%\miniforge3"
    "%LOCALAPPDATA%\miniconda3"
    "%PROGRAMDATA%\miniforge3"
    "%PROGRAMDATA%\miniconda3"
    "C:\ProgramData\miniforge3"
    "C:\ProgramData\miniconda3"
    "C:\miniforge3"
    "C:\miniconda3"
) DO (
    IF EXIST "%%~D\condabin\conda.bat" (
        SET "CONDA_BAT=%%~D\condabin\conda.bat"
        GOTO :found_conda
    )
)

powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Could not find a conda installation.`n`nInstall miniforge or miniconda, then create the storm environment:`n  conda env create -f storm_windows.yml', 'STORM — Launch Error', 'OK', 'Error')"
EXIT /B 1

:found_conda
CALL "%CONDA_BAT%" activate storm 2>nul
IF ERRORLEVEL 1 (
    CALL "%CONDA_BAT%" activate storm311 2>nul
)

IF "%CONDA_DEFAULT_ENV%" == "" (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('The ""storm"" conda environment was not found.`n`nCreate it with:`n  conda env create -f storm_windows.yml', 'STORM — Launch Error', 'OK', 'Error')"
    EXIT /B 1
)

cd /d "%PROJECT_DIR%"

REM pythonw suppresses the console window for GUI apps
START "" pythonw main.py
