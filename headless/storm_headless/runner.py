import logging
import threading
import time
from typing import Callable, Protocol

from .obs_tail import GPS_KA_FIELD_MAP, FieldMap, ObsFileTail
from .observation import Observation
from .settings import Settings

log = logging.getLogger(__name__)

# how often to log a one-line status summary
STATUS_INTERVAL_S = 60.0
# warn when the logger file has produced nothing for this long
STALE_WARN_S = 60.0


class _Tail(Protocol):
    def poll(self) -> Observation | None: ...


class _Publisher(Protocol):
    connected: bool
    published_count: int
    def publish_latest(self, obs: Observation) -> None: ...


class PublishLoop:
    """
    One tick per poll interval: read new rows from the tail, and publish the
    latest observation at most once per publish interval.  Separate from the
    sleeping/threading so it can be driven with a fake clock in tests.
    """

    def __init__(self, tail: _Tail, publisher: _Publisher, publish_s: float,
                 clock: Callable[[], float] = time.monotonic):
        self._tail = tail
        self._publisher = publisher
        self._publish_s = publish_s
        self._clock = clock

        self._latest: Observation | None = None
        self._latest_sent = True
        self._last_publish = float("-inf")
        self._last_obs_at: float | None = None
        self._started_at = clock()
        self._stale_warned = False

    @property
    def latest(self) -> Observation | None:
        return self._latest

    def tick(self):
        now = self._clock()

        obs = self._tail.poll()
        if obs is not None:
            self._latest = obs
            self._latest_sent = False
            self._last_obs_at = now
            if self._stale_warned:
                log.info("logger file is updating again")
                self._stale_warned = False

        if not self._latest_sent and now - self._last_publish >= self._publish_s:
            self._publisher.publish_latest(self._latest)
            self._latest_sent = True
            self._last_publish = now

        quiet_since = self._last_obs_at if self._last_obs_at is not None else self._started_at
        if not self._stale_warned and now - quiet_since >= STALE_WARN_S:
            log.warning("no new rows from the logger file for %.0fs", now - quiet_since)
            self._stale_warned = True


def run(settings: Settings, publisher, stop_event: threading.Event):
    """Run until *stop_event* is set."""
    field_map = GPS_KA_FIELD_MAP if settings.gps_mode else FieldMap()
    tail = ObsFileTail(str(settings.data_dir), settings.vehicle_id,
                       field_map=field_map, gps_mode=settings.gps_mode)
    loop = PublishLoop(tail, publisher, settings.publish_s)

    log.info("tailing %s (%s) as %s every %.1fs, publishing every %.1fs",
             settings.data_dir, "GPS Ka" if settings.gps_mode else "FOFS",
             settings.vehicle_id, settings.poll_s, settings.publish_s)

    publisher.start()
    next_status = time.monotonic() + STATUS_INTERVAL_S
    try:
        while not stop_event.is_set():
            try:
                loop.tick()
            except Exception:
                # one bad poll must not kill a service that runs unattended
                log.exception("poll failed")

            if time.monotonic() >= next_status:
                next_status += STATUS_INTERVAL_S
                latest = loop.latest
                log.info(
                    "status: mqtt=%s published=%d file=%s latest=%s",
                    "up" if publisher.connected else "down",
                    publisher.published_count,
                    tail.current_path,
                    latest.timestamp.strftime("%H:%M:%SZ") if latest else "none",
                )

            stop_event.wait(settings.poll_s)
    finally:
        publisher.stop()
