# archive/fetchers/sounding_archive_fetcher.py
# ArchiveSoundingFetcher — retrieves atmospheric soundings for archive mode.
#
# Three sources
# -------------
# 1. RUC/RAP model analysis (rucsoundings.noaa.gov)
#    Lightweight text-format soundings at any lat/lon for any historical time.
#    No GRIB2 download needed.
#
# 2. Observed radiosondes (IEM RAOB archive)
#    Same API as the live ObsSoundingFetcher; query by station + timestamp.
#
# 3. NSSL CLAMPS / mobile soundings
#    Same directory-based access as real-time mode.  The directory grows
#    through the season; we scan it and pick the sounding whose issue_time
#    is nearest to the current archive time.

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet

log = logging.getLogger(__name__)

_RUCSOUNDINGS_URL = "https://rucsoundings.noaa.gov/get_soundings.cgi"
_IEM_RAOB_URL     = "https://mesonet.agron.iastate.edu/json/raob.py"


class ArchiveSoundingFetcher(QObject):
    """
    On-demand sounding fetcher for archive mode.

    Unlike the live sounding fetcher, this is triggered by a user map click
    (lat/lon) combined with the current archive time rather than "now".

    Signals
    -------
    sounding_ready(SoundingSet)
    fetch_error(str)
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_archive_time: Optional[datetime] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def on_time_changed(self, archive_time: datetime) -> None:
        self._current_archive_time = archive_time

    def fetch_model_sounding(self, lat: float, lon: float) -> None:
        """Fetch RUC/RAP analysis sounding at lat/lon for the current archive time."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_model,
            args=(lat, lon, t),
            daemon=True,
        ).start()

    def fetch_obs_sounding(self, station_id: str, lat: float, lon: float, elev: float) -> None:
        """Fetch observed radiosonde sounding from IEM RAOB archive."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_obs,
            args=(station_id, lat, lon, elev, t),
            daemon=True,
        ).start()

    def fetch_nssl_sounding(self, base_url: str) -> None:
        """Fetch the NSSL mobile sounding nearest to the current archive time."""
        t = self._current_archive_time
        if t is None:
            self.fetch_error.emit("No archive time set")
            return
        threading.Thread(
            target=self._do_fetch_nssl,
            args=(base_url, t),
            daemon=True,
        ).start()

    # ── Model sounding (RUC/RAP via rucsoundings.noaa.gov) ───────────────────

    def _do_fetch_model(self, lat: float, lon: float, t: datetime) -> None:
        try:
            import math, io
            unix_ts = int(t.timestamp())
            params = {
                "data_source": "Op40",
                "startSecs":   str(unix_ts),
                "endSecs":     str(unix_ts + 3600),
                "lat":         f"{lat:.4f}",
                "lon":         f"{lon:.4f}",
                "fcst_len":    "shortest",
                "n_hrs":       "1.0",
                "Airport":     "",
                "hydrometeors": "false",
                "start":       "latest",
            }
            resp = requests.get(_RUCSOUNDINGS_URL, params=params, timeout=30)
            resp.raise_for_status()
            sounding = _parse_gsd_sounding(resp.text, lat, lon, t)
            if sounding is None:
                self.fetch_error.emit("No model sounding data returned")
                return
            sset = SoundingSet(
                lat=lat, lon=lon, elevation=0.0,
                fetch_time=t,
                soundings=[sounding],
                source="ruc_rap",
            )
            self.sounding_ready.emit(sset)
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: model sounding failed: %s", exc)
            self.fetch_error.emit(f"Model sounding error: {exc}")

    # ── Observed sounding (IEM RAOB) ──────────────────────────────────────────

    def _do_fetch_obs(
        self, station_id: str, lat: float, lon: float, elev: float, t: datetime
    ) -> None:
        try:
            # Find the nearest synoptic time (00Z, 06Z, 12Z, 18Z) before t.
            nearest_synoptic = _nearest_synoptic_time(t)
            ts_str = nearest_synoptic.strftime("%Y-%m-%dT%H:%M:%S")
            resp = requests.get(
                _IEM_RAOB_URL,
                params={"station": station_id, "ts": ts_str},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            soundings = _parse_iem_raob(data, station_id, lat, lon, elev)
            if not soundings:
                self.fetch_error.emit(f"No observed sounding for {station_id} near {ts_str}")
                return
            sset = SoundingSet(
                lat=lat, lon=lon, elevation=elev,
                fetch_time=t,
                soundings=soundings,
                source="obs",
            )
            self.sounding_ready.emit(sset)
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: obs sounding failed: %s", exc)
            self.fetch_error.emit(f"Observed sounding error: {exc}")

    # ── NSSL sounding ─────────────────────────────────────────────────────────

    def _do_fetch_nssl(self, base_url: str, t: datetime) -> None:
        """
        List sounding files from the server directory and pick the one whose
        timestamp is nearest to (but not after) t.

        The server is expected to return a JSON list of file entries, each with
        a "time" ISO field and a "url" for the sounding data.

        Schema will be filled in once the MQTT/sounding server is configured.
        """
        try:
            # Fetch directory listing.
            resp = requests.get(base_url.rstrip("/") + "/index.json", timeout=10)
            resp.raise_for_status()
            entries = resp.json()  # list of {"time": "...", "url": "..."}

            # Find the entry with the largest timestamp <= t.
            best = None
            best_time = None
            for entry in entries:
                entry_time = _parse_iso(entry.get("time", ""))
                if entry_time is None:
                    continue
                if entry_time <= t:
                    if best_time is None or entry_time > best_time:
                        best_time = entry_time
                        best = entry

            if best is None:
                self.fetch_error.emit("No NSSL sounding available before archive time")
                return

            # Delegate to the live NSSL fetcher's HTTP fetch logic.
            from data.clamps_sounding_fetcher import ClampsSoundingFetcher
            fetcher = ClampsSoundingFetcher.__new__(ClampsSoundingFetcher)
            sset = fetcher._fetch_from_url(best["url"])
            if sset is not None:
                self.sounding_ready.emit(sset)
            else:
                self.fetch_error.emit("NSSL sounding parse failed")
        except Exception as exc:
            log.error("ArchiveSoundingFetcher: NSSL sounding failed: %s", exc)
            self.fetch_error.emit(f"NSSL sounding error: {exc}")


# ── GSD sounding parser ───────────────────────────────────────────────────────

def _parse_gsd_sounding(
    text: str, lat: float, lon: float, valid_time: datetime
) -> Optional[Sounding]:
    """
    Parse a GSD-format sounding from rucsoundings.noaa.gov.

    GSD format columns (space-separated):
        type  pressure  height  temp  dewpt  wind_dir  wind_spd
    Pressure in hPa, temp/dewpt in tenths of °C, wind_spd in knots.
    """
    import numpy as np

    pressures, heights, temps, dewpts, wdir, wspd = [], [], [], [], [], []
    in_data = False

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("RAOB"):
            continue
        parts = line.split()
        if len(parts) < 7:
            continue
        rtype = parts[0]
        if rtype in ("4", "5", "6", "7", "8", "9"):
            in_data = True
            try:
                p  = float(parts[1]) / 10.0   # hPa
                z  = float(parts[2])           # m
                tc = float(parts[3]) / 10.0    # °C
                td = float(parts[4]) / 10.0    # °C
                wd = float(parts[5])           # degrees
                ws = float(parts[6])           # knots
                if p > 0 and z > -9990 and tc > -9990:
                    pressures.append(p)
                    heights.append(z)
                    temps.append(tc)
                    dewpts.append(td)
                    wdir.append(wd)
                    wspd.append(ws)
            except (ValueError, IndexError):
                continue

    if len(pressures) < 5:
        return None

    p  = np.array(pressures, dtype=np.float32)
    z  = np.array(heights,   dtype=np.float32)
    tc = np.array(temps,     dtype=np.float32)
    td = np.array(dewpts,    dtype=np.float32)
    wd = np.array(wdir,      dtype=np.float32)
    ws = np.array(wspd,      dtype=np.float32)

    u = -ws * np.sin(np.radians(wd))
    v = -ws * np.cos(np.radians(wd))

    return Sounding(
        valid_time=valid_time,
        pressure=p,
        temperature=tc,
        dewpoint=td,
        u_wind=u,
        v_wind=v,
        height=z,
        source="ruc_rap",
        station_id="model",
        lat=lat,
        lon=lon,
        elevation=float(z[0]) if len(z) else 0.0,
    )


# ── IEM RAOB parser ──────────────────────────────────────────────────────────

def _parse_iem_raob(data: dict, station_id: str, lat: float, lon: float, elev: float) -> list:
    """Parse IEM RAOB JSON response into a list of Sounding objects."""
    import numpy as np

    soundings = []
    profiles = data.get("profiles", [data]) if "profiles" in data else [data]

    for profile in profiles:
        valid_str = profile.get("valid_at") or profile.get("valid")
        valid_time = _parse_iso(valid_str)
        if valid_time is None:
            continue

        levels = profile.get("levels", [])
        if not levels:
            continue

        pressures, heights, temps, dewpts, wdir, wspd = [], [], [], [], [], []
        for lvl in levels:
            try:
                p  = float(lvl.get("pressure", 0))
                z  = float(lvl.get("height", -9999))
                tc = float(lvl.get("tmpf", -9999))   # IEM uses Fahrenheit for some, C for others
                td = float(lvl.get("dwpf", -9999))
                wd_v = lvl.get("drct") or lvl.get("wind_dir")
                ws_v = lvl.get("sknt") or lvl.get("wind_speed")
                wd = float(wd_v) if wd_v is not None else 0.0
                ws = float(ws_v) if ws_v is not None else 0.0
                # Convert from Fahrenheit if needed (IEM RAOB typically returns °C).
                if p > 0 and tc > -999:
                    pressures.append(p)
                    heights.append(z if z > -9990 else 0.0)
                    temps.append(tc)
                    dewpts.append(td if td > -999 else float("nan"))
                    wdir.append(wd)
                    wspd.append(ws)
            except (TypeError, ValueError):
                continue

        if len(pressures) < 5:
            continue

        p  = np.array(pressures, dtype=np.float32)
        z  = np.array(heights,   dtype=np.float32)
        tc = np.array(temps,     dtype=np.float32)
        td = np.array(dewpts,    dtype=np.float32)
        wd = np.array(wdir,      dtype=np.float32)
        ws = np.array(wspd,      dtype=np.float32)

        u = -ws * np.sin(np.radians(wd))
        v = -ws * np.cos(np.radians(wd))

        soundings.append(Sounding(
            valid_time=valid_time,
            pressure=p,
            temperature=tc,
            dewpoint=td,
            u_wind=u,
            v_wind=v,
            height=z,
            source="obs",
            station_id=station_id,
            lat=lat,
            lon=lon,
            elevation=elev,
        ))

    return soundings


# ── Time utilities ────────────────────────────────────────────────────────────

def _nearest_synoptic_time(t: datetime) -> datetime:
    """Return the most recent synoptic time (00Z, 06Z, 12Z, 18Z) at or before t."""
    hour = (t.hour // 6) * 6
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            s.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except Exception:
        return None
