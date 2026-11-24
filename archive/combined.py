"""Several providers' ARCHIVE instruments behind one object: a team's own
lidar (the archive_lidar hook) beside a provider drawer's (NSSL's), and
their wind profiles (archive_wind_profiles) likewise. The window talks to
one lidar fetcher and one wind-profile fetcher (ui/app/main_window_lidar.py,
main_window_instruments.py); these give it that, routing each lidar's
requests to the fetcher it came from.

lidar() / wind_profiles() hand back None for none and the fetcher itself
for one, so a single provider is used exactly as before. A lidar instrument
or wind-profile id two providers both use is kept for the first and logged.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


def lidar(parts: list, parent=None):
    parts = [p for p in parts if p is not None]
    if len(parts) < 2:
        return parts[0] if parts else None
    return CombinedLidar(parts, parent)


def wind_profiles(parts: list, parent=None):
    parts = [p for p in parts if p is not None]
    if len(parts) < 2:
        return parts[0] if parts else None
    return CombinedWinds(parts, parent)


def _description(parts) -> str:
    return "; ".join(dict.fromkeys(d for d in (str(getattr(p, "description", "") or "") for p in parts) if d))


def _owners(parts, key) -> dict:
    """instrument -> the part whose sources name it (the first, on a clash)."""
    owners = {}
    for part in parts:
        for source in getattr(part, "sources", ()):
            name = key(source)
            if name in owners and owners[name] is not part:
                log.warning("Lidar %s is offered by two providers; the first is used", name)
                continue
            owners.setdefault(name, part)
    return owners


class CombinedLidar(QObject):
    """Several lidar fetchers (ArchiveRawLidarFetcher's interface) as one."""
    assets_ready = pyqtSignal(dict)
    sites_ready = pyqtSignal(dict)
    file_ready = pyqtSignal(str, object)
    large_file = pyqtSignal(str, object, int)
    instrument_loaded = pyqtSignal(str, int)
    survey_done = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parts: list, parent=None):
        super().__init__(parent)
        self.parts = list(parts)
        self._owner = _owners(self.parts, lambda s: s.instrument)
        self.sources = tuple(s for p in self.parts for s in p.sources if self._owner.get(s.instrument) is p)
        self.description = _description(self.parts)
        self._assets: dict = {}          # part -> its assets_ready, until every part has answered
        self._surveying: set = set()
        stares = [p.stares for p in self.parts if getattr(p, "stares", None) is not None]
        self.stares = stares[0] if len(stares) == 1 else CombinedStares(stares, self) if stares else None
        for part in self.parts:
            part.assets_ready.connect(self._on_assets)
            part.sites_ready.connect(self.sites_ready)
            part.file_ready.connect(self.file_ready)
            part.large_file.connect(self.large_file)
            part.instrument_loaded.connect(self.instrument_loaded)
            part.survey_done.connect(self._on_survey_done)
            part.error.connect(self.error)

    def set_truck_track(self, source) -> None:
        """source(vehicle) -> that vehicle's observations; each part is
        placed on its own vehicle's track (its track_vehicle)."""
        for part in self.parts:
            if hasattr(part, "set_truck_track"):
                part.set_truck_track(lambda p=part: source(getattr(p, "track_vehicle", None)))

    def fetch(self) -> None:
        self._assets = {}
        for part in self.parts:
            part.fetch()

    def cancel(self) -> None:
        for part in self.parts:
            if hasattr(part, "cancel"):
                part.cancel()

    def _on_assets(self, found: dict) -> None:
        self._assets[self.sender()] = found
        if len(self._assets) < len(self.parts):
            return
        merged = {}
        for part in self.parts:                        # in provider order
            merged.update({k: v for k, v in self._assets[part].items() if k not in merged})
        self.assets_ready.emit(merged)

    def survey(self, assets_by_instrument: dict) -> None:
        """Each part surveys its own lidars; survey_done once all have."""
        self._surveying = set(self.parts)
        for part in self.parts:
            part.survey({k: v for k, v in assets_by_instrument.items() if self._owner.get(k) is part})

    def _on_survey_done(self) -> None:
        self._surveying.discard(self.sender())
        if not self._surveying:
            self.survey_done.emit()

    def load_files(self, instrument: str, assets) -> None:
        part = self._owner.get(instrument)
        if part is not None:
            part.load_files(instrument, assets)


class CombinedStares(QObject):
    """Several lidar fetchers' stares (ArchiveLidarStareFetcher's interface) as one."""
    found = pyqtSignal(dict)
    ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)
    progress = pyqtSignal(str, str)

    def __init__(self, parts: list, parent=None):
        super().__init__(parent)
        self.parts = list(parts)
        self._owner = _owners(self.parts, lambda s: s.instrument)
        self.sources = tuple(s for p in self.parts for s in p.sources if self._owner.get(s.instrument) is p)
        self._found: dict = {}
        for part in self.parts:
            part.found.connect(self._on_found)
            part.ready.connect(self.ready)
            part.failed.connect(self.failed)
            if hasattr(part, "progress"):
                part.progress.connect(self.progress)

    def name(self, instrument: str) -> str:
        part = self._owner.get(instrument)
        return part.name(instrument) if part is not None else instrument

    def fetch(self) -> None:
        self._found = {}
        for part in self.parts:
            part.fetch()

    def cancel(self) -> None:
        for part in self.parts:
            part.cancel()

    def load(self, instrument: str) -> None:
        part = self._owner.get(instrument)
        if part is not None:
            part.load(instrument)

    def _on_found(self, found: dict) -> None:
        self._found[self.sender()] = found
        if len(self._found) < len(self.parts):
            return
        merged = {}
        for part in self.parts:
            merged.update({k: v for k, v in self._found[part].items() if k not in merged})
        self.found.emit(merged)


class CombinedWinds(QObject):
    """Several wind-profile fetchers (ArchiveClampsWindFetcher's interface)
    as one: a fetch asks every part and answers once all have, with every
    part's sets, or the errors when none had any."""
    sets_ready = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, parts: list, parent=None):
        super().__init__(parent)
        self.parts = list(parts)
        self.description = _description(self.parts)
        self._owner: dict = {}                  # platform id -> the part whose set it is
        self._waiting: set = set()
        self._results: dict = {}
        self._errors: list = []
        for part in self.parts:
            part.sets_ready.connect(self._on_sets)
            part.error.connect(self._on_error)

    @property
    def sets(self):
        """Every part's sets, or None until every part has loaded."""
        loaded = [p.sets for p in self.parts]
        if any(s is None for s in loaded):
            return None
        merged = {}
        for found in loaded:
            merged.update({k: v for k, v in found.items() if k not in merged})
        return merged

    def is_loading(self) -> bool:
        return bool(self._waiting) or any(p.is_loading() for p in self.parts)

    def source_name(self, platform_id: str) -> str:
        part = self._owner.get(platform_id, self.parts[0])
        return part.source_name(platform_id)

    def fetch(self) -> bool:
        if self._waiting:
            return False                        # this round's answer is coming
        self._waiting = set(self.parts)
        self._results, self._errors = {}, []
        for part in self.parts:
            part.fetch()
        return True

    def _on_sets(self, sets: dict) -> None:
        part = self.sender()
        for platform_id, found in sets.items():
            if platform_id in self._results:
                log.warning("Wind profiles %s are offered by two providers; the first is used", platform_id)
                continue
            self._results[platform_id] = found
            self._owner[platform_id] = part
        self._answered(part)

    def _on_error(self, msg: str) -> None:
        self._errors.append(msg)
        self._answered(self.sender())

    def _answered(self, part) -> None:
        if part not in self._waiting:
            return
        self._waiting.discard(part)
        if self._waiting:
            return
        if self._results or not self._errors:
            self.sets_ready.emit(dict(self._results))
        else:
            self.error.emit("; ".join(dict.fromkeys(self._errors)))
