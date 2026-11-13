"""Case packages (core/case_package.py) and the menu bar that opens them.

Menu bar: File (case packages, Video Studio and Change Day in ARCHIVE,
screenshot), Edit (track undo /
redo, ARCHIVE), View (error log, debug panel), Help (user guide, About). On
macOS it sits in the system menu bar. Entries with a key in ui/shortcuts.py
get it bound on the menu's QAction (ShortcutRegistry.bind), and every entry
is in the command palette.

Export asks what to include (CaseExportDialog), then packs in a background
thread behind a TaskProgressDialog. Open imports the package's track,
restores the radar view and clock, and serves the packed files to the
fetchers (core/package_sources.py). A package given at launch
(``main.py --case``) is served before any fetcher starts, so the case opens
with no network.

Change Day, and opening a package for another date, close the window with
``restart_with`` set; main.py's session loop then opens the launch window
again (or that date directly) without quitting STORM.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import config
from core.case_package import PACKAGES_ROOT
from core.storm_track import parse_track_csv
from core.workspace import unused_path
from ui.command_registry import Command

log = logging.getLogger("ui.app.main_window")

USER_GUIDE = Path(__file__).resolve().parents[2] / "STORM_USER_GUIDE.md"
_FETCH_TIMEOUT_S = 30        # per socket read: Cancel waits at most this long on a stalled download


@dataclass(frozen=True)
class SessionRestart:
    """What main.py's session loop opens after this window closes."""
    archive_time: datetime                 # the date the launch window starts on, or the one opened
    case_package: str | None = None
    launch_window: bool = True             # False: open archive_time (and the package) directly


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def package_fetch(url: str, cancel: threading.Event | None = None) -> bytes:
    """A loaded file's bytes for a case package: the copy kept during the
    session when small, otherwise a fresh download, read in pieces so Cancel
    stops it (case_package.build checks the hash)."""
    from urllib.request import Request, urlopen  # noqa: PLC0415
    from core import provenance  # noqa: PLC0415
    kept = provenance.kept_bytes(url)
    if kept is not None:
        return kept
    headers = {"User-Agent": "Mozilla/5.0 STORM/1.0"}
    if config.NSSL_API_KEY and url.startswith(config.NSSL_API_ROOT):
        headers["X-API-Key"] = config.NSSL_API_KEY       # the API only, as the MQTT reader does
    chunks = []
    with urlopen(Request(url, headers=headers), timeout=_FETCH_TIMEOUT_S,
                 context=config.NSSL_SSL_CONTEXT) as response:
        while chunk := response.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("export canceled")
            chunks.append(chunk)
    return b"".join(chunks)


class _ExportJob:
    """case_package.build on a worker thread; the window polls it."""

    def __init__(self, path: Path, build_kwargs: dict):
        self.path = path
        self.cancel = threading.Event()
        self.done, self.total, self.label = 0, 1, ""
        self.error: BaseException | None = None
        self.finished = False
        self._kwargs = build_kwargs
        self._thread = threading.Thread(target=self._run, daemon=True, name="case-export")

    def start(self) -> None:
        self._thread.start()

    def _progress(self, done: int, total: int, label: str) -> None:
        self.done, self.total, self.label = done, max(total, 1), label

    def _run(self) -> None:
        from core import case_package  # noqa: PLC0415
        try:
            case_package.build(self.path, progress=self._progress, cancel=self.cancel,
                               fetch=lambda url: package_fetch(url, self.cancel), **self._kwargs)
        except BaseException as exc:  # noqa: BLE001 -- disk full, canceled, ...
            self.error = exc
        finally:
            self.finished = True


class MainWindowCaseMixin:

    def _setup_case_sources(self, archive: bool, case_package) -> None:
        """Provenance records this window's loads only, and only in ARCHIVE.
        A package given at launch serves its files before any fetcher starts."""
        from core import case_package as cp, package_sources, provenance  # noqa: PLC0415
        provenance.reset(active=archive)
        package_sources.deactivate()
        self._case_export = None
        self._case_package_at_start = None
        self.restart_with: SessionRestart | None = None    # set when closing for main.py's session loop
        if archive and case_package:
            try:
                manifest, _ = cp.read(Path(case_package))
            except ValueError as exc:
                log.warning("Case package %s not usable: %s", case_package, exc)
                return
            package_sources.activate(Path(case_package), manifest.get("data", []))
            self._case_package_at_start = str(case_package)

    def _close_case_sources(self) -> None:
        from core import package_sources  # noqa: PLC0415
        job = getattr(self, "_case_export", None)
        if job is not None:
            job.cancel.set()        # its partial file is removed by case_package.build
        package_sources.deactivate()

    # ── menu bar ──────────────────────────────────────────
    def _init_menu_bar(self):
        """Build File / Edit / View / Help. Keyed entries are left unconnected
        here: _init_shortcuts binds them from the shortcut table."""
        bar = self.menuBar()
        self._menu_actions: dict[str, QAction] = {}      # shortcut action -> its menu entry
        self._menu_commands: list[tuple[str, QAction]] = []   # unkeyed entries: palette title, entry

        def entry(menu, text, *, action=None, slot=None, palette="", archive_only=False, role=None):
            a = QAction(text, self)
            if role is not None:
                a.setMenuRole(role)
            if action is not None:
                self._menu_actions[action] = a
            else:
                a.triggered.connect(lambda _checked=False: slot())
                self._menu_commands.append((palette, a))
            a.setVisible(self._archive or not archive_only)
            menu.addAction(a)
            return a

        file_menu = bar.addMenu("File")
        entry(file_menu, "Open Case Package…", action="case_open", archive_only=True)
        entry(file_menu, "Export Case Package…", action="case_export", archive_only=True)
        entry(file_menu, "Show Case Packages Folder", slot=self._show_case_packages_folder,
              palette="Show case packages folder", archive_only=True)
        studio = entry(file_menu, "Video Studio", action="studio", archive_only=True)
        studio.setCheckable(True)
        sep = file_menu.addSeparator()
        sep.setVisible(self._archive)
        entry(file_menu, "Change Day…", slot=self._change_day, palette="Change day", archive_only=True)
        sep = file_menu.addSeparator()
        sep.setVisible(self._archive)
        entry(file_menu, "Save Map Screenshot…", action="screenshot")

        edit_menu = bar.addMenu("Edit")
        entry(edit_menu, "Undo Track Edit", action="track_undo", archive_only=True)
        entry(edit_menu, "Redo Track Edit", action="track_redo", archive_only=True)
        edit_menu.menuAction().setVisible(self._archive)     # nothing to edit in live modes

        view_menu = bar.addMenu("View")
        entry(view_menu, "Error Log", action="error_log")
        entry(view_menu, "Debug Panel", action="debug")

        help_menu = bar.addMenu("Help")
        entry(help_menu, "STORM User Guide", slot=self._show_user_guide, palette="STORM user guide")
        entry(help_menu, "About STORM", slot=self._show_about, palette="About STORM",
              role=QAction.MenuRole.AboutRole)

    def _palette_menu(self):
        """Menu entries without a key (the keyed ones come with the shortcuts)."""
        for title, a in getattr(self, "_menu_commands", []):
            if a.isVisible():
                yield Command(f"menu:{title.lower().replace(' ', '_')}", title, "Action", a.trigger)

    def _change_day(self):
        """Leave this session for the launch window, on this date, without quitting."""
        reply = QMessageBox.question(
            self, "Change Day",
            "Close this archive session and return to the launch window to pick another day?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._time_ctrl.pause()
        self._restart_session(SessionRestart(self._time_ctrl.current_time))

    def _restart_session(self, restart: SessionRestart):
        self.restart_with = restart
        self.close()

    def _show_case_packages_folder(self):
        PACKAGES_ROOT.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PACKAGES_ROOT)))

    def _show_user_guide(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(USER_GUIDE)))

    def _show_about(self):
        from core.case_package import storm_version  # noqa: PLC0415
        v = storm_version()
        commit = ""
        if v.get("commit"):
            commit = f"\nCommit {v['commit'][:10]}" + (" (with local changes)" if v.get("uncommitted_changes") else "")
        QMessageBox.about(self, "About STORM",
                          f"STORM {v.get('version') or config.VERSION}\n"
                          f"Severe Thunderstorm Observation and Reconnaissance Monitor{commit}")

    # ── export ────────────────────────────────────────────
    def _case_settings(self) -> dict:
        start, end = self._time_ctrl.window
        radar = getattr(self, "_archive_radar", None)
        return {
            "session_date": self._track_session_day().isoformat(),
            "archive_time": _iso(self._archive_time),
            "window_start": _iso(start), "window_end": _iso(end),
            "clock_time": _iso(self._time_ctrl.current_time),
            "radar_station": radar.station if radar is not None else None,
            "radar_product": self.radar_controls.current_product() if radar is not None else None,
            "radar_tilt_index": self.radar_controls.current_tilt_index() if radar is not None else None,
            "velocity": {"dealias": self.radar_controls.is_dealias()},
        }

    def _default_case_frame(self, session: tuple[datetime, datetime]) -> tuple[datetime, datetime]:
        """The storm track's span with 30 minutes either side when the date
        has a track, otherwise the hour either side of the clock; never
        outside the session."""
        pad = timedelta(minutes=30)
        points = sorted(p.time for p in self._track.points) if hasattr(self, "_track") else []
        if points:
            start, end = points[0] - pad, points[-1] + pad
        else:
            clock = self._time_ctrl.current_time
            start, end = clock - timedelta(hours=1), clock + timedelta(hours=1)
        start, end = max(start, session[0]), min(end, session[1])
        return (start, end) if end > start else session

    def _case_track_files(self) -> list[Path]:
        if not hasattr(self, "_track") or not len(self._track):
            return []
        path = self._track_file()
        return [path] if path.exists() else []

    def _export_case_package(self):
        """Choose what to include (all by default), then build the package in
        the background with progress and Cancel."""
        if not hasattr(self, "_time_ctrl"):
            return
        if getattr(self, "_case_export", None) is not None:
            self.status_msg_label.setText("A case package is already being exported")
            return
        from core import case_package, provenance  # noqa: PLC0415
        from ui.dialogs.case_export_dialog import CaseExportDialog  # noqa: PLC0415
        sources = provenance.snapshot()
        tracks = self._case_track_files()
        session = self._time_ctrl.window
        listed = case_package.listed_files(sources, provenance.kept_bytes)
        dialog = CaseExportDialog(sources, len(tracks), session, self._default_case_frame(session), self,
                                  listed=listed)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        include_tracks, kinds, time_frame = dialog.choices()
        if dialog.include_listed():
            # a catalog lists the whole day: only the frame (or the session) is wanted
            keep = case_package.in_time_frame(sources + listed, time_frame or session)
            sources = sources + [e for e in listed if e["url"] in keep]

        case = self._case_settings()
        if time_frame is not None:
            clock = min(max(self._time_ctrl.current_time, time_frame[0]), time_frame[1])
            case["clock_time"] = _iso(clock)
        day = self._track_session_day()
        folder = PACKAGES_ROOT / f"{day:%Y%m%d}"
        folder.mkdir(parents=True, exist_ok=True)
        name = (f"STORM_case_{day:%Y%m%d}_{case.get('radar_station') or 'noradar'}_"
                f"{_parse_iso(case['clock_time']):%H%M}Z.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Export Case Package", str(unused_path(folder / name)),
                                              "STORM case package (*.zip)")
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() != ".zip":
            path = path.with_name(path.name + ".zip")
        self._start_case_export(path, dict(case=case, tracks=tracks if include_tracks else [],
                                           sources=sources, include_kinds=kinds, time_frame=time_frame))

    def _start_case_export(self, path: Path, build_kwargs: dict):
        from ui.dialogs.task_progress_dialog import TaskProgressDialog  # noqa: PLC0415
        job = self._case_export = _ExportJob(path, build_kwargs)
        bar = self._case_export_bar = TaskProgressDialog("Export Case Package", f"Packing {path.name}", self)
        bar.canceled.connect(job.cancel.set)
        timer = QTimer(self)
        timer.setInterval(100)

        def poll():
            bar.set_progress(job.done, job.total, job.label)
            if job.finished:
                timer.stop()
                timer.deleteLater()
                bar.finish()
                self._case_export = self._case_export_bar = None
                self._case_export_finished(job)
        timer.timeout.connect(poll)
        bar.show()
        job.start()
        timer.start()

    def _case_export_finished(self, job: _ExportJob):
        from core import case_package  # noqa: PLC0415
        if isinstance(job.error, InterruptedError):
            self.status_msg_label.setText("Case package export canceled")
            return
        if job.error is not None:
            log.error("Case package export to %s failed: %s", job.path, job.error)
            QMessageBox.warning(self, "Export Case Package", f"Couldn't write {job.path.name}:\n{job.error}")
            return
        manifest, tracks = case_package.read(job.path)
        packed, missing = len(manifest.get("data", [])), len(manifest.get("data_not_packed", []))
        changed = manifest.get("data_changed_since_viewed", 0)
        detail = f"{'storm track, ' if tracks else ''}{packed} data file{'s' if packed != 1 else ''}"
        if missing:
            detail += f", {missing} couldn't be downloaded"
        if changed:
            detail += f", {changed} changed upstream since viewed"
        self.status_msg_label.setText(f"Case package saved to {job.path} ({detail})")

    # ── open ──────────────────────────────────────────────
    def _open_case_package(self):
        if not hasattr(self, "_time_ctrl"):
            return
        folder = PACKAGES_ROOT if PACKAGES_ROOT.is_dir() else Path.home()
        path, _ = QFileDialog.getOpenFileName(self, "Open Case Package", str(folder),
                                              "STORM case package (*.zip)")
        if path:
            self._load_case_package(Path(path))

    def _load_case_package(self, path: Path, *, at_start: bool = False):
        """Import the package's track, restore its radar view and clock, and
        serve its data files. A package for another date isn't opened here."""
        from core import case_package, package_sources  # noqa: PLC0415
        try:
            manifest, tracks = case_package.read(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Open Case Package", f"{path.name}: {exc}")
            return
        case = manifest.get("case") or {}
        day = self._track_session_day().isoformat()
        if case.get("session_date") != day:
            try:
                when = _parse_iso(case["archive_time"])
            except (KeyError, TypeError, ValueError) as exc:
                QMessageBox.warning(self, "Open Case Package", f"{path.name}: {exc}")
                return
            reply = QMessageBox.question(
                self, "Open Case Package",
                f"{path.name} is a case from {case.get('session_date')}; this session is {day}.\n\n"
                f"Close this session and open {case.get('session_date')}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._restart_session(SessionRestart(when, str(path), launch_window=False))
            return
        if not at_start and manifest.get("data"):
            package_sources.activate(path, manifest["data"])

        imported = self._import_case_track(tracks)
        self._restore_case_radar(case)
        if case.get("clock_time"):
            clock = _parse_iso(case["clock_time"])
            # at launch, after the startup's own first set_time
            self._later(400 if at_start else 0, lambda: (self._time_ctrl.pause(), self._time_ctrl.set_time(clock)))

        parts = [f"Opened case package {path.name}"]
        if imported:
            parts.append(f"track: {imported} points")
        frame = manifest.get("time_frame")
        if manifest.get("data"):
            span = f" for {frame['start'][11:16]}–{frame['end'][11:16]}Z" if frame else ""
            parts.append(f"{len(manifest['data'])} data files{span}; anything else downloads from its source")
        self.status_msg_label.setText(" · ".join(parts))

    def _import_case_track(self, tracks: dict[str, bytes]) -> int:
        """Replace the date's track with the package's (undoable). Asks first
        when the date already has a different track. Returns the points taken."""
        if not tracks or not hasattr(self, "_track"):
            return 0
        name, data = next(iter(tracks.items()))
        try:
            points = parse_track_csv(data, name)
        except ValueError as exc:
            log.warning("Case package track %s unreadable: %s", name, exc)
            return 0
        if not points:
            return 0
        if len(self._track):
            key = lambda pts: sorted((p.time, p.lat, p.lon) for p in pts)
            if key(self._track.points) == key(points):
                return len(points)          # already this track
            reply = QMessageBox.question(
                self, "Open Case Package",
                f"Replace this date's storm track ({len(self._track)} points) with the package's "
                f"({len(points)} points)?\nEdit > Undo Track Edit brings yours back.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return 0
        self._track.set_points(points)
        self._track_changed()
        return len(points)

    def _restore_case_radar(self, case: dict):
        """Station now (or when the radar starts); product and tilt once the
        station's first scan has listed them (_apply_package_radar_view)."""
        velocity = case.get("velocity") or {}
        self.radar_controls.set_dealias(bool(velocity.get("dealias")))
        if case.get("radar_product") or case.get("radar_tilt_index") is not None:
            self._package_radar_view = {"product": case.get("radar_product"),
                                        "tilt_index": case.get("radar_tilt_index")}
        station = case.get("radar_station")
        radar = getattr(self, "_archive_radar", None)
        if not station:
            return
        if radar is None:
            self._package_radar_station = station       # _apply_radar_station takes it
            if not getattr(self, "_radar_station_awaiting_dense_obs", False):
                return
            self._radar_station_awaiting_dense_obs = False
            self._apply_radar_station(station)
        elif radar.station != station:
            self._on_radar_station_clicked(station)
        else:
            self._later(0, self._apply_package_radar_view)

    def _apply_package_radar_view(self):
        """An opened package's product and tilt, once the scan's lists exist."""
        view = getattr(self, "_package_radar_view", None)
        if not view:
            return
        self._package_radar_view = None
        if view.get("product"):
            self.radar_controls.select_product(view["product"])
        if view.get("tilt_index") is not None and view["tilt_index"] >= 0:
            self.radar_controls.select_tilt(view["tilt_index"])
