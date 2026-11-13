"""Where storm tracks are saved: one CSV per case date in the local
``storm_tracks/`` folder next to main.py (git-ignored).

    storm_tracks/storm_20240427_track.csv     the 2024-04-27 archive session's track

The date is the archive session's (its UTC day), so a track that runs past
00Z stays with the session it was drawn in. The fork's named workspaces,
manifest and shared tracks are left out: a case date has one track, and
TRACK opens it. Files are written atomically (temp file, then rename) so a
crash mid-save can't leave half a track.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

TRACKS_ROOT = Path(__file__).resolve().parents[1] / "storm_tracks"


def tracks_root() -> Path:
    return TRACKS_ROOT


def track_path(session_day: date, root: Path | None = None) -> Path:
    return (root or tracks_root()) / f"storm_{session_day:%Y%m%d}_track.csv"


def unused_path(path: Path) -> Path:
    """`path`, or name_2, name_3, ... if that file already exists."""
    path = Path(path)
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
