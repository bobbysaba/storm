
import os
import re
import sys
import uuid
import socket
import config
import logging
import net_compat
import argparse
import faulthandler
import runtime_flags
from dataclasses import replace
from datetime import datetime, timezone
from ui.launch.dialog import LaunchDialog
from ui import text_scale
from data.ingest.truck_replay import load_truck_observations

# try to import PyQt packages
try:
    from PyQt6.QtCore import QCoreApplication, QEvent, QTimer, Qt
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
# if there is a failure, show an error message
except ModuleNotFoundError as exc:
    if exc.name == "PyQt6":
        print("PyQt6 is not installed, please activate the 'storm' environment, then run 'python main.py'.", file=sys.stderr)

        # exit with error
        sys.exit(1)
    raise

# reference kept alive so the OS doesn't release the bound port
_instance_lock_file = None
# kept alive so the OS doesn't GC the handle before a fault occurs
_fault_log_file = None


def _acquire_instance_lock() -> bool:
    """Use an exclusive lockfile as a single-instance guard.

    Returns True if this is the first instance; False if one is already running.
    The file handle is kept alive in _instance_lock_file for the entire process
    lifetime — closing it would release the lock. The OS releases the lock
    automatically on crash, so no stale lockfiles accumulate.
    """
    global _instance_lock_file
    import tempfile  # noqa: PLC0415

    lock_path = os.path.join(tempfile.gettempdir(), "storm_instance.lock")

    try:
        fh = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _instance_lock_file = fh  # keep alive to hold the lock
        return True
    except OSError:
        return False


def _case_archive_time(path: str):
    """The archive time a case package was made at; exits with a message if
    the file isn't a package this STORM can open."""
    from datetime import datetime  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    from core import case_package  # noqa: PLC0415
    try:
        manifest, _ = case_package.read(Path(path))
        return datetime.fromisoformat(manifest["case"]["archive_time"].replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        QMessageBox.warning(None, "Open Case Package", f"{Path(path).name}: {exc}")
        sys.exit(1)


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
            "  python main.py --debug --disable-mqtt"
        ),
    )

    # run in debug mode 
    parser.add_argument("--debug", action="store_true", help="enable debug logging and in-app debug panel")

    # open an archive case package straight away (File > Export Case Package)
    parser.add_argument(
        "--case", default=None, metavar="PACKAGE.zip",
        help="open this case package in ARCHIVE mode, skipping the launch window;\n"
             "its packed data is used before any download, so it opens offline",
    )

    # performance log for testing on field laptops (core/perf_log.py)
    parser.add_argument(
        "--perf-log", action="store_true",
        help="write timings, memory and UI responsiveness to perf_logs/ (also: STORM_PERF_LOG=1)",
    )

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
            "radar render resolution override (default runtime value: adaptive, starts at 768). "
            "Suggested values: 1536, 1024, 768, 512, 384, 256, 128"
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
    parser.add_argument("--disable-annotations", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-deploy-locs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-data-inputs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--mqtt-no-tls", action=argparse.BooleanOptionalAction, default=None)

    # performance options for weak hardware
    parser.add_argument(
        "--lite",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="lite mode: lower radar resolution, fewer surface plots, slower loops",
    )
    parser.add_argument(
        "--gpu-radar",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="experimental: draw live reflectivity with WebGL instead of Python (off in safe/lite mode)",
    )
    parser.add_argument(
        "--webengine-flags",
        default=None,
        metavar="FLAGS",
        help=(
            "extra Chromium flags for the map, e.g. \"--disable-gpu-compositing\" or "
            "\"--renderer-process-limit=1\""
        ),
    )

    # return the parsed arguments
    return parser

# function to register the custom storm:// URL scheme with WebEngine.
def _register_storm_scheme() -> None:
    try:
        from PyQt6.QtWebEngineCore import QWebEngineUrlScheme
        scheme = QWebEngineUrlScheme(b"storm")
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
        flags = (
            QWebEngineUrlScheme.Flag.SecureScheme
            | QWebEngineUrlScheme.Flag.CorsEnabled
        )
        # fetchApiAllowed lets JS fetch() use the storm:// scheme (Qt 6.2+)
        fetch_flag = getattr(QWebEngineUrlScheme.Flag, "FetchApiAllowed", None)
        if fetch_flag is not None:
            flags |= fetch_flag
        scheme.setFlags(flags)
        QWebEngineUrlScheme.registerScheme(scheme)
    except Exception as exc:
        print(f"[STORM] WARNING: could not register storm:// scheme: {exc}", flush=True)


# function to configure qt webengine
def _configure_qt_webengine_env() -> None:
    # if the platform is windows, runtime-safe forces software rendering
    if sys.platform == "win32":
        if runtime_flags.FLAGS.runtime_safe:
            os.environ["QMLSCENE_DEVICE"] = "softwarecontext"
            os.environ["QT_QUICK_BACKEND"] = "software"
            os.environ["QT_OPENGL"] = "software"
        else:
            os.environ.pop("QMLSCENE_DEVICE", None)
            os.environ.pop("QT_QUICK_BACKEND", None)
            os.environ.pop("QT_OPENGL", None)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = runtime_flags.chromium_flags()

def _configure_qt_application_attributes() -> None:
    """Set Qt attributes that must be configured before QApplication exists."""
    if runtime_flags.FLAGS.safe_map_mode:
        return
    QCoreApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts,
        True,
    )

# function to generate a default vehicle ID (if in monitor mode)
def _default_vehicle_id() -> str:
    # pull the host name
    host = (socket.gethostname() or "").strip().lower()

    # additional formatting for the host name
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")

    # derive a stable 4-char hex suffix from the MAC address so two machines
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

    # configure logging: every line carries the UTC date/time, this run's
    # session ID and the open case (see log_context)
    import log_context
    logging.basicConfig(level=level)
    for handler in logging.getLogger().handlers:
        log_context.install(handler)

    # write WARNING+ logs to a persistent file for post-session review
    try:
        from logging.handlers import RotatingFileHandler
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storm_errors.log")
        _fh = RotatingFileHandler(
            _log_path, mode="a", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        _fh.setLevel(logging.WARNING)
        log_context.install(_fh)
        logging.getLogger().addHandler(_fh)
    except Exception:
        pass

    # set logging levels (if the level is greater than DEBUG)
    if level > logging.DEBUG:
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("metpy").setLevel(logging.WARNING)
        # newer radar volumes carry message types MetPy doesn't decode and
        # STORM doesn't need ("Unknown message: 32", dozens per session)
        logging.getLogger("metpy.io.nexrad").setLevel(logging.ERROR)

def _start_perf_log(requested: bool) -> None:
    """--perf-log / STORM_PERF_LOG=1: start core/perf_log.py, and copy
    STORM's warnings and errors into it so one file holds everything."""
    from core import perf_log
    if not (requested or perf_log.requested()):
        return
    log_file = perf_log.start()
    if log_file is None:
        return
    import atexit
    atexit.register(perf_log.stop)

    class _ToPerfLog(logging.Handler):
        def emit(self, record):
            perf_log.event("log", level=record.levelname, logger=record.name, msg=record.getMessage()[:500])

    handler = _ToPerfLog(level=logging.WARNING)
    logging.getLogger().addHandler(handler)
    print(f"[STORM] performance log: {log_file}", flush=True)


_perf_heartbeat = None


def _perf_log_screen(app) -> None:
    """Log the screen, and start the UI heartbeat: a 200 ms timer that runs
    late by however long the window was too busy to respond."""
    global _perf_heartbeat
    from core import perf_log
    if not perf_log.enabled():
        return
    import time
    interval_ms = 200
    last = [time.perf_counter()]

    def beat():
        now = time.perf_counter()
        perf_log.note_ui_lag(max(0.0, (now - last[0]) * 1000.0 - interval_ms))
        last[0] = now

    _perf_heartbeat = QTimer()
    _perf_heartbeat.setInterval(interval_ms)
    _perf_heartbeat.timeout.connect(beat)
    _perf_heartbeat.start()
    try:
        screen = app.primaryScreen()
        size = screen.size()
        perf_log.event("screen", width=size.width(), height=size.height(),
                       scale=screen.devicePixelRatio(), dpi=round(screen.logicalDotsPerInch()),
                       screens=len(app.screens()))
    except Exception:  # noqa: BLE001
        pass


# function to check for missing required files and show user-visible warnings
def _warn_missing_files() -> None:
    """Show QMessageBox warnings for any missing required files.

    Called after QApplication is created but before the main window is built, so
    dialogs render correctly.  Warnings are non-fatal — the app continues in a
    degraded state so the user can at least see what is wrong.
    """
    # no tiles/storm.mbtiles: the base map comes over the network instead
    # (ui/map/html.py), so it's a note in the log and the status bar, not a warning
    from ui.map.html import TILES_PATH, basemap_source  # noqa: PLC0415
    if basemap_source() == "online":
        logging.getLogger(__name__).info(
            "Map tiles: %s not found; using online tiles (OpenFreeMap) while connected", TILES_PATH)

    if runtime_flags.FLAGS.disable_mqtt:
        return

    # the shipped viewer certificate covers VIEWER; the launch window says
    # what's missing if a publishing mode is picked without aws/
    if config.viewer_cert_present():
        return

    aws_dir = os.path.dirname(config.MQTT_CA_CERT)

    if not os.path.isdir(aws_dir):
        QMessageBox.warning(
            None,
            "Missing AWS Credentials Folder",
            f"The AWS credentials directory was not found:\n\n  {aws_dir}\n\n"
            "Only VIEWER mode can launch without it, and it gets no live MQTT data.\n"
            "Create the 'aws/' folder and add your certificates to enable the other modes.",
        )
    else:
        missing = config.missing_mqtt_cert_files()
        if missing:
            names = "\n".join(f"  \u2022 {os.path.basename(f)}" for f in missing)
            QMessageBox.warning(
                None,
                "Missing AWS Certificate Files",
                f"The following MQTT certificate files were not found:\n\n{names}\n\n"
                "Only VIEWER mode can launch without them, and it gets no live MQTT data.",
            )




# main function
def main() -> None:
    global _fault_log_file
    # try to configure fault handler
    try:
        # open the log file and keep it alive at module level so GC doesn't close it
        _fault_log_file = open("storm_fault.log", "a", buffering=1, encoding="utf-8")

        # enable the fault handler
        faulthandler.enable(file=_fault_log_file, all_threads=True)
    # if that fails
    except Exception:
        # do nothing
        pass

    # stop every fetch waiting on unreachable IPv6 addresses (see net_compat)
    net_compat.prefer_ipv4()

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
        disable_annotations = args.disable_annotations,
        disable_deploy_locs = args.disable_deploy_locs,
        disable_data_inputs = args.disable_data_inputs,
        mqtt_no_tls = args.mqtt_no_tls,
        lite_mode = args.lite,
        gpu_radar = args.gpu_radar,
        webengine_flags = args.webengine_flags,
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
    _start_perf_log(args.perf_log)

    # set the vehicle ID
    config.VEHICLE_ID = _normalize_vehicle_id(config.VEHICLE_ID)

    # configure the Qt webengine environment
    _configure_qt_webengine_env()

    # set Qt application attributes required before QApplication exists
    _configure_qt_application_attributes()

    # lite mode lowers the radar grid before any overlay is created
    if runtime_flags.FLAGS.lite_mode:
        from ui.map.radar_overlay import apply_lite_mode  # noqa: PLC0415
        apply_lite_mode()

    # register storm:// URL scheme — must happen before QApplication
    if not runtime_flags.FLAGS.safe_map_mode:
        _register_storm_scheme()

    # set the render grid size for radar overlays
    if args.render_grid_size > 0:
        # set the render grid size (radar_overlay loads matplotlib, so import it only here)
        from ui.map.radar_overlay import set_render_grid_size  # noqa: PLC0415
        set_render_grid_size(args.render_grid_size)

    # create the application
    app = QApplication(sys.argv)
    _perf_log_screen(app)

    # set application properties
    app.setApplicationName("STORM")
    app.setOrganizationName("STORM")
    app.setFont(QFont("Segoe UI", 10))
    # scale stylesheet font sizes by the saved MAP → TEXT setting (before any window exists)
    text_scale.install(app)

    # set application quit behavior
    app.setQuitOnLastWindowClosed(False)

    if not _acquire_instance_lock():
        QMessageBox.warning(
            None,
            "STORM Already Running",
            "STORM is already running on this machine.\n\n"
            "Please close all existing STORM instances before opening a new one.",
        )
        sys.exit(0)


    sys.exit(_run_sessions(app, args))


def _run_sessions(app, args, window_class=None) -> int:
    """Open the launch window and the main window, and loop: a window
    closed by Change Day, or to open a case package for another date, sets
    ``restart_with`` (a SessionRestart), and the next session opens without
    quitting STORM. Returns the exit code."""
    if window_class is None:
        from ui.app.main_window import MainWindow as window_class  # noqa: PLC0415

    _warn_missing_files()
    verified_modes: set[str] = set()     # "admin" once its passphrase is given in this run
    restart = None
    first = True
    while True:
        monitor = viewer = False
        case_package = None
        if first and args.case:
            # a case package names its own date; the launch window is skipped
            archive_time = _case_archive_time(args.case)
            case_package = args.case
        elif restart is not None and not restart.launch_window:
            # a case package for another date: straight to it
            archive_time, case_package = restart.archive_time, restart.case_package
        else:
            # show the launch dialog for mode selection (and the admin passphrase)
            dialog = LaunchDialog(
                mode="archive" if restart is not None else None,
                archive_time=restart.archive_time if restart is not None else None,
                verified_modes=verified_modes,
            )

            # if the dialog is not accepted
            if dialog.exec() != QDialog.DialogCode.Accepted:
                # exit
                return 0

            # get the vehicle ID
            config.VEHICLE_ID = _normalize_vehicle_id(dialog.vehicle_id())

            # get the vehicle icon type selected by the user
            config.VEHICLE_ICON = dialog.vehicle_icon()

            # get the directory for real-time observation files (if any)
            config.OBS_FILE_DIR      = dialog.data_dir()
            config.OBS_FILE_GPS_MODE = dialog.gps_file_mode()

            # get mode from the dialog
            monitor      = dialog.monitor()
            viewer       = dialog.viewer()
            archive_time = dialog.archive_start_time()   # None unless archive mode
            case_package = dialog.case_package()         # a case to open at launch, if chosen
            runtime_flags.FLAGS.admin_mode = dialog.admin_mode()
            if dialog.admin_mode():
                verified_modes.add("admin")

            # apply radar render resolution from the launch dialog
            _dlg_res = dialog.radar_resolution()
            if _dlg_res > 0:
                from ui.map.radar_overlay import set_render_grid_size, set_adaptive_render_grid  # noqa: PLC0415
                set_render_grid_size(_dlg_res)
                set_adaptive_render_grid(False)
            dialog.deleteLater()

        from ui.dialogs.loading_dialog import LoadingDialog  # noqa: PLC0415
        loading_dialog = LoadingDialog()
        loading_dialog.show()
        app.processEvents()  # Force the dialog to render immediately

        # create the main window
        window = window_class(
            debug=args.debug,
            monitor=monitor,
            viewer=viewer,
            archive_time=archive_time,
            case_package=case_package,
        )

        # close loading dialog once main window is ready
        loading_dialog.close()
        loading_dialog = None

        # define the JS console message handler
        js_log = logging.getLogger("storm.js")

        # if the map widget has a page
        if hasattr(window.map_widget, "page"):
            # define the JS console message handler
            def handle_js_message(level, message, line, source):
                js_log.debug("JS [%s:%s] %s", source, line, message)

            # set the JS console message handler
            window.map_widget.page().javaScriptConsoleMessage = handle_js_message

        window.show()

        # if a truck replay file is specified (first session only)
        if first and args.truck_replay_file:
            # start the truck replay
            _start_truck_replay(window=window, file_path=args.truck_replay_file, interval_ms = max(50, args.truck_replay_interval_ms),
                                restamp=args.truck_replay_restamp)
        first = False

        # run the application; closeEvent quits the loop either way
        code = app.exec()
        restart = window.restart_with
        if restart is None:
            # a real quit: no deleteLater here, there's no event loop left to
            # run it, and the process tears QtWebEngine down on exit
            return code

        # the sounding, VAD and other windows belong to the session that closed
        for widget in app.topLevelWidgets():
            if widget is not window and widget.isWindow() and widget.isVisible():
                widget.close()
        # deleted now, before the next MainWindow is built: with no event
        # loop running, processEvents() alone wouldn't delete it
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        window = None


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
