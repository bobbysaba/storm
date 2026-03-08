# main.py
# Entry point: parse CLI args, configure logging, and launch Qt application.

import argparse
import faulthandler
import logging
import os
import re
import socket
import sys

import runtime_flags


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

    parser.add_argument("--debug", action="store_true", help="enable debug logging and in-app debug panel")
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="monitor mode: skip local obs inputs; MQTT sync for map edits remains enabled",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity (default: WARNING; --debug overrides to DEBUG)",
    )
    parser.add_argument(
        "--truck-replay-file",
        default="",
        help="path to CSV/TXT truck data file to replay locally (no MQTT)",
    )
    parser.add_argument(
        "--truck-replay-interval-ms",
        type=int,
        default=1000,
        help="milliseconds between replayed truck samples (default: 1000)",
    )
    parser.add_argument(
        "--truck-replay-restamp",
        action="store_true",
        help=(
            "shift replay timestamps so the last obs lands at now; useful for old files so "
            "history/freshness behavior matches live data"
        ),
    )
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

    parser.add_argument(
        "--debug-run",
        type=int,
        choices=range(0, 7),
        default=0,
        metavar="N",
        help="quick diagnostic profile number (see 'Debug-run profiles' below)",
    )
    parser.add_argument(
        "--runtime-safe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="force software rendering and disable background ingest services for this run",
    )
    parser.add_argument(
        "--safe-map-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="disable WebEngine map and show the Safe Map Mode placeholder for this run",
    )

    parser.add_argument("--enable-startup-toggles", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-radar", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-mqtt", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-vehicle-fetcher", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-annotations", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-deploy-locs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--disable-data-inputs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--mqtt-no-tls", action=argparse.BooleanOptionalAction, default=None)

    return parser


def _configure_qt_webengine_env() -> None:
    # Must run before importing modules that pull in QtWebEngine classes.
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
    else:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --in-process-gpu"


def _default_vehicle_id() -> str:
    host = (socket.gethostname() or "device").strip().lower()
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-")
    if not host:
        host = "device"
    return f"storm-{host}"


def _normalize_vehicle_id(raw: str) -> str:
    vid = (raw or "").strip().lower()
    vid = re.sub(r"[^a-z0-9-]+", "-", vid).strip("-")
    if not vid or vid == "storm":
        return _default_vehicle_id()
    return vid


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if level > logging.DEBUG:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("metpy").setLevel(logging.WARNING)


def main() -> None:
    try:
        _fh = open("storm_fault.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass

    parser = _build_parser()
    args = parser.parse_args()

    runtime_flags.reset_flags()
    runtime_flags.apply_debug_run_profile(args.debug_run)
    runtime_flags.apply_overrides(
        runtime_safe=args.runtime_safe,
        safe_map_mode=args.safe_map_mode,
        enable_startup_toggles=args.enable_startup_toggles,
        disable_radar=args.disable_radar,
        disable_mqtt=args.disable_mqtt,
        disable_vehicle_fetcher=args.disable_vehicle_fetcher,
        disable_annotations=args.disable_annotations,
        disable_deploy_locs=args.disable_deploy_locs,
        disable_data_inputs=args.disable_data_inputs,
        mqtt_no_tls=args.mqtt_no_tls,
    )
    runtime_flags.finalize_flags()

    if args.debug_run > 0 and not args.debug:
        args.debug = True

    log_level = "DEBUG" if args.debug else args.log_level
    _configure_logging(log_level)

    import config

    config.VEHICLE_ID = _normalize_vehicle_id(config.VEHICLE_ID)

    _configure_qt_webengine_env()

    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QApplication, QDialog
    except ModuleNotFoundError as exc:
        if exc.name == "PyQt6":
            print(
                "PyQt6 is not installed for this Python interpreter.\n"
                "Activate the conda environment first:\n"
                "  conda activate storm\n"
                "Then run:\n"
                "  python main.py",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    from ui.launch_dialog import LaunchDialog
    from ui.main_window import MainWindow
    from ui.radar_overlay import set_render_grid_size

    if args.render_grid_size > 0:
        set_render_grid_size(args.render_grid_size)

    app = QApplication(sys.argv)
    app.setApplicationName("STORM")
    app.setOrganizationName("STORM")
    app.setFont(QFont("Segoe UI", 10))
    app.setQuitOnLastWindowClosed(False)

    monitor = args.monitor
    if not monitor:
        dialog = LaunchDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        config.VEHICLE_ID = _normalize_vehicle_id(dialog.vehicle_id())
        config.OBS_FILE_DIR = dialog.data_dir()
        monitor = dialog.monitor()

    window = MainWindow(debug=args.debug, monitor=monitor)

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


def _start_truck_replay(window, file_path: str, interval_ms: int, restamp: bool = False) -> None:
    from dataclasses import replace
    from datetime import datetime, timezone

    from PyQt6.QtCore import QTimer

    from data.truck_replay import load_truck_observations

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
        now = datetime.now(timezone.utc)
        shift = now - observations[-1].timestamp
        observations = [replace(o, timestamp=o.timestamp + shift) for o in observations]
        log.info("restamped %d obs (shift=%.0fs)", len(observations), shift.total_seconds())

    log.info(
        "truck replay loaded %d rows from %s (interval=%dms, restamp=%s)",
        len(observations),
        file_path,
        interval_ms,
        restamp,
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

    def start_replay():
        tick()
        window._truck_replay_timer.start()

    QTimer.singleShot(1400, start_replay)


if __name__ == "__main__":
    main()