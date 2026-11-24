"""
Records broker traffic for ARCHIVE mode to replay: every message under the
topic root goes, one line each, into ``<folder>/<prefix>.<kind>.<YYYYMMDD>``
(the UTC day it arrived), the files archive/history.py reads. The layout is
in docs/archive-data.md.

Run it on any machine that stays connected through the field day:

    python -m archive.recorder [--folder recordings]

It uses the VIEWER login ([mqtt.viewer], else [mqtt]) and only subscribes.
MONITOR mode can record too (File > Record Broker Traffic), with the same
Recorder.

Standard library and paho only: no Qt.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from headless.storm_headless import topics
from headless.storm_headless.connect_errors import describe_connect_error

log = logging.getLogger(__name__)


def _file_prefix(topic_root: str) -> str:
    # settings.file_prefix, without importing settings (which reads storm.toml)
    return topic_root.replace("/", ".")


class Recorder:
    """Appends each message it's handed to the day's file for its kind.
    Thread-safe: paho calls it from its own thread."""

    def __init__(self, folder: Path, topic_root: str):
        self.folder = Path(folder)
        self.topic_root = topic_root
        self._prefix = _file_prefix(topic_root)
        self._lock = threading.Lock()
        self._files: dict[str, tuple[str, object]] = {}   # kind -> (date, open file)
        self.count = 0

    def path_for(self, kind: str, day: datetime) -> Path:
        return self.folder / f"{self._prefix}.{kind}.{day:%Y%m%d}"

    def handle(self, topic: str, payload: bytes, retained: bool = False,
               now: datetime | None = None) -> bool:
        """Record one message; True if it was written. Skipped: topics that
        aren't STORM's, retained copies the broker hands a new subscription
        (recorded when first published), and payloads that aren't a JSON
        object. An empty payload (the standard way to clear a retained
        message) is written as the delete it means."""
        if retained:
            return False
        kind = item = None
        for k in topics.KINDS:
            item = topics.item_id(self.topic_root, k, topic)
            if item is not None:
                kind = k
                break
        if kind is None:
            return False
        now = now or datetime.now(timezone.utc)
        line = _line(kind, item, payload, now)
        if line is None:
            return False
        with self._lock:
            fh = self._file(kind, now)
            fh.write(line + "\n")
            fh.flush()
            self.count += 1
        return True

    def _file(self, kind: str, now: datetime):
        day = f"{now:%Y%m%d}"
        open_day, fh = self._files.get(kind, (None, None))
        if open_day != day:
            if fh is not None:
                fh.close()
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.path_for(kind, now)
            fh = path.open("a", encoding="utf-8")
            log.info("recorder: writing %s", path)
            self._files[kind] = (day, fh)
        return fh

    def close(self) -> None:
        with self._lock:
            for _, fh in self._files.values():
                fh.close()
            self._files.clear()


def _line(kind: str, item: str, payload: bytes, now: datetime) -> str | None:
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        stamp = now.isoformat()
        if kind == topics.VEHICLES:
            return None
        if kind == topics.SCAN_SECTORS:
            return json.dumps({"vehicle_id": item, "active": False, "timestamp": stamp})
        return json.dumps({"id": item, "deleted": True, "deleted_at": stamp})
    try:
        obj = json.loads(text)
    except ValueError:
        log.debug("recorder: not JSON on %s/%s", kind, item)
        return None
    if not isinstance(obj, dict):
        return None
    # one payload per line, as published unless it spans lines
    return text if "\n" not in text and "\r" not in text else json.dumps(obj)


def _client_id_base() -> str:
    host = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")
    return f"recorder-{host or 'host'}"


def main(argv=None) -> int:
    import paho.mqtt.client as mqtt

    import settings

    parser = argparse.ArgumentParser(
        prog="python -m archive.recorder",
        description="Record STORM broker traffic to daily files for ARCHIVE mode.")
    parser.add_argument("--folder", type=Path, default=None,
                        help="where to write (default: [archive] recordings, else recordings/)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    s = settings.SETTINGS
    # each table stands alone (settings.load): a bad [mqtt] shows up as no broker below
    for line in s.problems:
        log.warning("%s: %s", s.path.name, line)
    broker = s.mqtt_for(viewer=True)
    if not broker.configured:
        log.error("no broker: add an [mqtt] host to %s", s.path)
        return 2
    folder = args.folder or s.archive.recordings
    recorder = Recorder(folder, broker.topic_root)
    stopping = threading.Event()
    # whether the broker answered this connection, and the last reason given
    state = {"acked": False, "refused": False, "failure": ""}

    def on_connect(client, userdata, flags, reason_code, properties):
        state["acked"] = True
        if reason_code.is_failure:
            state["refused"] = True
            log.warning("recorder: broker refused the login: %s (check the [mqtt.viewer] "
                        "username and STORM_MQTT_VIEWER_PASSWORD)", reason_code)
            return
        state["refused"], state["failure"] = False, ""
        for kind in topics.KINDS:
            _, mid = client.subscribe(topics.subscription(broker.topic_root, kind), qos=1)
            subs[mid] = topics.subscription(broker.topic_root, kind)
        log.info("recorder: connected; recording %s/# to %s", broker.topic_root, folder)

    subs: dict[int, str] = {}

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        for code in reason_codes or ():
            if code.is_failure:
                log.warning("recorder: the broker refused %s (%s): check topic_root and what "
                            "the viewer login may read", subs.get(mid, "?"), code)

    def on_connect_fail(client, userdata):
        reason = describe_connect_error(broker.host, broker.port, ctx)
        if reason != state["failure"]:   # paho retries; say each new reason once
            log.warning("recorder: can't connect to %s:%d: %s; retrying", broker.host, broker.port, reason)
            state["failure"] = reason

    def on_disconnect(client, userdata, flags, reason_code, properties):
        acked, state["acked"] = state["acked"], False
        if stopping.is_set():
            return
        if state["refused"]:
            state["refused"] = False
        elif not acked:
            on_connect_fail(client, userdata)
        else:
            log.warning("recorder: disconnected (%s); reconnecting", reason_code)

    def stop(*_):
        stopping.set()
        c.disconnect()

    def on_message(client, userdata, message):
        recorder.handle(message.topic, bytes(message.payload), retained=bool(message.retain))

    client_id = broker.client_id(_client_id_base())
    c = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=client_id, protocol=mqtt.MQTTv5)
    c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
    c.on_connect_fail = on_connect_fail
    c.on_subscribe = on_subscribe
    try:
        ctx = broker.tls_context()
    except (OSError, ValueError) as exc:
        log.error("recorder: %s", exc)
        return 2
    if ctx is not None:
        c.tls_set_context(ctx)
    if broker.username:
        c.username_pw_set(broker.username, broker.password or None)
    c.reconnect_delay_set(2, 30)
    log.info("recorder: connecting to %s:%d as %r", broker.host, broker.port, client_id)
    c.connect_async(broker.host, broker.port, keepalive=60)
    # a service manager stops it with SIGTERM: leave the loop the same way as Ctrl-C
    signal.signal(signal.SIGTERM, stop)
    try:
        c.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        recorder.close()
        log.info("recorder: stopped after %d messages", recorder.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
