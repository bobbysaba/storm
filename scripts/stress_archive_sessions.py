"""Open, use and close archive-mode windows one after another in one process,
as Change Day does, to catch native crashes in startup and teardown (a
background worker or timer calling into a window that's already deleted,
a second map that can't load storm://).

Needs a display (it opens real windows, parked off-screen) and the network.
A crash prints a Python thread dump (faulthandler) and exits non-zero; a
clean run ends with "STRESS DONE". Saved settings and storm tracks go to a
scratch folder, not yours.

    python scripts/stress_archive_sessions.py --seed 1 --cycles 20
"""
import argparse
import faulthandler
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

DATES = [datetime(2024, 4, 27, 20, 0, tzinfo=timezone.utc),
         datetime(2025, 6, 6, 20, 0, tzinfo=timezone.utc),
         datetime(2013, 5, 31, 23, 0, tzinfo=timezone.utc)]
LIFETIMES = [0.3, 1.5, 4, 8, 15]            # seconds; short ones close mid-startup
ACTIONS = ["none", "station", "product", "seek", "velocity"]


def _scratch_settings(scratch: Path):
    """Every STORM module's QSettings reads and writes a scratch INI file."""
    from PyQt6 import QtCore

    real = QtCore.QSettings

    class ScratchSettings(real):
        def __init__(self, *_args):
            super().__init__(str(scratch / "settings.ini"), real.Format.IniFormat)
    for name, module in list(sys.modules.items()):
        if name.startswith(("ui.", "core.", "data.")) and getattr(module, "QSettings", None) is real:
            module.QSettings = ScratchSettings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cycles", type=int, default=12)
    args = parser.parse_args()
    faulthandler.enable(all_threads=True)

    import logging
    logging.basicConfig(level=logging.ERROR)
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    from PyQt6 import QtWebEngineWidgets  # noqa: F401 -- must precede QApplication
    import main as storm_main
    storm_main._register_storm_scheme()
    import net_compat
    net_compat.prefer_ipv4()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    import runtime_flags
    runtime_flags.FLAGS.admin_mode = True

    scratch = Path(tempfile.mkdtemp(prefix="storm_stress_"))
    from core import workspace
    workspace.TRACKS_ROOT = scratch / "storm_tracks"
    from ui.app import main_window as mw
    from ui.app.main_window_case import SessionRestart
    _scratch_settings(scratch)

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.005)

    rng = random.Random(args.seed)
    started = time.time()
    for cycle in range(args.cycles):
        when, life, action = rng.choice(DATES), rng.choice(LIFETIMES), rng.choice(ACTIONS)
        window = mw.MainWindow(archive_time=when)
        window.resize(1200, 800)
        window.move(-3000, 0)
        window.show()
        pump(life / 2)
        if action == "station" and getattr(window, "_archive_radar", None):
            window._on_radar_station_clicked(rng.choice(["KTLX", "KFDR", "KLBB", "KAMA"]))
        elif action in ("product", "velocity") and hasattr(window, "radar_controls"):
            window.radar_controls.select_product("velocity")
            if action == "velocity":
                window.radar_controls.set_dealias(True)
        elif action == "seek" and getattr(window, "_time_ctrl", None):
            window._time_ctrl.set_time(when.replace(hour=23))
        pump(life / 2)
        # as Change Day closes it: main.py's session loop then deletes it
        window.restart_with = SessionRestart(when)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pump(0.2)
        print(f"cycle {cycle}: {when:%Y-%m-%d} lived {life}s, {action}, closed ok "
              f"(t={time.time() - started:.0f}s)", flush=True)
    print("STRESS DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
