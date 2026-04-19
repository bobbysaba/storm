
import logging
import math
import re
import ssl
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# nssl's THREDDS server uses a cert that Python's default SSL context rejects.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from core.sounding import Sounding, SoundingSet

log = logging.getLogger(__name__)

_CATALOG_XML = (
    "https://data.nssl.noaa.gov/thredds/catalog/"
    "FRDD/CLAMPS/dltruck/dltruck1/ingested/dltruckdlsonderawDL1.b1/catalog.xml"
)
_CATALOG_HTML = (
    "https://data.nssl.noaa.gov/thredds/catalog/"
    "FRDD/CLAMPS/dltruck/dltruck1/ingested/dltruckdlsonderawDL1.b1/catalog.html"
)
_FILESERVER_BASE = "https://data.nssl.noaa.gov/thredds/fileServer/"
_THREDDS_NS      = "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"
_REQUEST_TIMEOUT = 20
_LOOKBACK_HOURS  = 12

_TEST_FILE_URL = None

_FILENAME_RE = re.compile(r"upperair\.NSSL_Lidar_sonde\.(\d{12})\.skewT", re.IGNORECASE)

_HEADERS = {"User-Agent": "Mozilla/5.0 STORM/1.0"}


class ClampsSoundingFetcher(QObject):
    """Fire-and-forget fetcher for NSSL CLAMPS DL Truck soundings.

    Signals:
        sounding_ready(SoundingSet)  — emitted on main thread when data arrives
        fetch_error(str)             — emitted on recoverable errors
    """

    sounding_ready = pyqtSignal(object)   # SoundingSet
    fetch_error    = pyqtSignal(str)

    def fetch(self):
        """Start background fetch of last 12 hours of CLAMPS soundings."""
        threading.Thread(target=self._bg_fetch, daemon=True).start()
        log.debug("CLAMPS sounding fetch started")


    def _bg_fetch(self):
        try:
            sset = _fetch_sounding_set()
            self.sounding_ready.emit(sset)
        except (HTTPError, URLError, TimeoutError) as e:
            msg = f"CLAMPS fetch failed: {e}"
            log.warning(msg)
            self.fetch_error.emit(msg)
        except Exception as e:
            msg = f"CLAMPS fetch error: {e}"
            log.exception(msg)
            self.fetch_error.emit(msg)



def _fetch_sounding_set() -> SoundingSet:
    now_utc = datetime.now(timezone.utc)

    if _TEST_FILE_URL:
        m = _FILENAME_RE.search(_TEST_FILE_URL)
        file_time = (
            datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            if m else now_utc
        )
        req = Request(_TEST_FILE_URL, headers=_HEADERS)
        with urlopen(req, timeout=_REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        snd = _parse_skewt(text, file_time, 0)
        if snd is None:
            raise ValueError("Failed to parse test CLAMPS file")
        snd.slot_offset = 0
        snd.label       = _format_label(file_time)
        soundings       = [snd]
        surface_elev    = float(snd.height[0]) if snd.height.size > 0 else 0.0
        return SoundingSet(
            lat=0.0, lon=0.0, elevation=surface_elev, fetch_time=now_utc,
            soundings=soundings, station_id="CLAMPS",
            station_name="NSSL CLAMPS DL Truck", source="nssl",
        )

    cutoff = now_utc - timedelta(hours=_LOOKBACK_HOURS)

    req = Request(_CATALOG_XML, headers=_HEADERS)
    with urlopen(req, timeout=_REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
        xml_bytes = resp.read()

    root = ET.fromstring(xml_bytes)

    candidates: list[tuple[datetime, str]] = []
    for ds in root.iter(f"{{{_THREDDS_NS}}}dataset"):
        name = ds.get("name", "")
        m    = _FILENAME_RE.search(name)
        if not m:
            continue
        try:
            file_time = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if file_time < cutoff or file_time > now_utc:
            continue
        url_path = ds.get("urlPath") or ds.get("ID")
        if url_path:
            candidates.append((file_time, url_path))

    if not candidates:
        raise ValueError("No CLAMPS soundings found in the last 12 hours")

    candidates.sort(key=lambda x: x[0])

    soundings: list[Sounding] = []
    for idx, (file_time, url_path) in enumerate(candidates):
        try:
            snd = _fetch_and_parse(url_path, file_time, idx)
            if snd is not None:
                soundings.append(snd)
        except Exception as e:
            log.warning("Failed to fetch CLAMPS file %s: %s", url_path, e)

    if not soundings:
        raise ValueError("No valid CLAMPS soundings could be parsed")

    # reassign slot_offset and labels after filtering
    for i, snd in enumerate(soundings):
        snd.slot_offset = i
        snd.label       = _format_label(snd.valid_time)

    surface_elev = float(soundings[0].height[0]) if soundings[0].height.size > 0 else 0.0

    return SoundingSet(
        lat          = 0.0,
        lon          = 0.0,
        elevation    = surface_elev,
        fetch_time   = now_utc,
        soundings    = soundings,
        station_id   = "CLAMPS",
        station_name = "NSSL CLAMPS DL Truck",
        source       = "nssl",
    )


def _fetch_and_parse(url_path: str, file_time: datetime, idx: int) -> "Sounding | None":
    url = _FILESERVER_BASE + url_path
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=_REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return _parse_skewt(text, file_time, idx)


def _parse_skewt(text: str, file_time: datetime, idx: int) -> "Sounding | None":
    """Parse a .skewT text file into a Sounding.

    Columns: pressure(hPa), height(m MSL), temp(°C), dewpoint(°C),
             wind_dir(deg), wind_speed(m/s)
    """
    pressures: list[float] = []
    heights:   list[float] = []
    temps:     list[float] = []
    dewpts:    list[float] = []
    u_winds:   list[float] = []
    v_winds:   list[float] = []

    in_raw = False
    for line in text.splitlines():
        line = line.strip()
        if line == "%RAW%":
            in_raw = True
            continue
        if line in ("%END%", "%TITLE%"):
            in_raw = False
            continue
        if not in_raw or not line:
            continue

        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            pres = float(parts[0])
            hght = float(parts[1])
            temp = float(parts[2])
            dwpc = float(parts[3])
            wdir = float(parts[4])
            wspd = float(parts[5]) / 1.944   # convert knots to m/s
        except ValueError:
            continue

        # direction + speed (m/s) → meteorological u/v components (m/s)
        wdir_rad = math.radians(wdir)
        u = -wspd * math.sin(wdir_rad)
        v = -wspd * math.cos(wdir_rad)

        pressures.append(pres)
        heights.append(hght)
        temps.append(temp)
        dewpts.append(dwpc)
        u_winds.append(u)
        v_winds.append(v)

    if len(pressures) < 5:
        log.warning("CLAMPS skewT only has %d valid levels — skipping", len(pressures))
        return None

    return Sounding(
        lat         = 0.0,
        lon         = 0.0,
        valid_time  = file_time,
        slot_offset = idx,
        label       = "",
        pressure    = np.array(pressures, dtype=np.float64),
        temperature = np.array(temps,     dtype=np.float64),
        dewpoint    = np.array(dewpts,    dtype=np.float64),
        u_wind      = np.array(u_winds,   dtype=np.float64),
        v_wind      = np.array(v_winds,   dtype=np.float64),
        height      = np.array(heights,   dtype=np.float64),
    )


def _format_label(dt: datetime) -> str:
    return f"{dt.strftime('%H%M')}Z {dt.strftime('%b')} {dt.day}"
