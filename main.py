# Bobby Saba - main script to run application

# import required packages and modules
import os
import re
import sys
import uuid
import config
import socket
import logging
import argparse
import faulthandler
import runtime_flags
from dataclasses import replace
from ui.main_window import MainWindow
from datetime import datetime, timezone
from ui.launch_dialog import LaunchDialog
from ui.radar_overlay import set_render_grid_size
from data.truck_replay import load_truck_observations

# try to import PyQt packages
try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
# if there is a failure, show an error message
except ModuleNotFoundError as exc:
    if exc.name == "PyQt6":
        print("PyQt6 is not installed, please activate the 'storm' environment, then run 'python main.py'.", file=sys.stderr)

        # exit with error
        sys.exit(1)
    raise

# port used exclusively as a single-instance lock (not a real server)
_INSTANCE_LOCK_PORT = 19876

# reference kept alive so the OS doesn't release the bound port
_instance_lock_socket = None


def _acquire_instance_lock() -> bool:
    """Bind a localhost socket as a single-instance guard.

    Returns True if this is the first instance; False if one is already running.
    The socket is intentionally kept alive (stored in _instance_lock_socket) for
    the entire process lifetime — closing it would release the lock.
    """
    global _instance_lock_socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Disable SO_REUSEADDR so a recently-closed instance can't be re-bound
        # before Python's GC collects the socket.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind(("127.0.0.1", _INSTANCE_LOCK_PORT))
        sock.listen(1)
        _instance_lock_socket = sock  # keep alive
        return True
    except OSError:
        return False


# function to parse command line arguments (if run from the command line)
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="STORM - Severe Thunderstorm Observation and Reconnaissance Monitor",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Debug-run profiles:\n"
            "  0 normal\n"
            "  1 safe map mode only\n"
            "  2 runtime-safe + safe map mode\n"
            "  3 disable radar path\n"
            "  4 disable MQTT path\n"
            "  5 minimal/offline diagnostic shell\n"
            "  6 MQTT diagnostics (no TLS)\n\n"
            "Examples:\n"
            "  python main.py --debug-run 2\n"
            "  python main.py --disable-mqtt --disable-radar\n"
            "  python main.py --monitor --debug"
        ),
    )

    # run in debug mode 
    parser.add_argument("--debug", action="store_true", help="enable debug logging and in-app debug panel")

    # run in monitor mode
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="monitor mode: skip local obs inputs; MQTT sync for map edits remains enabled",
    )

    # determine the log level to run in 
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: WARNING; --debug overrides to DEBUG)",
    )

    # mode to test a local file as "real-time"
    parser.add_argument(
        "--truck-replay-file",
        default="",
        help="path to CSV/TXT truck data file to replay locally (no MQTT)",
    )

    # interval to read in local test data (ms)
    parser.add_argument(
        "--truck-replay-interval-ms",
        type=int,
        default=1000,
        help="milliseconds between replayed truck samples (default: 1000)",
    )

    # shifts local test data to real-time
    parser.add_argument(
        "--truck-replay-restamp",
        action="store_true",
        help=(
            "shift replay timestamps so the last obs lands at now; useful for old files so "
            "history/freshness behavior matches live data"
        ),
    )

    # render resolution for radar data
    parser.add_argument(
        "--render-grid-size",
        type=int,
        default=0,
        metavar="N",
        help=(
            "radar render resolution override (default runtime value: 512). "
            "Suggested values: 512, 384, 256, 128"
        ),
    )

    # debug mode (NOTE: there are different flags here that can be seen in the --help section)
    parser.add_argument(
        "--debug-run",
        type=int,
        choices=range(0, 7),
        default=0,
        metavar="N",
        help="quick diagnostic profile number (see 'Debug-run profiles' below)",
    )

    # actions for the debug-run flag
    parser.add_argument("--enable-startup-toggles", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-radar", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-mqtt", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-vehicle-fetcher", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-annotations", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-deploy-locs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-data-inputs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--mqtt-no-tls", action=argparse.BooleanOptionalAction, default=None)

    # return the parsed arguments
    return parser

# function to configure qt webengine 
def _configure_qt_webengine_env() -> None:
    # if the platform is windows, force software rendering
    if sys.platform == "win32":
        runtime_safe = runtime_flags.FLAGS.runtime_safe
        if runtime_safe:
            os.environ["QMLSCENE_DEVICE"] = "softwarecontext"
            os.environ["QT_QUICK_BACKEND"] = "software"
            os.environ["QT_OPENGL"] = "software"
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu --disable-gpu-compositing"
        else:
            os.environ.pop("QMLSCENE_DEVICE", None)
            os.environ.pop("QT_QUICK_BACKEND", None)
            os.environ.pop("QT_OPENGL", None)
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu --ignore-gpu-blocklist"
    # otherwise, use mac/linux defaults
    else:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu"

# function to generate a default vehicle ID (if in monitor mode)
# NOTE: this is needed because if multiple people are in monitor mode, then the MQTT connection will fail
def _default_vehicle_id() -> str:
    # pull the host name
    host = (socket.gethostname() or "").strip().lower()

    # additional formatting for the host name
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")

    # derive a stable 4-char hex suffix from the MAC address so two machines
    # with the same hostname (or no hostname) still get distinct MQTT client IDs
    mac_suffix = format(uuid.getnode() & 0xFFFF, "04x")

    # if the host name is empty, use "device"
    if not host:
        host = "device"

    # return the ID used for the MQTT connection
    return f"storm-{host}-{mac_suffix}"

# function to normalize a user-provided vehicle ID
def _normalize_vehicle_id(raw: str) -> str:
    # strip leading/trailing whitespace
    vid = (raw or "").strip().lower()

    # remove non-alphanumeric characters
    vid = re.sub(r"[^a-z0-9-]+", "-", vid).strip("-")

    # if the ID is left empty, use the default
    if not vid or vid == "storm":
        # run the default function
        return _default_vehicle_id()
    
    # return the normalized ID
    return vid

# function to configure logging
def _configure_logging(level_name: str) -> None:
    # set the logging level
    level = getattr(logging, level_name.upper(), logging.WARNING)

    # configure logging
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # write WARNING+ logs to a persistent file for post-session review
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storm_errors.log")
        _fh = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
        _fh.setLevel(logging.WARNING)
        _fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(_fh)
    except Exception:
        pass

    # set logging levels (if the level is greater than DEBUG)
    if level > logging.DEBUG:
        # set logging levels
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("metpy").setLevel(logging.WARNING)

# function to check for missing required files and show user-visible warnings
def _warn_missing_files() -> None:
    """Show QMessageBox warnings for any missing required files.

    Called after QApplication is created but before the main window is built, so
    dialogs render correctly.  Warnings are non-fatal — the app continues in a
    degraded state so the user can at least see what is wrong.
    """
    # ── Map tiles ──────────────────────────────────────────────────────────────
    tiles_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "tiles", "storm.mbtiles")
    )
    if not os.path.exists(tiles_path):
        QMessageBox.warning(
            None,
            "Missing Map Tiles",
            f"Map tiles file not found:\n\n  {tiles_path}\n\n"
            "The map will not render correctly.\n"
            "Ensure 'tiles/storm.mbtiles' is present in the application directory.",
        )

    # ── AWS / MQTT certificates ────────────────────────────────────────────────
    # Skip entirely if MQTT was disabled via a flag or debug-run profile.
    if runtime_flags.FLAGS.disable_mqtt:
        return

    aws_dir = os.path.dirname(config.MQTT_CA_CERT)
    cert_files = [config.MQTT_CA_CERT, config.MQTT_CERT_FILE, config.MQTT_KEY_FILE]

    if not os.path.isdir(aws_dir):
        QMessageBox.warning(
            None,
            "Missing AWS Credentials Folder",
            f"The AWS credentials directory was not found:\n\n  {aws_dir}\n\n"
            "MQTT features (annotation sync, vehicle tracking) will be unavailable.\n"
            "Create the 'aws/' folder and add your certificates to enable them.",
        )
    else:
        missing = [f for f in cert_files if not os.path.isfile(f)]
        if missing:
            names = "\n".join(f"  \u2022 {os.path.basename(f)}" for f in missing)
            QMessageBox.warning(
                None,
                "Missing AWS Certificate Files",
                f"The following MQTT certificate files were not found:\n\n{names}\n\n"
                "MQTT features (annotation sync, vehicle tracking) will be unavailable.",
            )




# main function
def main() -> None:
    # try to configure fault handler
    try:
        # open the log file
        _fh = open("storm_fault.log", "a", buffering=1, encoding="utf-8")

        # enable the fault handler
        faulthandler.enable(file=_fh, all_threads=True)
    # if that fails
    except Exception:
        # do nothing
        pass

    # build the argument parser
    parser = _build_parser()

    # parse the arguments
    args = parser.parse_args()

    # configure runtime flags
    runtime_flags.reset_flags()

    # apply debug run profile
    runtime_flags.apply_debug_run_profile(args.debug_run)

    # apply overrides
    runtime_flags.apply_overrides(
        enable_startup_toggles = args.enable_startup_toggles,
        disable_radar = args.disable_radar,
        disable_mqtt = args.disable_mqtt,
        disable_vehicle_fetcher = args.disable_vehicle_fetcher,
        disable_annotations = args.disable_annotations,
        disable_deploy_locs = args.disable_deploy_locs,
        disable_data_inputs = args.disable_data_inputs,
        mqtt_no_tls = args.mqtt_no_tls,
    )

    # finalize flags
    runtime_flags.finalize_flags()

    # if the debug run profile is enabled
    if args.debug_run > 0 and not args.debug:
        # set the debug flag
        args.debug = True

    # configure logging
    log_level = "DEBUG" if args.debug else args.log_level
    _configure_logging(log_level)

    # set the vehicle ID
    config.VEHICLE_ID = _normalize_vehicle_id(config.VEHICLE_ID)

    # configure the Qt webengine environment
    _configure_qt_webengine_env()

    # set the render grid size for radar overlays
    if args.render_grid_size > 0:
        # set the render grid size
        set_render_grid_size(args.render_grid_size)

    # create the application
    app = QApplication(sys.argv)

    # set application properties
    app.setApplicationName("STORM")
    app.setOrganizationName("STORM")
    app.setFont(QFont("Segoe UI", 10))

    # set application quit behavior
    app.setQuitOnLastWindowClosed(False)

    # ── Single-instance guard ──────────────────────────────────────────────────
    # Must happen after QApplication exists so the warning dialog can be shown.
    if not _acquire_instance_lock():
        QMessageBox.warning(
            None,
            "STORM Already Running",
            "STORM is already running on this machine.\n\n"
            "Please close all existing STORM instances before opening a new one.",
        )
        sys.exit(0)


    # pull whether or not we're in monitor mode
    monitor = args.monitor

    # if not in monitor mode
    if not monitor:
        # show the launch dialog
        dialog = LaunchDialog()

        # if the diaglog is not accepted
        if dialog.exec() != QDialog.DialogCode.Accepted:
            # exit
            sys.exit(0)

        # get the vehicle ID
        config.VEHICLE_ID = _normalize_vehicle_id(dialog.vehicle_id())

        # get the directory for real-time observation files (if any)
        config.OBS_FILE_DIR = dialog.data_dir()

        # get whether or not we're in monitor mode (from the user-selected window)
        monitor = dialog.monitor()

    # ── File presence checks ───────────────────────────────────────────────────
    # Warn about missing tiles / certificates before the window is built so the
    # user understands why certain features may be broken.
    _warn_missing_files()

    # create the main window
    window = MainWindow(debug = args.debug, monitor = monitor)

    # define the JS console message handler
    js_log = logging.getLogger("storm.js")

    # if the map widget has a page
    if hasattr(window.map_widget, "page"):
        # define the JS console message handler
        def handle_js_message(level, message, line, source):
            js_log.debug("JS [%s:%s] %s", source, line, message)

        # set the JS console message handler
        window.map_widget.page().javaScriptConsoleMessage = handle_js_message

    # define before-quit handler
    app.aboutToQuit.connect(lambda: print("DEBUG: aboutToQuit signal fired", flush=True))

    # define last window closed handler
    app.lastWindowClosed.connect(lambda: print("DEBUG: lastWindowClosed signal fired", flush=True))

    # show the window
    print("DEBUG: calling window.show()", flush=True)
    window.show()

    print("DEBUG: entering app.exec()", flush=True)

    # if a truck replay file is specified
    if args.truck_replay_file:
        # start the truck replay
        _start_truck_replay(window=window, file_path=args.truck_replay_file, interval_ms = max(50, args.truck_replay_interval_ms), 
                            restamp=args.truck_replay_restamp)

    # run the application
    exit_code = app.exec()

    # print the exit code
    print(f"DEBUG: app.exec() returned {exit_code}", flush=True)
    sys.exit(exit_code)

# function to start a truck replay
def _start_truck_replay(window, file_path: str, interval_ms: int, restamp: bool = False) -> None:
    # logger
    log = logging.getLogger("storm.replay")

    # try to load in observations
    try:
        observations = load_truck_observations(file_path)
    # error out if there are issues with the provided file path
    except Exception as exc:
        log.error("truck replay failed to load %s: %s", file_path, exc)
        return

    # if there are no observations
    if not observations:
        # log a warning
        log.warning("truck replay file has no valid rows: %s", file_path)
        return

    # if restamp of times was requested
    if restamp:
        # pull the current time
        now = datetime.now(timezone.utc)

        # restamp the observations
        shift = now - observations[-1].timestamp
        observations = [replace(o, timestamp=o.timestamp + shift) for o in observations]

        # log action
        log.info("restamped %d obs (shift=%.0fs)", len(observations), shift.total_seconds())

    # log the replay data loaded
    log.info("truck replay loaded %d rows from %s (interval=%dms, restamp=%s)", len(observations), file_path, interval_ms, restamp)

    # define the replay timer
    idx = 0

    # define the tick handler
    def tick():
        # pull the current index
        nonlocal idx

        # if we've reached the end
        if idx >= len(observations):
            # stop the timer
            window._truck_replay_timer.stop()

            # log action
            log.info("truck replay complete")
            return

        # if there is still data to parse through, update the vehicle
        window.update_vehicle_obs(observations[idx])

        # increment the index
        idx += 1

    # create the timer
    window._truck_replay_timer = QTimer(window)
    window._truck_replay_timer.setInterval(interval_ms)
    window._truck_replay_timer.timeout.connect(tick)

    # start the timer
    def start_replay():
        tick()
        window._truck_replay_timer.start()

    # start the replay
    QTimer.singleShot(1400, start_replay)

# if this is the main script
if __name__ == "__main__":
    main()
