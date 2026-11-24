
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.geo import haversine_km


@dataclass
class ArchiveSession:
    """
    Immutable configuration for an archive replay session.

    Attributes
    ----------
    start_time : datetime
        UTC datetime at which the replay begins.
    radar_station : str | None
        NEXRAD 4-letter station ID (e.g. "KTLX").  None until the first
        vehicle position is found and the nearest station is resolved.
    """

    start_time: datetime
    radar_station: Optional[str] = None

    def __post_init__(self):
        # normalise to UTC so callers don't have to worry about tz.
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        else:
            self.start_time = self.start_time.astimezone(timezone.utc)

    @property
    def date_str(self) -> str:
        """UTC date string YYYY-MM-DD."""
        return self.start_time.strftime("%Y-%m-%d")


# An archive session covers its UTC day and, since intercepts routinely run
# into the evening after 00Z, up to 06Z of the next day. Those are the
# defaults; [archive] session_start_utc / session_hours /
# session_activity_after_hours in storm.toml move the window for teams
# elsewhere (the window always ends by the end of the next UTC day). Where inside that
# span the timeline actually ends depends on the day's activity -- the last
# time a vehicle was driving or the crew touched a scan sector, annotation,
# cone or drawing -- plus a margin; that can be well before 00Z on a quiet
# day. Mesonet racks are sometimes left logging overnight, so a vehicle
# that is only parked never extends the session. Activity before 12Z on the
# session day is the previous local day's evening (e.g. the drive back to
# the hotel after 00Z), so it says nothing about when this session ends;
# with no activity from 12Z on, the session keeps its full UTC day.
ACTIVITY_MARGIN = timedelta(minutes=30)
# Driving: covering more than this distance within a minute.
_MOVING_KM_PER_MINUTE = 0.12  # 2 m/s
_MOVING_WINDOW_S = 60


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _window():
    import settings  # noqa: PLC0415
    return settings.SETTINGS.archive


def activity_from() -> timedelta:
    """How long after the session's start activity begins to count."""
    return timedelta(hours=_window().session_activity_after_hours)


def session_bounds(session_time: datetime) -> tuple[datetime, datetime]:
    """(start of the session on its UTC date, latest possible end)."""
    window = _window()
    day = _utc(session_time).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day + timedelta(hours=window.session_start_utc)
    return start, start + timedelta(hours=window.session_hours)


def session_days(session_time: datetime) -> tuple[datetime, datetime]:
    """Midnights of the UTC dates whose files a session reads: its own day
    and the next one."""
    day = _utc(session_time).replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day + timedelta(days=1)


def last_moving_time(observations: Iterable) -> Optional[datetime]:
    """Latest time a vehicle track shows it driving (not merely logging while
    parked). `observations` need .timestamp/.lat/.lon, sorted by time."""
    observations = list(observations)
    latest = None
    j = 0
    for i, obs in enumerate(observations):
        while (observations[j].timestamp - obs.timestamp).total_seconds() < -_MOVING_WINDOW_S:
            j += 1
        if j < i and _km(observations[j], obs) > _MOVING_KM_PER_MINUTE:
            latest = obs.timestamp
    return latest


def activity_end(
    session_time: datetime,
    activity_times: Iterable[Optional[datetime]],
) -> Optional[datetime]:
    """Where the timeline should end, or None when nothing shows this day's
    activity (then the caller keeps the full window). Only activity from
    activity_from() after the start (12Z by default) counts. Never earlier
    than half an hour after the time the session was opened at, never past
    the cap."""
    start, cap = session_bounds(session_time)
    times = [t for t in activity_times
             if t is not None and start + activity_from() <= t <= cap]
    if not times:
        return None
    opened = _utc(session_time)
    return min(cap, max(max(times) + ACTIVITY_MARGIN, opened + ACTIVITY_MARGIN))


def _km(a, b) -> float:
    return haversine_km(a.lat, a.lon, b.lat, b.lon)
