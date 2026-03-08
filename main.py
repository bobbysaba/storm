# main.py
# entry point â€” parses CLI args, configures logging, launches Qt application.

import sys
import os
import argparse
import logging
import faulthandler

# Chromium flags vary by platform:
#   macOS: --in-process-gpu avoids Mach port rendezvous failures in unsigned bundles
#   Windows: The Intel HD 620 driver (21.20.x) crashes after a failed
#            IDCompositionDevice4 QueryInterface in direct_composition_support.cc.
#            --in-process-gpu runs the GPU thread inside the main browser process
#            rather than a separate GPU subprocess.  This takes a different
#            initialization path that avoids the direct_composition_support.cc crash
#            on the old Intel driver.  (Same flag used on macOS for a similar reason.)
#            --ignore-gpu-blocklist allows the Intel D3D11 GPU to be used.
def _configure_qt_webengine_env() -> None:
    # Must run before importing modules that pull in QtWebEngine classes.
    if sys.platform == "win32":
        # Windows defaults to map-capable hardware mode.
        # Set STORM_RUNTIME_SAFE=1 to force software rendering on unstable machines.
        runtime_safe = os.environ.get("STORM_RUNTIME_SAFE", "0") == "1"
        if runtime_safe:
            os.environ["QMLSCENE_DEVICE"] = "softwarecontext"
            os.environ["QT_QUICK_BACKEND"] = "software"
            os.environ["QT_OPENGL"] = "software"
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
                "--no-sandbox "
                "--disable-gpu "
                "--disable-gpu-compositing"
            )
        else:
            os.environ.pop("QMLSCENE_DEVICE", None)
            os.environ.pop("QT_QUICK_BACKEND", None)
            os.environ.pop("QT_OPENGL", None)
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
                "--no-sandbox "
                "--in-process-gpu "
                "--ignore-gpu-blocklist"
            )
    else:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu"


_configure_qt_webengine_env()

from PyQt6.QtWidgets import QApplication, QDialog
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from ui.launch_dialog import LaunchDialog
from ui.main_window import MainWindow
from ui.radar_overlay import set_render_grid_size
from data.truck_replay import load_truck_observations
import config

def _configure_logging(level_name: str) -> None:
    # map level name string to logging constant
    level = getattr(logging, level_name.upper(), logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # silence noisy third-party loggers unless debug
    if level > logging.DEBUG:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("metpy").setLevel(logging.WARNING)


def main():
    try:
        _fh = open("storm_fault.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="STORM â€” Severe Thunderstorm Observation and Reconnaissance Monitor")
    parser.add_argument(
        "--debug", action="store_true",
        help="enable debug logging and in-app debug panel"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="monitor mode â€” display remote vehicles and radar without publishing any local data"
    )
    parser.add_argument(
        "--log-level", default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: WARNING; --debug overrides to DEBUG)"
    )
    parser.add_argument(
        "--truck-replay-file",
        default="",
        help="path to CSV/TXT truck data file to replay locally (no MQTT)"
    )
    parser.add_argument(
        "--truck-replay-interval-ms",
        type=int,
        default=1000,
        help="milliseconds between replayed truck samples (default: 1000)"
    )
    parser.add_argument(
        "--truck-replay-restamp",
        action="store_true",
        help=(
            "shift all replay timestamps so the last obs lands at now. "
            "Required when replaying old files so the history store and "
            "freshness colors behave as if the data is live."
        )
    )
    parser.add_argument(
        "--render-grid-size",
        type=int,
        default=0,
        metavar="N",
        help=(
            "radar render resolution (default: 512). "
            "Lower values render faster on slow hardware. "
            "Suggested values: 512 (sharp), 384, 256 (fast), 128 (very fast/blocky)"
        )
    )
    args = parser.parse_args()

    # --debug flag overrides --log-level
    log_level = "DEBUG" if args.debug else args.log_level
    _configure_logging(log_level)

    # apply render grid size override before the window is created
    if args.render_grid_size > 0:
        set_render_grid_size(args.render_grid_size)

    app = QApplication(sys.argv)
    app.setApplicationName("STORM")
    app.setOrganizationName("STORM")
    app.setFont(QFont("Segoe UI", 10))
    # Prevent the app from quitting when the launch dialog hides.
    # On Windows, hiding a top-level dialog triggers Qt's "last window closed"
    # quit event before the main window is shown.  We handle quitting explicitly
    # in MainWindow.closeEvent instead.
    app.setQuitOnLastWindowClosed(False)

    # Show launch dialog unless --monitor was passed directly on the CLI
    monitor = args.monitor
    if not monitor:
        dialog = LaunchDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        # Push dialog values into config module so MainWindow picks them up
        config.VEHICLE_ID   = (dialog.vehicle_id() or config.VEHICLE_ID).lower()
        config.OBS_FILE_DIR = dialog.data_dir()
        monitor             = dialog.monitor()

    window = MainWindow(debug=args.debug, monitor=monitor)

    # Forward JS console messages when WebEngine is available.
    js_log = logging.getLogger("storm.js")
    if hasattr(window.map_widget, "page"):
        def handle_js_message(level, message, line, source):
            js_log.debug("JS [%s:%s] %s", source, line, message)
        window.map_widget.page().javaScriptConsoleMessage = handle_js_message

    app.aboutToQuit.connect(lambda: print("DEBUG: aboutToQuit signal fired", flush=True))
    app.lastWindowClosed.connect(lambda: print("DEBUG: lastWindowClosed signal fired", flush=True))
    print("DEBUG: calling window.show()", flush=True)
    window.show()
    print("DEBUG: entering app.exec()", flush=True)

    if args.truck_replay_file:
        _start_truck_replay(
            window=window,
            file_path=args.truck_replay_file,
            interval_ms=max(50, args.truck_replay_interval_ms),
            restamp=args.truck_replay_restamp,
        )

    exit_code = app.exec()
    print(f"DEBUG: app.exec() returned {exit_code}", flush=True)
    sys.exit(exit_code)


def _start_truck_replay(window: MainWindow, file_path: str,
                        interval_ms: int, restamp: bool = False) -> None:
    from datetime import datetime, timezone
    from dataclasses import replace

    log = logging.getLogger("storm.replay")
    try:
        observations = load_truck_observations(file_path)
    except Exception as exc:
        log.error("truck replay failed to load %s: %s", file_path, exc)
        return

    if not observations:
        log.warning("truck replay file has no valid rows: %s", file_path)
        return

    if restamp:
        # Shift all timestamps so the last obs lands at now.
        # This preserves relative spacing while making the data appear live â€”
        # required for the history store (10-min rolling window) and freshness
        # colors to work correctly when replaying old files.
        now = datetime.now(timezone.utc)
        shift = now - observations[-1].timestamp
        observations = [replace(o, timestamp=o.timestamp + shift) for o in observations]
        log.info("restamped %d obs (shift=%.0fs)", len(observations), shift.total_seconds())

    log.info(
        "truck replay loaded %d rows from %s (interval=%dms, restamp=%s)",
        len(observations), file_path, interval_ms, restamp
    )

    idx = 0

    def tick():
        nonlocal idx
        if idx >= len(observations):
            window._truck_replay_timer.stop()
            log.info("truck replay complete")
            return
        window.update_vehicle_obs(observations[idx])
        idx += 1

    window._truck_replay_timer = QTimer(window)
    window._truck_replay_timer.setInterval(interval_ms)
    window._truck_replay_timer.timeout.connect(tick)

    # Give map/webview time to fully initialize before first replay sample.
    def start_replay():
        tick()  # push first sample immediately when replay starts
        window._truck_replay_timer.start()

    QTimer.singleShot(1400, start_replay)

if __name__ == "__main__":
    main()
