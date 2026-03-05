@echo off
REM scripts/launch_storm.bat — activates the storm conda environment and launches STORM.
REM This file is called by the desktop shortcut created by create_app_windows.bat.

REM Resolve project root (one level above scripts/)
FOR %%I IN ("%~dp0..") DO SET "PROJECT_DIR=%%~fI"

REM ── Find conda ────────────────────────────────────────────────────────────────
SET "CONDA_BAT="
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

powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Could not find a conda installation.`n`nInstall miniforge or miniconda, then create the storm environment:`n  conda env create -f envs\storm_windows.yml', 'STORM — Launch Error', 'OK', 'Error')"
EXIT /B 1

:found_conda
CALL "%CONDA_BAT%" activate storm 2>nul
IF ERRORLEVEL 1 (
    CALL "%CONDA_BAT%" activate storm311 2>nul
)

IF "%CONDA_DEFAULT_ENV%" == "" (
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('The ""storm"" conda environment was not found.`n`nCreate it with:`n  conda env create -f envs\storm_windows.yml', 'STORM — Launch Error', 'OK', 'Error')"
    EXIT /B 1
)

cd /d "%PROJECT_DIR%"

REM Use certifi's CA bundle — Windows conda envs may not inherit system SSL certs
FOR /F "delims=" %%F IN ('dir /b /s "%CONDA_PREFIX%\Lib\site-packages\certifi\cacert.pem" 2^>nul') DO (
    SET "SSL_CERT_FILE=%%F"
    SET "REQUESTS_CA_BUNDLE=%%F"
    GOTO :ssl_done
)
:ssl_done

REM pythonw suppresses the console window for GUI apps
START "" pythonw main.py
