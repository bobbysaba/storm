
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from archive.fetchers import spc_shapefiles
from core import package_sources
from core.qt_signals import emit_alive
from data import endpoints
from data.fetchers.hazard_fetcher import (
    _spc_cat_key,
    _spc_prob_label,
    _nws_color_for_phenom,
)

log = logging.getLogger(__name__)

_IEM_SBW_URL   = endpoints.IEM_STORM_BASED_WARNINGS
# these endpoints require a 12-digit YYYYMMDDHHmm timestamp via ?ts=
_IEM_WATCH_URL = endpoints.IEM_SPC_WATCHES
# iem GIS shapefile endpoint for MCDs — returns a zip with .shp/.dbf/.prj
_IEM_MCD_GIS_URL = endpoints.IEM_SPC_MD_GIS

# spc direct archive — same GeoJSON schema as the live endpoint (LABEL, DN, etc.).
_SPC_OUTLOOK_ARCHIVE = endpoints.SPC_OUTLOOK_ARCHIVE
_SPC_EXTENDED_OUTLOOK_ARCHIVE = endpoints.SPC_EXTENDED_OUTLOOK_ARCHIVE

# SPC only started publishing day1/2 outlooks as .lyr.geojson on 2020-01-01;
# before that the archive only has the shapefile zip (day{N}otlk_..._-shp.zip,
# one zip per cycle bundling every product's .shp/.dbf together, unlike the
# geojson path which is one file per product). Confirmed by HEAD request:
# .../2019/day1otlk_20190608_1300_wind.lyr.geojson -> 404
# .../2019/day1otlk_20190608_1300-shp.zip -> 200
# spc_shapefiles.py reads those zips.

# cache resolution for time-varying fetches.
_CACHE_MINUTES = 5

_OUTLOOK_CYCLES = [
    (1, 0, 1, 0),     # 01Z issuance → 0100 archive file
    (6, 0, 12, 0),    # 06Z issuance → 1200 valid-time archive file
    (13, 0, 13, 0),
    (16, 30, 16, 30),
    (20, 0, 20, 0),
]

_DAY_OUTLOOK_CYCLES: dict[int, list[tuple[int, int, int, int]]] = {
    1: _OUTLOOK_CYCLES,
    2: [(6, 0, 7, 0), (17, 0, 17, 30)],
    3: [(7, 30, 8, 30), (19, 30, 19, 30)],
    4: [(9, 0, 0, 0)],
    5: [(9, 0, 0, 0)],
    6: [(9, 0, 0, 0)],
    7: [(9, 0, 0, 0)],
    8: [(9, 0, 0, 0)],
}

_EMPTY_FC = '{"type":"FeatureCollection","features":[]}'


class ArchiveHazardFetcher(QObject):
    """
    Replays historical hazard data (warnings, watches, MDs, outlooks) in sync
    with the archive time controller.

    Signals
    -------
    spc_received(str, str, str, str, str, str)
        cat, wind, hail, tor, prob, sig GeoJSON strings — same signature as live fetcher.
    nws_received(str)
        NWS warnings GeoJSON string.
    watches_received(str)
        SPC watches GeoJSON string.
    spc_mds_received(str)
        SPC mesoscale discussions GeoJSON string.
    loading_changed(bool)
    error(str)
    """

    spc_received     = pyqtSignal(str, str, str, str, str, str)
    nws_received     = pyqtSignal(str)
    watches_received = pyqtSignal(str)
    spc_mds_received = pyqtSignal(str)
    loading_changed  = pyqtSignal(bool)
    error            = pyqtSignal(str)

    def __init__(self, session_date: datetime, parent=None):
        super().__init__(parent)
        self._date = session_date

        # per-type caches: {rounded_time → geojson_str}
        self._sbw_cache:   dict[datetime, str] = {}
        self._watch_cache: dict[datetime, str] = {}
        self._mcd_cache:   dict[datetime, str] = {}

        # per-type in-flight sets + locks
        self._sbw_pending:   set[datetime] = set()
        self._watch_pending: set[datetime] = set()
        self._mcd_pending:   set[datetime] = set()
        self._sbw_lock   = threading.Lock()
        self._watch_lock = threading.Lock()
        self._mcd_lock   = threading.Lock()

        # outlook cache: {(day, cycle_time) → (cat, wind, hail, tor, prob, sig)}
        self._outlook_cache: dict[tuple[int, datetime], tuple[str, str, str, str, str, str]] = {}
        self._current_cycle: Optional[tuple[int, datetime]] = None
        self._outlook_pending: set[tuple[int, datetime]] = set()
        self._current_archive_time: Optional[datetime] = None
        self._spc_day = 1

        # immediately ready — no pre-fetch step required.
        self._watches_loaded = True


    def load_day_data(self) -> None:
        """No-op: all data is now fetched on demand. _watches_loaded stays True."""
        pass

    def on_time_changed(self, archive_time: datetime) -> None:
        """Called by TimeController on every tick."""
        self._current_archive_time = archive_time
        self._update_warnings(archive_time)
        self._update_watches(archive_time)
        self._update_mds(archive_time)
        self._update_outlook(archive_time)

    def refresh_now(self) -> None:
        """Re-emit or fetch hazard data for the current archive time."""
        if self._current_archive_time is None:
            return
        self._current_cycle = None
        self.on_time_changed(self._current_archive_time)

    def set_spc_day(self, day: int) -> None:
        self._spc_day = max(1, min(8, int(day or 1)))
        self._current_cycle = None


    def _update_warnings(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._sbw_cache.get(rounded)
        if cached is not None:
            self.nws_received.emit(cached)
            return
        with self._sbw_lock:
            if rounded in self._sbw_pending:
                return
            self._sbw_pending.add(rounded)
        threading.Thread(target=self._fetch_sbw, args=(rounded,), daemon=True).start()

    def _fetch_sbw(self, rounded_time: datetime) -> None:
        try:
            ts = rounded_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            resp = package_sources.requests_get("hazards", _IEM_SBW_URL, params={"ts": ts}, timeout=20)
            resp.raise_for_status()
            geojson_str = _normalize_sbw_geojson(resp.text)
            self._sbw_cache[rounded_time] = geojson_str
            emit_alive(self, "nws_received", geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: SBW fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._sbw_lock:
                self._sbw_pending.discard(rounded_time)


    def _update_watches(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._watch_cache.get(rounded)
        if cached is not None:
            self.watches_received.emit(cached)
            return
        with self._watch_lock:
            if rounded in self._watch_pending:
                return
            self._watch_pending.add(rounded)
        threading.Thread(target=self._fetch_watches, args=(rounded,), daemon=True).start()

    def _fetch_watches(self, rounded_time: datetime) -> None:
        try:
            ts = rounded_time.strftime("%Y%m%d%H%M")   # spcwatch.py requires YYYYMMDDHHmm
            resp = package_sources.requests_get("hazards", _IEM_WATCH_URL, params={"ts": ts}, timeout=20)
            resp.raise_for_status()
            geojson_str = _normalize_watch_geojson(resp.text)
            self._watch_cache[rounded_time] = geojson_str
            emit_alive(self, "watches_received", geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: Watch fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._watch_lock:
                self._watch_pending.discard(rounded_time)


    def _update_mds(self, t: datetime) -> None:
        rounded = _round_to_minutes(t, _CACHE_MINUTES)
        cached = self._mcd_cache.get(rounded)
        if cached is not None:
            self.spc_mds_received.emit(cached)
            return
        with self._mcd_lock:
            if rounded in self._mcd_pending:
                return
            self._mcd_pending.add(rounded)
        threading.Thread(target=self._fetch_mds, args=(rounded,), daemon=True).start()

    def _fetch_mds(self, rounded_time: datetime) -> None:
        try:
            # use a ±2-hour window to catch MCDs that started before rounded_time
            t_start = rounded_time - timedelta(hours=2)
            t_end   = rounded_time + timedelta(hours=2)
            params = {
                "year1":   t_start.year,
                "month1":  t_start.month,
                "day1":    t_start.day,
                "hour1":   t_start.hour,
                "minute1": t_start.minute,
                "year2":   t_end.year,
                "month2":  t_end.month,
                "day2":    t_end.day,
                "hour2":   t_end.hour,
                "minute2": t_end.minute,
            }
            resp = package_sources.requests_get("hazards", _IEM_MCD_GIS_URL, params=params, timeout=30)
            resp.raise_for_status()
            if not resp.content.startswith(b"PK"):
                # IEM answers a burst of requests with a short non-zip reply; that
                # isn't "no MDs", so don't cache it -- the next clock change asks again
                log.info("ArchiveHazardFetcher: MCD reply for %s wasn't a zip (%r); will retry",
                         rounded_time, resp.content[:80])
                return

            features = spc_shapefiles.parse_mcd_zip(resp.content, rounded_time)
            geojson_str = _normalize_mcd_geojson(
                json.dumps({"type": "FeatureCollection", "features": features})
            )
            self._mcd_cache[rounded_time] = geojson_str
            emit_alive(self, "spc_mds_received", geojson_str)
        except Exception as exc:
            log.warning("ArchiveHazardFetcher: MCD fetch failed for %s: %s", rounded_time, exc)
        finally:
            with self._mcd_lock:
                self._mcd_pending.discard(rounded_time)


    def _update_outlook(self, t: datetime) -> None:
        """Fetch the selected SPC outlook day/cycle valid at time t."""
        day = self._spc_day
        cycle = _current_outlook_cycle(t, day)
        cache_key = (day, cycle)
        if cache_key == self._current_cycle:
            cached = self._outlook_cache.get(cache_key)
            if cached:
                self.spc_received.emit(*cached)
            return
        self._current_cycle = cache_key
        cached = self._outlook_cache.get(cache_key)
        if cached:
            self.spc_received.emit(*cached)
            return
        if cache_key in self._outlook_pending:
            return
        self._outlook_pending.add(cache_key)
        threading.Thread(target=self._fetch_outlook, args=(day, cycle), daemon=True).start()

    def _fetch_outlook(self, day: int, cycle: datetime) -> None:
        try:
            results: dict[str, str] = {}
            candidates = _outlook_cycle_candidates(cycle, day)
            products = _archive_product_suffixes(day)
            sig_products = _archive_sig_suffixes(day)
            any_found = False

            for key, suffix in products.items():
                found = False
                for ct in candidates:
                    url = _archive_spc_url(day, ct, suffix)
                    try:
                        resp = package_sources.requests_get("hazards", url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                        results[key] = _normalize_archive_spc_geojson(
                            key,
                            resp.text,
                            day=day,
                            product=key,
                        )
                        found = True
                        log.debug(
                            "ArchiveHazardFetcher: day %s outlook %s found at %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                        )
                        break
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook %s @ %s failed: %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                            exc,
                        )
                if not found:
                    results[key] = _EMPTY_FC
                any_found = any_found or found

            for key, suffix in sig_products.items():
                base_key = key if key in ("prob", "sig") else key
                found = False
                for ct in candidates:
                    url = _archive_spc_url(day, ct, suffix)
                    try:
                        resp = package_sources.requests_get("hazards", url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                        sig_geojson = _normalize_archive_spc_geojson(
                            "significant",
                            resp.text,
                            day=day,
                            product=base_key,
                            force_label="SIGN",
                        )
                        if key == "sig":
                            results["sig"] = sig_geojson
                        else:
                            results[key] = _merge_significant_geojson(
                                results.get(key, _EMPTY_FC),
                                sig_geojson,
                            )
                        found = True
                        break
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook %s significant @ %s failed: %s",
                            day,
                            key,
                            ct.strftime("%Y%m%d_%H%M"),
                            exc,
                        )
                any_found = any_found or found

            # SPC only started publishing day1/2 outlooks as .lyr.geojson on
            # 2020-01-01 -- every candidate above 404s for anything older,
            # silently (a 404 is treated as "try the next candidate", not an
            # error), which is why this could fail with no warning logged at
            # all. Older dates are only in the archive as one shapefile zip
            # per cycle bundling every product together; try it once, across
            # the same candidate cycles, only once the geojson path has
            # genuinely found nothing (2020+ dates always succeed above and
            # never reach this).
            if not any_found and day in (1, 2):
                for ct in candidates:
                    zip_url = _archive_spc_shapefile_zip_url(day, ct)
                    try:
                        resp = package_sources.requests_get("hazards", zip_url, timeout=20)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                    except Exception as exc:
                        log.warning(
                            "ArchiveHazardFetcher: day %s outlook shapefile zip @ %s failed: %s",
                            day, ct.strftime("%Y%m%d_%H%M"), exc,
                        )
                        continue
                    by_suffix = spc_shapefiles.parse_outlook_zip(resp.content)
                    if not by_suffix:
                        continue
                    log.info(
                        "ArchiveHazardFetcher: day %s outlook found via pre-2020 shapefile archive at %s",
                        day, ct.strftime("%Y%m%d_%H%M"),
                    )
                    # Route through the same normalization the geojson path
                    # uses (_normalize_archive_spc_geojson) rather than
                    # serializing the raw shapefile records directly -- the
                    # frontend colors categorical fills off props["cat"]
                    # and probabilistic fills off props["LABEL"] (a percent
                    # string like "5"/"15"/"30"), neither of which the raw
                    # DBF fields carry as-is (only DN, and LABEL for cat).
                    # Without this, every shapefile-sourced feature rendered
                    # with its match expression's fallback color instead of
                    # its real risk level -- confirmed live against a real
                    # case (2010-05-10) where this fallback path is the only
                    # way outlook data is found at all.
                    for key, suffix in products.items():
                        features = by_suffix.get(suffix, [])
                        raw_geojson = json.dumps({"type": "FeatureCollection", "features": features})
                        results[key] = _normalize_archive_spc_geojson(
                            key, raw_geojson, day=day, product=key,
                        )
                    for key, suffix in sig_products.items():
                        base_key = key if key in ("prob", "sig") else key
                        sig_features = by_suffix.get(suffix, [])
                        if not sig_features:
                            continue
                        raw_sig_geojson = json.dumps({"type": "FeatureCollection", "features": sig_features})
                        sig_geojson = _normalize_archive_spc_geojson(
                            "significant", raw_sig_geojson, day=day, product=base_key, force_label="SIGN",
                        )
                        if key == "sig":
                            results["sig"] = sig_geojson
                        else:
                            results[key] = _merge_significant_geojson(
                                results.get(key, _EMPTY_FC),
                                sig_geojson,
                            )
                    break
                else:
                    log.warning("ArchiveHazardFetcher: day %s outlook not found for any candidate cycle (geojson or shapefile)", day)

            payload = (
                results.get("categorical", _EMPTY_FC),
                results.get("wind",        _EMPTY_FC),
                results.get("hail",        _EMPTY_FC),
                results.get("tornado",     _EMPTY_FC),
                results.get("prob",        _EMPTY_FC),
                results.get("sig",         _EMPTY_FC),
            )
            self._outlook_cache[(day, cycle)] = payload
            emit_alive(self, "spc_received", *payload)
        except Exception as exc:
            log.error("ArchiveHazardFetcher: outlook fetch error: %s", exc)
            emit_alive(self, "error", f"Outlook fetch error: {exc}")
        finally:
            self._outlook_pending.discard((day, cycle))



def _archive_spc_shapefile_zip_url(day: int, cycle: datetime) -> str:
    """Pre-2020 day1/2 outlook archive URL -- one zip per cycle, bundling
    every product's .shp/.dbf/.prj together (unlike the per-product
    .lyr.geojson files _archive_spc_url builds for 2020+)."""
    return (
        f"{_SPC_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
        f"day{day}otlk_{cycle.strftime('%Y%m%d')}_{cycle.strftime('%H%M')}-shp.zip"
    )


def _normalize_sbw_geojson(raw_text: str) -> str:
    """Normalize IEM SBW GeoJSON to match the live WWA MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem uses 'phenomena' or 'phenomenon'; live mode uses 'phenom'
        phenom = str(
            props.get("phenomena") or props.get("phenomenon") or props.get("phenom", "")
        ).upper()
        props["phenom"]    = phenom
        props["prod_type"] = str(
            props.get("type_") or props.get("ps") or props.get("prod_type") or phenom
        )
        props["nws_color"]   = _nws_color_for_phenom(phenom)
        props["wfo"]         = str(props.get("wfo", "")).upper()
        props["event"]       = str(props.get("eventid") or props.get("event", ""))
        props["warning_url"] = str(props.get("href") or props.get("warning_url", ""))
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_watch_geojson(raw_text: str) -> str:
    """Normalize IEM spc_watch.geojson to match the live WWA MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem spc_watch: 'type' is TOR/SVR, 'num' is the watch number
        watch_type = str(props.get("type") or props.get("phenomena") or "").upper()
        num_raw    = props.get("num") or props.get("number") or props.get("event", "")
        try:
            num_str = str(int(num_raw)).zfill(4)
        except (TypeError, ValueError):
            num_str = str(num_raw)
        is_tor = watch_type in ("TOR", "TO")
        props["watch_num"]   = num_str
        props["watch_color"] = "#FF0000" if is_tor else "#4169E1"
        props["event"]       = "Tornado Watch" if is_tor else "Severe Thunderstorm Watch"
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_mcd_geojson(raw_text: str) -> str:
    """Normalize IEM spc_mcd.geojson to match the live SPC MapServer property schema."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    feats = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom  = feature.get("geometry")
        if not geom:
            continue
        # iem spc_mcd: 'num' is the MD number
        num_raw = props.get("num") or props.get("number", "")
        try:
            name = f"MD {int(num_raw):04d}"
        except (TypeError, ValueError):
            name = str(num_raw) or "MD"
        if name.strip().upper() in ("MD", "MD 0000"):
            continue
        props["name"] = name
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def _normalize_archive_spc_geojson(
    kind: str,
    raw_text: str,
    *,
    day: int | None = None,
    product: str | None = None,
    force_label: str | None = None,
) -> str:
    """Normalize archive SPC GeoJSON to the same property schema live mode uses."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text

    features = []
    for feature in payload.get("features", []):
        props = dict(feature.get("properties") or {})
        geom = feature.get("geometry")
        if not geom:
            continue
        if geom.get("type") == "GeometryCollection" and not geom.get("geometries"):
            continue
        if kind == "categorical":
            cat = _spc_cat_key(props)
            if not cat:
                continue
            props["cat"] = cat
        else:
            label = force_label or _spc_prob_label(props)
            if label is not None:
                props["LABEL"] = label
        if day is not None:
            props["spc_day"] = day
        if product:
            props["spc_product"] = product
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props,
        })

    return json.dumps({"type": "FeatureCollection", "features": features})


def _merge_significant_geojson(base_text: str, sig_text: str) -> str:
    """Merge significant polygons into a probabilistic product collection."""
    try:
        base = json.loads(base_text or _EMPTY_FC)
        sig = json.loads(sig_text or _EMPTY_FC)
    except json.JSONDecodeError:
        return base_text

    base_features = [
        f for f in (base.get("features") or [])
        if str((f.get("properties") or {}).get("LABEL", "")).upper() != "SIGN"
    ]
    base_features.extend(sig.get("features") or [])
    return json.dumps({"type": "FeatureCollection", "features": base_features})


def _archive_product_suffixes(day: int) -> dict[str, str]:
    if day in (1, 2):
        return {
            "categorical": "cat",
            "wind": "wind",
            "hail": "hail",
            "tornado": "torn",
        }
    if day == 3:
        return {
            "categorical": "cat",
            "prob": "prob",
        }
    if 4 <= day <= 8:
        return {"prob": f"day{day}prob"}
    return {"categorical": "cat"}


def _archive_sig_suffixes(day: int) -> dict[str, str]:
    if day in (1, 2):
        return {
            "wind": "sigwind",
            "hail": "sighail",
            "tornado": "sigtorn",
        }
    if day == 3:
        return {
            "prob": "sigprob",
            "sig": "sigprob",
        }
    return {}


def _archive_spc_url(day: int, cycle: datetime, suffix: str) -> str:
    if 4 <= day <= 8:
        return (
            f"{_SPC_EXTENDED_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
            f"{suffix}_{cycle.strftime('%Y%m%d')}.lyr.geojson"
        )
    return (
        f"{_SPC_OUTLOOK_ARCHIVE}/{cycle.strftime('%Y')}/"
        f"day{day}otlk_{cycle.strftime('%Y%m%d')}_{cycle.strftime('%H%M')}_{suffix}.lyr.geojson"
    )



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


def _outlook_cycle_candidates(cycle: datetime, day: int) -> list[datetime]:
    """Return archive filename cycles to try, newest first."""
    if 4 <= day <= 8:
        return [cycle, cycle - timedelta(days=1)]

    cycles = _DAY_OUTLOOK_CYCLES.get(day, _DAY_OUTLOOK_CYCLES[1])
    candidates: list[datetime] = [cycle]
    midnight = cycle.replace(hour=0, minute=0, second=0, microsecond=0)

    for h_eff, m_eff, h_file, m_file in sorted(cycles, reverse=True):
        _ = (h_eff, m_eff)
        ct = midnight.replace(hour=h_file, minute=m_file)
        if ct < cycle and ct not in candidates:
            candidates.append(ct)

    prev_midnight = midnight - timedelta(days=1)
    for _h_eff, _m_eff, h_file, m_file in sorted(cycles, reverse=True):
        candidates.append(prev_midnight.replace(hour=h_file, minute=m_file))
    return candidates


def _current_outlook_cycle(t: datetime, day: int = 1) -> datetime:
    """Return the archive file cycle for the selected SPC day valid at time t."""
    day = max(1, min(8, int(day or 1)))
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    cycles = _DAY_OUTLOOK_CYCLES.get(day, _DAY_OUTLOOK_CYCLES[1])

    if 4 <= day <= 8:
        issue_time = midnight.replace(hour=cycles[0][0], minute=cycles[0][1])
        return midnight if t >= issue_time else midnight - timedelta(days=1)

    candidate: datetime | None = None
    for h_eff, m_eff, h_file, m_file in cycles:
        effective = midnight.replace(hour=h_eff, minute=m_eff)
        if effective <= t:
            candidate = midnight.replace(hour=h_file, minute=m_file)
    if candidate is None:
        prev_midnight = midnight - timedelta(days=1)
        _h_eff, _m_eff, h_file, m_file = cycles[-1]
        candidate = prev_midnight.replace(hour=h_file, minute=m_file)
    return candidate
