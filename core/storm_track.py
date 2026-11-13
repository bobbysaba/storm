"""Subjective storm (mesocyclone) track: points placed by hand on archive radar,
one per frame, as in MESO-VIEW's inline track editor.

``StormTrack`` holds the points with undo / redo and knows nothing about Qt or
the map; ``ui/app/main_window_track.py`` drives it from map clicks and drags.
Every edit replaces the whole list, so one undo step is one user action.

Tracks are CSV files in MESO-VIEW's layout (point_id, time, lat, lon first),
so they open in MESO-VIEW and Excel. ``read_track_csv`` takes STORM's files,
MESO-VIEW's, or any table with time and lat / lon columns; other columns are
ignored and not written back. Where they live: ``core/workspace.py``.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TrackPoint:
    point_id: int
    time: datetime                 # UTC
    lat: float
    lon: float
    radar_site: str = ""           # NEXRAD site on screen when placed; "" before one is picked
    product: str = ""              # product code on screen, e.g. "reflectivity"
    product_label: str = ""        # e.g. "Reflectivity (REF)"
    tilt_deg: float | None = None
    source: str = "manual"         # manual, moved_retimed, moved_position_only


COLUMNS = (
    "point_id", "time", "lat", "lon", "source",
    "radar_site", "product", "product_label", "tilt_deg",
)
_TIME_ALIASES = ("time", "datetime", "date", "time_utc", "utc")
_LAT_ALIASES = ("lat", "latitude")
_LON_ALIASES = ("lon", "longitude", "lng")


def _parse_time(text: str) -> datetime | None:
    """ISO times ("2024-04-27 20:03:10", "...T20:03:10Z", "+00:00"); naive is UTC."""
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def _number(text) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value == value else None      # NaN


def read_track_csv(path: Path) -> list[TrackPoint]:
    """Points in time order. Column names match the way MESO-VIEW matches
    them (case and spaces ignored, a few aliases); rows without a usable time
    or position are skipped; missing or repeated point ids get fresh ones.
    Raises ValueError when the file has no time or lat / lon column."""
    path = Path(path)
    with open(path, newline="", encoding="utf-8-sig") as f:
        return _read_track_rows(f, path.name)


def parse_track_csv(data: bytes, name: str) -> list[TrackPoint]:
    """read_track_csv for a file's bytes (a case package's track)."""
    return _read_track_rows(io.StringIO(data.decode("utf-8-sig"), newline=""), name)


def _read_track_rows(f, name: str) -> list[TrackPoint]:
    reader = csv.DictReader(f)
    names = {str(n).strip().lower(): n for n in (reader.fieldnames or [])}

    def column(aliases):
        return next((names[a] for a in aliases if a in names), None)

    time_col, lat_col, lon_col = column(_TIME_ALIASES), column(_LAT_ALIASES), column(_LON_ALIASES)
    missing = [n for n, c in (("time", time_col), ("lat", lat_col), ("lon", lon_col)) if c is None]
    if missing:
        raise ValueError(f"{name} has no {', '.join(missing)} column")
    rows = []
    for row in reader:
        when = _parse_time(row.get(time_col) or "")
        lat, lon = _number(row.get(lat_col)), _number(row.get(lon_col))
        if when is not None and lat is not None and lon is not None:
            rows.append((when, lat, lon, {k: (row.get(names[k]) or "").strip()
                                          for k in COLUMNS if k in names}))
    rows.sort(key=lambda r: r[0])

    points, used = [], set()
    for when, lat, lon, extra in rows:
        point_id = _number(extra.get("point_id"))
        point_id = int(point_id) if point_id is not None and int(point_id) not in used else None
        if point_id is None:
            point_id = max(used, default=0) + 1
        used.add(point_id)
        points.append(TrackPoint(
            point_id=point_id, time=when, lat=lat, lon=lon,
            radar_site=extra.get("radar_site", ""), product=extra.get("product", ""),
            product_label=extra.get("product_label", ""),
            tilt_deg=_number(extra.get("tilt_deg")),
            source=extra.get("source") or "original",
        ))
    return points


def track_csv_text(points: list[TrackPoint]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for p in sorted(points, key=lambda p: p.time):
        writer.writerow({
            "point_id": p.point_id,
            "time": p.time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "lat": round(p.lat, 6), "lon": round(p.lon, 6),
            "source": p.source, "radar_site": p.radar_site,
            "product": p.product, "product_label": p.product_label,
            "tilt_deg": "" if p.tilt_deg is None else p.tilt_deg,
        })
    return buffer.getvalue()


def write_track_csv(points: list[TrackPoint], path: Path) -> None:
    """Written atomically: a crash mid-save leaves the previous file intact."""
    from core.workspace import atomic_write_text  # noqa: PLC0415
    atomic_write_text(Path(path), track_csv_text(points))


def _second(when: datetime) -> datetime:
    return when.replace(microsecond=0)


class StormTrack:
    def __init__(self):
        self.points: list[TrackPoint] = []
        self.selected_id: int | None = None
        self._next_id = 1
        self._undo: list[list[TrackPoint]] = []
        self._redo: list[list[TrackPoint]] = []

    # ── state ─────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.points)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def point(self, point_id: int) -> TrackPoint | None:
        return next((p for p in self.points if p.point_id == point_id), None)

    def nearest_id(self, when: datetime) -> int | None:
        if not self.points:
            return None
        return min(self.points, key=lambda p: abs((p.time - when).total_seconds())).point_id

    # ── edits ─────────────────────────────────────────────
    def load(self, points: list[TrackPoint]) -> None:
        """Start from saved points: nothing to undo, nothing selected."""
        self._undo.clear()
        self._redo.clear()
        self.selected_id = None
        self.set_points(points, record_undo=False)

    def set_points(self, points: list[TrackPoint], *, record_undo: bool = True) -> None:
        """Replace the track as one undoable edit."""
        if record_undo:
            self._undo.append(list(self.points))
            self._redo.clear()
        self.points = sorted(points, key=lambda p: p.time)
        self._next_id = max((p.point_id for p in self.points), default=0) + 1
        if self.point(self.selected_id) is None:
            self.selected_id = None

    def place(self, when: datetime, lat: float, lon: float, **context) -> tuple[TrackPoint, bool]:
        """The storm center is at (lat, lon) at ``when``.

        A point already at this time (to the second: stepping frames lands on
        scan times exactly) moves here instead, so stepping back to a point's
        frame and clicking refines it. ``context`` fills the radar fields of a
        new point. Returns the point and whether it was an existing one.
        """
        at = _second(when)
        existing = [p for p in self.points if _second(p.time) == at]
        if existing:
            target = existing[-1]
            moved = replace(target, lat=lat, lon=lon, source="manual")
            self.selected_id = target.point_id
            self.set_points([moved if p.point_id == target.point_id else p for p in self.points])
            return moved, True
        point = TrackPoint(point_id=self._next_id, time=when, lat=lat, lon=lon, **context)
        self.set_points(self.points + [point])
        return point, False

    def move(self, point_id: int, lat: float, lon: float, when: datetime | None) -> None:
        """Drag: new position, and the time ``when`` unless it's None (Alt held)."""
        target = self.point(point_id)
        if target is None:
            return
        moved = replace(target, lat=lat, lon=lon,
                        time=target.time if when is None else when,
                        source="moved_position_only" if when is None else "moved_retimed")
        self.set_points([moved if p.point_id == point_id else p for p in self.points])

    def delete(self, point_id: int) -> bool:
        if self.point(point_id) is None:
            return False
        self.set_points([p for p in self.points if p.point_id != point_id])
        return True

    def clear(self) -> None:
        if self.points:
            self.set_points([])

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(list(self.points))
        self.set_points(self._undo.pop(), record_undo=False)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(list(self.points))
        self.set_points(self._redo.pop(), record_undo=False)
        return True

    # ── map ───────────────────────────────────────────────
    def geojson(self, now: datetime) -> dict:
        """Points (state: selected / current / normal, plus the hover label's
        fields) and, with two or more, the line joining them."""
        current_id = self.nearest_id(now)
        total = len(self.points)
        features = []
        for n, p in enumerate(self.points, start=1):
            if p.point_id == self.selected_id:
                state = "selected"
            elif p.point_id == current_id:
                state = "current"
            else:
                state = "normal"
            radar = " ".join(x for x in (
                p.radar_site, p.product_label or p.product,
                f"{p.tilt_deg:.1f}°" if p.tilt_deg is not None else "") if x)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
                "properties": {
                    "point_id": p.point_id, "state": state,
                    "time": p.time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "order": f"Point {n} of {total}", "radar": radar,
                },
            })
        if total >= 2:
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[p.lon, p.lat] for p in self.points]},
                "properties": {},
            })
        return {"type": "FeatureCollection", "features": features}
