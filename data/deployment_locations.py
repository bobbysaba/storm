"""Past deployment locations for PREV LOCS, from ``[map] deployment_locations``:
a CSV file or http(s) URL with ``lat`` and ``lon`` columns, and optionally
``rank_abi``, ``rank_aoi`` and ``rqi`` (the drawer's filters). Columns are
in https://bobbysaba.com/docs/storm/ref/configuration/#map-home-location-and-local-time.
"""
from __future__ import annotations

import csv
import io
from urllib.request import Request, urlopen

from data import auth

_METRICS = (("rank_abi", int), ("rank_aoi", int), ("rqi", float))


def read(where: str) -> list[dict]:
    """Every row with a readable lat and lon, as {lat, lon, rank_abi, rank_aoi, rqi}
    (a metric the file leaves out or blank is None)."""
    if where.startswith(("http://", "https://")):
        full, headers, context = auth.request(where, {"User-Agent": "Mozilla/5.0 STORM/1.0"})
        with urlopen(Request(full, headers=headers), timeout=20, context=context) as resp:
            text = resp.read().decode("utf-8-sig", errors="replace")
    else:
        with open(where, encoding="utf-8-sig", newline="") as f:
            text = f.read()
    return parse(text)


def parse(text: str) -> list[dict]:
    points = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            point = {"lat": float(row["lat"]), "lon": float(row["lon"])}
        except (KeyError, TypeError, ValueError):
            continue
        for name, kind in _METRICS:
            raw = (row.get(name) or "").strip()
            try:
                point[name] = kind(raw) if raw else None
            except ValueError:
                point[name] = None
        points.append(point)
    return points
