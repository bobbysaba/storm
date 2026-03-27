# archive/fetchers/hazard_archive_fetcher.py
# ArchiveHazardFetcher — retrieves historical NWS warnings, SPC watches, and
# SPC outlooks from IEM's archive APIs.
#
# Data sources
# ------------
# NWS Storm-Based Warnings (SBW):
#   https://mesonet.agron.iastate.edu/geojson/sbw.geojson?ts=YYYY-MM-DDTHH:MM:SS
#   Returns all warnings valid at the given timestamp.
#
# SPC Mesoscale Watches:
#   https://mesonet.agron.iastate.edu/json/watches.json?date=YYYY-MM-DD
#   Returns all watches issued on that UTC date.
#
# SPC Day-1 Outlook:
#   https://mesonet.agron.iastate.edu/api/1/spc/outlook.geojson?day=1&valid={YYYYMMDDTHHMM}
#   Returns the outlook valid at the given UTC time.
#
# Strategy
# --------
# • Warnings: fetched for the current timestamp from IEM with a 5-minute
#   resolution cache (avoid hitting IEM on every 10-second tick).
# • Watches: entire day fetched once; client-side filtered by issue/expire time.
# • Outlooks: fetched when the cycle changes (SPC issues ~4 outlooks per day).

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_IEM_SBW_URL      = "https://mesonet.agron.iastate.edu/geojson/sbw.geojson"
_IEM_WATCHES_URL  = "https://mesonet.agron.iastate.edu/json/watches.json"
_IEM_OUTLOOK_URL  = "https://mesonet.agron.iastate.edu/api/1/spc/outlook.geojson"
_SPC_OUTLOOK_CAT  = "https://www.spc.noaa.gov/services/getpackage.php"

# Cache resolution for SBW fetches: only re-fetch if the archive time has
# moved by at least this many minutes.
_SBW_CACHE_MINUTES = 5

# SPC Day-1 outlook issuance times (UTC).  A new cycle is triggered when the
# archive time crosses one of these thresholds.
_OUTLOOK_CYCLES = [
    (1, 0),   # 01:00Z — Day 1 initial
    (6, 0),   # 06:00Z
    (13, 0),  # 13:00Z
    (16, 30), # 16:30Z
    (20, 0),  # 20:00Z
    (23, 0),  # 23:00Z
]


class ArchiveHazardFetcher(QObject):
    """
    Replays historical hazard data (warnings, watches, outlooks) in sync with
    the archive time controller.

    Signals
    -------
    spc_received(str, str, str, str)
        cat, wind, hail, tor GeoJSON strings — same signature as live fetcher.
    nws_received(str)
        NWS warnings GeoJSON string.
    watches_received(str)
        SPC watches GeoJSON string (filtered to currently-active watches).
    loading_changed(bool)
    error(str)
    """

    spc_received     = pyqtSignal(str, str, str, str)
    nws_received     = pyqtSignal(str)
    watches_received = pyqtSignal(str)
    loading_changed  = pyqtSignal(bool)
    error            = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = session_date

        # SBW cache: {rounded_time → geojson_str}
        self._sbw_cache: dict[datetime, str] = {}
        self._sbw_pending: set[datetime] = set()
        self._sbw_lock = threading.Lock()

        # Watches: fetched once for the day, stored as list of dicts.
        self._watches_raw: Optional[list] = None
        self._watches_loaded = False

        # Outlook cache: {cycle_time → (cat, wind, hail, tor)}
        self._outlook_cache: dict[datetime, tuple] = {}
        self._current_cycle: Optional[datetime] = None
        self._outlook_pending: set[datetime] = set()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_day_data(self) -> None:
        """Pre-fetch watches for the session date (call once at startup)."""
        threading.Thread(target=self._fetch_watches_for_day, daemon=True).start()

    def on_time_changed(self, archive_time: datetime) -> None:
        """Called by TimeController on every tick."""
        self._update_warnings(archive_time)
        self._update_watches(archive_time)
        self._update_outlook(archive_time)

    # ── NWS Warnings (SBW) ───────────────────────────────────────────────────

    def _update_warnings(self, t: datetime) -> None:
        """Emit warnings valid at the archive time; cache at 5-min resolution."""
        rounded = _round_to_minutes(t, _SBW_CACHE_MINUTES)
        cached = self._sbw_cache.get(rounded)
        if cached is not None:
            self.nws_received.emit(cached)
            return
        with self._sbw_lock:
            if rounded in self._sbw_pending:
                return
            self._sbw_pending.add(rounded)
        threading.Thread(
            target=self._fetch_sbw, args=(rounded,), daemon=True
        ).start()

    def _fetch_sbw(self, rounded_time: datetime) -> None:
        try:
            ts = rounded_time.strftime("%Y-%m-%dT%H:%M:%S")
            resp = requests.get(
                _IEM_SBW_URL,
                params={"ts": ts, "wfo": "all"},
                timeout=20,
            )
            resp.raise_for_status()
            geojson_str = resp.text
            self._sbw_cache[rounded_time] = geojson_str
            # Only emit if this is still the relevant time.
            self.nws_received.emit(geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: SBW fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._sbw_lock:
                self._sbw_pending.discard(rounded_time)

    # ── SPC Watches ───────────────────────────────────────────────────────────

    def _fetch_watches_for_day(self) -> None:
        try:
            date_str = self._date.strftime("%Y-%m-%d")
            resp = requests.get(
                _IEM_WATCHES_URL,
                params={"date": date_str},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            self._watches_raw = data.get("features", data.get("watches", []))
            self._watches_loaded = True
            log.info(
                "ArchiveHazardFetcher: loaded %d watches for %s",
                len(self._watches_raw), date_str,
            )
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: watches fetch failed: %s", exc)
            self._watches_raw = []
            self._watches_loaded = True

    def _update_watches(self, t: datetime) -> None:
        if not self._watches_loaded or self._watches_raw is None:
            return
        # Filter to watches that were active at time t.
        active = []
        for w in self._watches_raw:
            props = w.get("properties", w) if isinstance(w, dict) else {}
            issued  = _parse_iso(props.get("issued")  or props.get("issue_time"))
            expired = _parse_iso(props.get("expired") or props.get("expire_time"))
            if issued and expired and issued <= t < expired:
                active.append(w)
        if not active:
            self.watches_received.emit('{"type":"FeatureCollection","features":[]}')
            return
        geojson = {"type": "FeatureCollection", "features": active}
        self.watches_received.emit(json.dumps(geojson))

    # ── SPC Outlooks ──────────────────────────────────────────────────────────

    def _update_outlook(self, t: datetime) -> None:
        """Fetch the Day-1 outlook cycle valid at time t."""
        cycle = _current_outlook_cycle(t)
        if cycle == self._current_cycle:
            cached = self._outlook_cache.get(cycle)
            if cached:
                self.spc_received.emit(*cached)
            return
        self._current_cycle = cycle
        cached = self._outlook_cache.get(cycle)
        if cached:
            self.spc_received.emit(*cached)
            return
        if cycle in self._outlook_pending:
            return
        self._outlook_pending.add(cycle)
        threading.Thread(
            target=self._fetch_outlook, args=(cycle,), daemon=True
        ).start()

    def _fetch_outlook(self, cycle: datetime) -> None:
        try:
            valid_str = cycle.strftime("%Y%m%dT%H%M")
            # Fetch all four outlook products: categorical, wind, hail, tornado.
            results = {}
            for product in ("categorical", "wind", "hail", "tornado"):
                try:
                    resp = requests.get(
                        _IEM_OUTLOOK_URL,
                        params={"day": 1, "valid": valid_str, "product": product},
                        timeout=20,
                    )
                    resp.raise_for_status()
                    results[product] = resp.text
                except Exception as exc:
                    log.warning(
                        "ArchiveHazardFetcher: outlook %s fetch failed: %s", product, exc
                    )
                    results[product] = '{"type":"FeatureCollection","features":[]}'

            payload = (
                results.get("categorical", '{"type":"FeatureCollection","features":[]}'),
                results.get("wind",        '{"type":"FeatureCollection","features":[]}'),
                results.get("hail",        '{"type":"FeatureCollection","features":[]}'),
                results.get("tornado",     '{"type":"FeatureCollection","features":[]}'),
            )
            self._outlook_cache[cycle] = payload
            self.spc_received.emit(*payload)
        except Exception as exc:
            log.error("ArchiveHazardFetcher: outlook fetch error: %s", exc)
            self.error.emit(f"Outlook fetch error: {exc}")
        finally:
            self._outlook_pending.discard(cycle)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _round_to_minutes(dt: datetime, minutes: int) -> datetime:
    """Round dt down to the nearest N-minute boundary."""
    total_secs = int(dt.timestamp())
    step = minutes * 60
    return datetime.fromtimestamp(total_secs - (total_secs % step), tz=timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None


def _current_outlook_cycle(t: datetime) -> datetime:
    """Return the most recent SPC outlook issuance time before t."""
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    candidate = midnight
    for (h, m) in _OUTLOOK_CYCLES:
        cycle = midnight.replace(hour=h, minute=m)
        if cycle <= t:
            candidate = cycle
    return candidate
