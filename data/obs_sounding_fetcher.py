# data/obs_sounding_fetcher.py
# On-demand fetcher for observed (radiosonde) soundings via IEM RAOB API.
#
# API endpoint:
#   https://mesonet.agron.iastate.edu/json/raob.py?station=OUN&ts=YYYY-MM-DDTHH:MM:SSZ
#
# Time logic:
#   - Query the standard 00Z/12Z launch times for the current UTC date.
#   - If UTC hour < 12, also query the previous UTC date for overnight continuity
#     (e.g. at 02Z on the 26th you still want the 25th's soundings).
#   - Profiles are filtered to valid_time <= now and sorted chronologically.
#
# Per-level units from IEM:
#   pres  (mb/hPa), hght (m MSL), tmpc (°C), dwpc (°C),
#   drct  (deg meteorological), sknt (knots)
#
# Wind conversion: (drct, sknt) → (u_wind, v_wind) in m/s
#   u = -speed_ms * sin(drct_rad)   (eastward component)
#   v = -speed_ms * cos(drct_rad)   (northward component)

import json
import logging
import math
import threading
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet

log = logging.getLogger(__name__)

_BASE_URL        = "https://mesonet.agron.iastate.edu/json/raob.py"
_REQUEST_TIMEOUT = 20   # seconds
_KNOTS_TO_MS     = 0.51444


class ObsSoundingFetcher(QObject):
    """Fire-and-forget fetcher for observed (radiosonde) soundings from IEM.

    Signals:
        sounding_ready(SoundingSet)  — emitted on the main thread when data arrives
        fetch_error(str)             — emitted on recoverable errors
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def fetch(
        self,
        station_id:   str,
        station_name: str,
        lat:          float,
        lon:          float,
        elevation:    float,
    ):
        """Start a background fetch for the given station.  Returns immediately."""
        threading.Thread(
            target=self._bg_fetch,
            args=(station_id, station_name, lat, lon, elevation),
            daemon=True,
        ).start()
        log.debug("obs sounding fetch started (%s)", station_id)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _bg_fetch(self, station_id, station_name, lat, lon, elevation):
        try:
            result = _fetch_sounding_set(station_id, station_name, lat, lon, elevation)
            self.sounding_ready.emit(result)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"OBS sounding fetch failed ({station_id}): {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"OBS sounding error ({station_id}): {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _timestamps_to_query() -> list[str]:
    """Return list of synoptic RAOB timestamps to query.

    Query all four standard RAOB synoptic times (00Z, 06Z, 12Z, 18Z) for the
    current UTC date.  If we are before 12Z, also include the previous UTC date
    so overnight continuity is preserved (e.g. 02Z on the 26th still shows the
    25th's soundings).  The future-sounding filter in _fetch_sounding_set() will
    discard any slot that hasn't launched yet.
    """
    now = datetime.now(timezone.utc)
    dates = [now.date()]
    if now.hour < 12:
        dates.append((now - timedelta(days=1)).date())
    timestamps: list[str] = []
    for date_obj in dates:
        for hour in (0, 6, 12, 18):
            ts = datetime(
                date_obj.year, date_obj.month, date_obj.day, hour, 0, 0,
                tzinfo=timezone.utc,
            )
            timestamps.append(ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return timestamps


def _fetch_profiles_for_timestamp(station_id: str, ts: str) -> list:
    """Fetch raw IEM profiles for one station/timestamp. Returns profile dicts."""
    url = f"{_BASE_URL}?station={station_id}&ts={ts}"
    log.debug("obs sounding URL: %s", url)
    with urlopen(url, timeout=_REQUEST_TIMEOUT) as resp:
        raw = resp.read()
    log.debug("obs sounding response: %.1f KB", len(raw) / 1024)
    data = json.loads(raw)
    return data.get("profiles", [])


def _fetch_sounding_set(
    station_id:   str,
    station_name: str,
    lat:          float,
    lon:          float,
    elevation:    float,
) -> SoundingSet:
    now_utc    = datetime.now(timezone.utc)
    fetch_time = now_utc

    # Collect all profiles across the relevant synoptic times
    raw_profiles = []
    for ts in _timestamps_to_query():
        try:
            raw_profiles.extend(_fetch_profiles_for_timestamp(station_id, ts))
        except Exception as e:
            log.warning("failed to fetch %s for %s: %s", station_id, ts, e)

    if not raw_profiles:
        raise ValueError(f"No sounding data returned for {station_id}")

    # Parse each profile into a Sounding, filtering to valid_time <= now
    soundings = []
    for idx, prof in enumerate(raw_profiles):
        valid_str = prof.get("valid", "")
        try:
            valid_time = datetime.fromisoformat(valid_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            log.warning("unparseable valid time %r — skipping", valid_str)
            continue

        # Skip future soundings
        if valid_time > now_utc:
            log.debug("skipping future obs sounding at %s", valid_str)
            continue

        sounding = _parse_profile(
            lat, lon, elevation, prof.get("profile", []), valid_time, idx
        )
        if sounding is not None:
            soundings.append(sounding)

    if not soundings:
        raise ValueError(f"No valid sounding time slots found for {station_id}")

    # Some timestamps can resolve to the same valid profile; keep unique valid times.
    deduped: dict[datetime, Sounding] = {}
    for sounding in soundings:
        deduped[sounding.valid_time] = sounding

    # Sort chronologically; use index as slot_offset
    soundings = list(deduped.values())
    soundings.sort(key=lambda s: s.valid_time)
    for i, s in enumerate(soundings):
        s.slot_offset = i
        s.label       = _format_label(s.valid_time)

    return SoundingSet(
        lat          = lat,
        lon          = lon,
        elevation    = elevation,
        fetch_time   = fetch_time,
        soundings    = soundings,
        station_id   = station_id,
        station_name = station_name,
        source       = "obs",
    )


def _parse_profile(
    lat:        float,
    lon:        float,
    site_elev:  float,
    levels:     list,
    valid_time: datetime,
    idx:        int,
) -> "Sounding | None":
    """Parse one IEM profile's level list into a Sounding.

    Skips levels where pressure, height, temperature, or dewpoint is missing.
    Wind is set to zero when direction or speed is missing.
    """
    pressures = []
    temps     = []
    dewpts    = []
    u_winds   = []
    v_winds   = []
    heights   = []

    for lv in levels:
        pres = lv.get("pres")
        hght = lv.get("hght")
        tmpc = lv.get("tmpc")
        dwpc = lv.get("dwpc")
        drct = lv.get("drct")
        sknt = lv.get("sknt")

        # Required fields
        if any(v is None for v in (pres, hght, tmpc, dwpc)):
            continue

        # Drop below-ground levels
        if float(hght) < site_elev - 10:
            continue

        # Wind: use zero components when missing
        if drct is not None and sknt is not None:
            speed_ms  = float(sknt) * _KNOTS_TO_MS
            drct_rad  = math.radians(float(drct))
            u = -speed_ms * math.sin(drct_rad)
            v = -speed_ms * math.cos(drct_rad)
        else:
            u = 0.0
            v = 0.0

        pressures.append(float(pres))
        temps.append(float(tmpc))
        dewpts.append(float(dwpc))
        u_winds.append(u)
        v_winds.append(v)
        heights.append(float(hght))

    if len(pressures) < 5:
        log.warning(
            "obs sounding at %s has only %d valid levels — skipping",
            valid_time.isoformat(), len(pressures),
        )
        return None

    return Sounding(
        lat         = lat,
        lon         = lon,
        valid_time  = valid_time,
        slot_offset = idx,          # will be reassigned after sorting
        label       = "",           # will be set after sorting
        pressure    = np.array(pressures, dtype=np.float64),
        temperature = np.array(temps,     dtype=np.float64),
        dewpoint    = np.array(dewpts,    dtype=np.float64),
        u_wind      = np.array(u_winds,   dtype=np.float64),
        v_wind      = np.array(v_winds,   dtype=np.float64),
        height      = np.array(heights,   dtype=np.float64),
    )


def _format_label(dt: datetime) -> str:
    """Return a scrubber label like '00Z Mar 25'."""
    return f"{dt.hour:02d}Z {dt.strftime('%b')} {dt.day}"
