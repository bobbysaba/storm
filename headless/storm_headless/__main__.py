"""Command-line entry point: storm-headless --config storm-headless.toml"""

import argparse
import logging
import signal
import sys
import threading
from datetime import datetime, timezone

from . import __version__
from .publisher import DryRunPublisher, VehiclePublisher, build_tls_context
from .runner import run
from .settings import Settings, SettingsError, load_settings

log = logging.getLogger("storm_headless")


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="storm-headless",
        description="Tail a vehicle logger file and publish observations to the STORM MQTT broker.",
    )
    p.add_argument("-c", "--config", help="path to the TOML config file")
    p.add_argument("--vehicle-id", help="override vehicle_id from the config")
    p.add_argument("--data-dir", help="override data_dir from the config")
    p.add_argument("--icon-type", help="override icon_type from the config")
    p.add_argument("--client-id", help="override the MQTT client id (default <vehicle_id>-pi)")
    p.add_argument("--dry-run", action="store_true",
                   help="parse the file and log payloads without connecting to MQTT")
    p.add_argument("--check", action="store_true",
                   help="validate the config, data directory, and certificates, then exit")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        settings = load_settings(
            args.config,
            vehicle_id=args.vehicle_id,
            data_dir=args.data_dir,
            icon_type=args.icon_type,
            client_id=args.client_id,
        )
    except SettingsError as e:
        log.error("%s", e)
        return 2

    if args.check:
        return _check(settings, need_certs=not args.dry_run)

    if not settings.data_dir.is_dir():
        log.warning("data_dir %s does not exist yet; waiting for it", settings.data_dir)

    publisher = DryRunPublisher(settings) if args.dry_run else VehiclePublisher(settings)

    stop_event = threading.Event()

    def _stop(signum, _frame):
        log.info("received %s, shutting down", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info("storm-headless %s starting", __version__)
    try:
        run(settings, publisher, stop_event)
    except (OSError, ValueError) as e:
        # missing/empty/unreadable certificates surface here from publisher.start()
        log.error("%s", e)
        return 1
    return 0


def _check(settings: Settings, need_certs: bool = True) -> int:
    """Print what would run and whether it can; return 0 when everything is ready."""
    ok = True

    def line(status: str, msg: str):
        print(f"[{status:>4}] {msg}")

    line("ok", f"vehicle_id={settings.vehicle_id} icon_type={settings.icon_type} "
               f"client_id={settings.effective_client_id}")
    line("ok", f"broker {settings.mqtt_host}:{settings.mqtt_port}")

    layout = "GPS Ka" if settings.gps_mode else "FOFS"
    if not settings.data_dir.is_dir():
        line("FAIL", f"data_dir {settings.data_dir} does not exist")
        ok = False
    else:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        pattern = "*_????????.txt" if settings.gps_mode else f"*{today}.txt"
        files = sorted(settings.data_dir.glob(pattern))
        if files:
            line("ok", f"data_dir {settings.data_dir} ({layout}): {files[-1].name}")
        else:
            # not fatal: the logger may simply not have started yet today
            line("warn", f"data_dir {settings.data_dir} ({layout}): no {pattern} file yet")

    try:
        build_tls_context(settings)
        line("ok", f"certificates load from {settings.cert_file.parent}")
    except (OSError, ValueError) as e:
        line("FAIL" if need_certs else "warn", f"certificates: {e}")
        ok = ok and not need_certs

    print("ready" if ok else "not ready")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
