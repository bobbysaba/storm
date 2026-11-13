"""Radiosondes launched from the NSSL lidar truck, found on THREDDS, for the
archive sounding viewer.

The live NSSL API's sonde index (data/fetchers/clamps_sounding_fetcher.py)
serves "upperair.NSSL_Lidar_sonde.<YYYYMMDDHHMM>.skewT.text" files; the
same files are kept on THREDDS under the lidar truck's sonde datastream,
from 2022-04-13 on (202 launches listed on 2026-11-12). The live module's
parser reads them unchanged.

Only the lidar truck (DLTRUCK1) has launches in the archive. CLAMPS1's own
sonde streams cover only 2015-06 to 2015-07 and CLAMPS2 has none (fork,
2026-09).

Launch positions: there is no location file next to these, so the parser's
(0, 0) "unknown" stands unless the caller can place the truck. The window
passes a lookup on the truck's recorded track (one-second FOFS, else MQTT):
a fix within 60 s of launch places it, and each sounding keeps the fix's
time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from archive.fetchers import clamps_common
from core.sounding import SoundingSet
from data import thredds_paths as paths
from data.fetchers.clamps_sounding_fetcher import _FILENAME_RE, _format_label, _parse_skewt

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30

# time -> (lat, lon, fix time) of the lidar truck within a minute, or None
TruckPosition = Callable[[datetime], "tuple[float, float, datetime] | None"]


def fetch_clamps_sonde_soundings(archive_time: datetime,
                                 truck_position: TruckPosition | None = None) -> SoundingSet | None:
    """Every launch on THREDDS for this UTC date, or None (most days:
    launches are a handful on a deployed day). Listing failures raise."""
    folder = paths.clamps_ingested(paths.SONDE_PLATFORM, paths.SONDE_STREAM)
    prefix = f"upperair.NSSL_Lidar_sonde.{archive_time:%Y%m%d}"
    names = sorted(n for n in clamps_common.list_folder(folder)
                   if n.startswith(prefix) and n.endswith(".skewT.text"))
    soundings = []
    for name in names:
        m = _FILENAME_RE.search(name)
        if not m:
            continue
        try:
            file_time = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        try:
            text = clamps_common.read("clamps profiles", paths.file_url(f"{folder}/{name}"),
                                      timeout=_REQUEST_TIMEOUT).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - skip this launch
            log.warning("CLAMPS sonde %s failed: %s", name, exc)
            continue
        snd = _parse_skewt(text, file_time, len(soundings))
        if snd is not None:
            snd.label = _format_label(file_time)
            soundings.append(snd)
    if not soundings:
        return None

    for snd in soundings:
        snd.location_source = "unknown"
        fix = truck_position(snd.valid_time) if truck_position is not None else None
        if fix is not None:
            snd.lat, snd.lon, snd.location_time = fix
            snd.location_source = "lidar truck track at launch (within 60 s)"
    latest = soundings[-1]
    return SoundingSet(
        lat=latest.lat,
        lon=latest.lon,
        elevation=float(latest.height[0]) if latest.height.size else 0.0,
        fetch_time=archive_time,
        soundings=soundings,
        station_id="CLAMPS",
        station_name="NSSL lidar truck (DLTRUCK1) sonde, THREDDS",
        source="nssl",
    )
