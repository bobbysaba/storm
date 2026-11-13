#!/usr/bin/env python3
"""
setup.py — cross-platform one-time setup for STORM.

Prerequisites: Python 3.8+
  If you don't have conda, install Miniforge first:
    https://github.com/conda-forge/miniforge#download

Usage:
    python setup.py
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_YML = os.path.join(PROJECT_DIR, "envs", "storm.yml")
SYSTEM = platform.system()  # "Darwin", "Linux", "Windows"



def _conda_search_dirs():
    """Return platform-appropriate directories to search for conda."""
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, "miniforge3"),
        os.path.join(home, "miniconda3"),
        os.path.join(home, "anaconda3"),
    ]
    if SYSTEM == "Darwin":
        dirs += [
            os.path.join(home, "opt", "miniforge3"),
            os.path.join(home, "opt", "miniconda3"),
            os.path.join(home, "opt", "anaconda3"),
            "/opt/homebrew/Caskroom/miniforge/base",
            "/opt/miniconda3",
            "/opt/anaconda3",
        ]
    elif SYSTEM == "Linux":
        dirs += [
            os.path.join(home, "opt", "miniforge3"),
            "/opt/miniforge3",
            "/opt/miniconda3",
            "/opt/anaconda3",
        ]
    elif SYSTEM == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        pdata = os.environ.get("PROGRAMDATA", "")
        dirs += [
            os.path.join(local, "miniforge3"),
            os.path.join(local, "miniconda3"),
            os.path.join(local, "anaconda3"),
            os.path.join(pdata, "miniforge3"),
            os.path.join(pdata, "miniconda3"),
            os.path.join(pdata, "anaconda3"),
            r"C:\miniforge3",
            r"C:\miniconda3",
            r"C:\anaconda3",
        ]
    return dirs


def find_conda():
    """Return the path to the conda executable, or None."""
    conda = shutil.which("conda")
    if conda:
        return conda

    for d in _conda_search_dirs():
        if SYSTEM == "Windows":
            candidate = os.path.join(d, "condabin", "conda.bat")
        else:
            candidate = os.path.join(d, "condabin", "conda")
        if os.path.isfile(candidate):
            return candidate
    return None


def install_miniforge():
    """Download and silently install Miniforge, return conda path."""
    home = os.path.expanduser("~")
    install_dir = os.path.join(home, "miniforge3")
    base_url = "https://github.com/conda-forge/miniforge/releases/latest/download"

    if SYSTEM == "Darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        filename = f"Miniforge3-MacOSX-{arch}.sh"
    elif SYSTEM == "Linux":
        filename = f"Miniforge3-Linux-{platform.machine()}.sh"
    elif SYSTEM == "Windows":
        filename = "Miniforge3-Windows-x86_64.exe"
    else:
        sys.exit(f"Unsupported platform: {SYSTEM}")

    url = f"{base_url}/{filename}"
    installer = os.path.join(tempfile.gettempdir(), filename)

    print(f"Downloading Miniforge from {url} ...")
    urllib.request.urlretrieve(url, installer)

    print(f"Installing Miniforge to {install_dir} ...")
    if SYSTEM == "Windows":
        subprocess.check_call([
            installer, "/S", "/InstallationType=JustMe",
            "/RegisterPython=0", f"/D={install_dir}",
        ])
        conda = os.path.join(install_dir, "condabin", "conda.bat")
    else:
        subprocess.check_call(["bash", installer, "-b", "-p", install_dir])
        conda = os.path.join(install_dir, "condabin", "conda")

    os.remove(installer)
    print("Miniforge installed.")
    return conda



def env_exists(conda):
    """Check if the 'storm' conda environment already exists."""
    result = subprocess.run(
        [conda, "env", "list"],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("storm "):
            return True
    return False


def create_env(conda):
    """Create the storm conda environment from the unified yml."""
    if env_exists(conda):
        # an environment made from an older envs/storm.yml lacks packages
        # added since: add them. No --prune, so nothing someone installed by
        # hand is removed.
        print("storm environment already exists — updating it from envs/storm.yml...")
        subprocess.check_call([conda, "env", "update", "-n", "storm", "-f", ENV_YML])
        print("storm environment updated.")
        return
    print("Creating storm conda environment (this may take a few minutes)...")
    subprocess.check_call([conda, "env", "create", "-f", ENV_YML])
    print("storm environment created.")



def is_wsl():
    """True when running inside Windows Subsystem for Linux."""
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _apt_has(pkg):
    """True when apt knows an installable candidate for pkg."""
    result = subprocess.run(
        ["apt-cache", "policy", pkg], capture_output=True, text=True,
    )
    return "Candidate:" in result.stdout and "Candidate: (none)" not in result.stdout


def install_linux_deps():
    """Install Qt6 WebEngine system dependencies on Linux."""
    print("Installing Qt6 WebEngine system dependencies...")
    if shutil.which("apt-get"):
        # fresh installs (WSL especially) ship with empty package lists
        subprocess.check_call(["sudo", "apt-get", "update"])
        # Ubuntu 24.04+ / Debian 13 renamed libasound2 to libasound2t64
        alsa = "libasound2t64" if _apt_has("libasound2t64") else "libasound2"
        subprocess.check_call([
            "sudo", "apt-get", "install", "-y",
            "libnss3", "libxss1", alsa, "libatk-bridge2.0-0",
            "libgtk-3-0", "libx11-xcb1", "libxcomposite1", "libxdamage1",
            "libxrandr2", "libxkbcommon0", "libxkbcommon-x11-0",
            "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0",
            "libxcb-keysyms1", "libxcb-randr0", "libxcb-render-util0",
            "libxcb-shape0", "libxcb-xinerama0", "libxcb-xkb1",
            "libgl1", "libegl1", "desktop-file-utils",
        ])
    elif shutil.which("dnf"):
        subprocess.check_call([
            "sudo", "dnf", "install", "-y",
            "nss", "libXScrnSaver", "alsa-lib", "at-spi2-atk", "gtk3",
            "libX11", "libXcomposite", "libXdamage", "libXrandr",
            "libxkbcommon", "libxkbcommon-x11",
            "xcb-util", "xcb-util-cursor", "xcb-util-image",
            "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-wm", "libxcb",
            "desktop-file-utils",
        ])
    elif shutil.which("yum"):
        subprocess.check_call([
            "sudo", "yum", "install", "-y",
            "nss", "libXScrnSaver", "alsa-lib", "at-spi2-atk", "gtk3",
            "libX11", "libXcomposite", "libXdamage", "libXrandr",
            "libxkbcommon", "libxkbcommon-x11",
            "xcb-util", "xcb-util-cursor", "xcb-util-image",
            "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-wm", "libxcb",
            "desktop-file-utils",
        ])
    else:
        print("WARNING: Could not detect package manager (apt/dnf/yum).")
        print("You may need to manually install Qt6 WebEngine system libraries.")



def create_shortcuts_mac():
    """Build STORM.app and place a Desktop symlink."""
    create_app = os.path.join(PROJECT_DIR, "scripts", "create_app.sh")
    print("Building STORM.app...")
    subprocess.check_call(["bash", create_app])
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        link = os.path.join(desktop, "STORM.app")
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(os.path.join(PROJECT_DIR, "STORM.app"), link)
        print("STORM shortcut placed on Desktop.")


def create_shortcuts_linux(conda):
    """Create a launcher script and .desktop entry."""
    # launcher script
    launcher = os.path.join(PROJECT_DIR, "storm_launcher.sh")
    conda_base = subprocess.check_output(
        [conda, "info", "--base"], text=True,
    ).strip()
    with open(launcher, "w") as f:
        f.write(f'#!/bin/bash\n'
                f'source "{conda_base}/etc/profile.d/conda.sh"\n'
                f'conda activate storm\n'
                f'cd "{PROJECT_DIR}"\n'
                f'python main.py "$@"\n')
    os.chmod(launcher, 0o755)
    print(f"Launcher written to {launcher}")

    # .desktop entry
    desktop_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    os.makedirs(desktop_dir, exist_ok=True)
    desktop_file = os.path.join(desktop_dir, "storm.desktop")
    icon_path = os.path.join(PROJECT_DIR, "storm.png")
    icon = icon_path if os.path.isfile(icon_path) else "storm"
    with open(desktop_file, "w") as f:
        f.write(f"[Desktop Entry]\n"
                f"Name=STORM\n"
                f"Comment=Severe-weather Tactical Operations and Response Manager\n"
                f"Exec={launcher}\n"
                f"Icon={icon}\n"
                f"Terminal=false\n"
                f"Type=Application\n"
                f"Categories=Science;\n")
    os.chmod(desktop_file, 0o755)

    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", desktop_dir], check=False)

    print(f".desktop entry written to {desktop_file}")

    # desktop shortcut
    user_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(user_desktop):
        dest = os.path.join(user_desktop, "storm.desktop")
        shutil.copy2(desktop_file, dest)
        os.chmod(dest, 0o755)
        if shutil.which("gio"):
            subprocess.run(
                ["gio", "set", dest, "metadata::trusted", "true"],
                check=False, capture_output=True,
            )
        print("STORM shortcut placed on Desktop.")


def create_shortcuts_wsl():
    """Place a Windows Desktop shortcut that launches STORM inside WSL."""
    powershell = (shutil.which("powershell.exe")
                  or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not os.path.isfile(powershell) and not shutil.which("powershell.exe"):
        print("Note: Windows interop unavailable — skipping Windows Desktop shortcut.")
        return

    def ps(script):
        # cwd on /mnt/c avoids PowerShell's "UNC paths are not supported" warning
        return subprocess.check_output(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            text=True, cwd="/mnt/c",
        ).strip()

    def q(s):
        return "'" + s.replace("'", "''") + "'"

    try:
        desktop, localappdata = ps(
            "[Environment]::GetFolderPath('Desktop'); $env:LOCALAPPDATA"
        ).splitlines()[:2]
    except (subprocess.CalledProcessError, ValueError, OSError) as e:
        print(f"Note: could not locate the Windows Desktop ({e}) — skipping shortcut.")
        return

    # Windows can't reliably read an icon from inside the WSL filesystem, so
    # copy it to %LOCALAPPDATA%\STORM
    icon_win = ""
    icon_src = os.path.join(PROJECT_DIR, "storm.ico")
    if os.path.isfile(icon_src):
        icon_dir = subprocess.check_output(
            ["wslpath", "-u", localappdata + "\\STORM"], text=True,
        ).strip()
        os.makedirs(icon_dir, exist_ok=True)
        shutil.copy2(icon_src, os.path.join(icon_dir, "storm.ico"))
        icon_win = localappdata + "\\STORM\\storm.ico"

    # wslg.exe launches without a console window; fall back to a minimized wsl.exe
    if os.path.isfile("/mnt/c/Program Files/WSL/wslg.exe"):
        target, window_style = r"C:\Program Files\WSL\wslg.exe", 1
    else:
        target, window_style = r"C:\Windows\System32\wsl.exe", 7
    launcher = os.path.join(PROJECT_DIR, "storm_launcher.sh")
    distro = os.environ.get("WSL_DISTRO_NAME")
    args = (f'-d "{distro}" ' if distro else "") + f'-- "{launcher}"'

    shortcut = desktop + "\\STORM.lnk"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + q(shortcut) + "); "
        f"$s.TargetPath = {q(target)}; "
        f"$s.Arguments = {q(args)}; "
        f"$s.WindowStyle = {window_style}; "
        + (f"$s.IconLocation = {q(icon_win + ',0')}; " if icon_win else "")
        + "$s.Save()"
    )
    try:
        ps(script)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"Note: could not create Windows Desktop shortcut ({e}).")
        return
    print(f"STORM shortcut placed on Windows Desktop ({shortcut}).")


def create_shortcuts_windows():
    """Create Desktop and Start Menu shortcuts via PowerShell."""
    create_app = os.path.join(PROJECT_DIR, "scripts", "create_app_windows.bat")
    if os.path.isfile(create_app):
        print("Creating STORM shortcuts...")
        subprocess.check_call([create_app], shell=True)



def main():
    print(f"STORM setup — {SYSTEM} ({platform.machine()})")
    print()

    # 0. linux system deps
    if SYSTEM == "Linux":
        install_linux_deps()
        if is_wsl() and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            print("WARNING: WSL detected but no display is available.")
            print("STORM's window needs WSLg (Windows 11, or Windows 10 with a")
            print("current WSL). From Windows, run:  wsl --update  then  wsl --shutdown")
            print()

    # 1. find or install conda
    conda = find_conda()
    if not conda:
        print("Conda not found.")
        reply = input("Install Miniforge automatically? [Y/n] ").strip().lower()
        if reply in ("", "y", "yes"):
            conda = install_miniforge()
        else:
            print("Please install Miniforge and re-run setup:")
            print("  https://github.com/conda-forge/miniforge#download")
            sys.exit(1)

    print(f"Using conda: {conda}")
    print()

    # 2. create environment
    create_env(conda)
    print()

    # 3. shortcuts
    if SYSTEM == "Darwin":
        create_shortcuts_mac()
    elif SYSTEM == "Linux":
        create_shortcuts_linux(conda)
        if is_wsl():
            create_shortcuts_wsl()
    elif SYSTEM == "Windows":
        create_shortcuts_windows()

    print()
    print("━" * 48)
    print("  Setup complete!")
    print("  Launch STORM from your shortcut or run:")
    print("    conda activate storm && python main.py")
    print("━" * 48)


if __name__ == "__main__":
    main()
