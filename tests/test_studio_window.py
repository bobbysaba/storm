"""D29 in the window: when a movie frame is complete, the radar a keyframe
shows, the burned-in captions, and the export loop (ui/studio/exporter.py)
from plan to written frames."""
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.studio import Keyframe, Overlays, Project, View
from ui.app.main_window_archive import MainWindowArchiveMixin
from ui.app.main_window_studio import MainWindowStudioMixin
from ui.studio import exporter as exporter_mod
from ui.studio.capture import MapCapture

UTC = timezone.utc
T0 = datetime(2024, 4, 27, 20, 0, tzinfo=UTC)
A = View(lon=-98.9, lat=34.0, zoom=8.0)
B = View(lon=-98.5, lat=34.3, zoom=9.0)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self, t=T0):
        self.current_time = t
        self.paused = False
        self.is_playing = False

    def pause(self):
        self.paused = True

    def set_time(self, t):
        self.current_time = t


class FakeRadar:
    def __init__(self, station="KTLX", scans=(T0,)):
        self.station = station
        self._product = "reflectivity"
        self.index_loading = False
        self._scans = list(scans)

    def _nearest_scan_before(self, t):
        before = [s for s in self._scans if s <= t]
        return before[-1] if before else None

    def scan_times(self):
        return list(self._scans)


class FakeRadarControls:
    def __init__(self):
        self.product, self.tilt = "reflectivity", 0
        self.calls = []

    def current_product(self):
        return self.product

    def current_tilt_index(self):
        return self.tilt

    def select_product(self, code):
        self.calls.append(("product", code))

    def select_tilt(self, idx):
        self.calls.append(("tilt", idx))


class FakeTimer:
    def __init__(self, active=False):
        self.active = active

    def isActive(self):
        return self.active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


def _scan(t=T0, site="KTLX", field="reflectivity"):
    return SimpleNamespace(scan_time=t, site=site, pyart_field=field, tilt_deg=0.5,
                           velocity_processing="")


class Host(MainWindowStudioMixin):
    pass


def _host(**kw):
    w = Host()
    w._time_ctrl = FakeClock()
    w._archive_radar = FakeRadar()
    w._current_radar_scan = _scan()
    w._radar_settled_scan = w._current_radar_scan
    w._archive_render_in_flight = False
    w._archive_pending_render_scan = None
    w._trails_timer = FakeTimer()
    w._archive_satellite = SimpleNamespace(_pending=set())
    w.radar_controls = FakeRadarControls()
    w.calls = []
    w._on_radar_station_clicked = lambda s: w.calls.append(("station", s))
    for k, v in kw.items():
        setattr(w, k, v)
    return w


# ── is the frame complete? ─────────────────────────────────
def test_a_settled_frame_is_ready():
    assert _host().studio_frame_pending() is None


def test_each_radar_stage_holds_the_frame():
    w = _host()
    w._archive_radar.index_loading = True
    assert w.studio_frame_pending() == "radar index"
    w._archive_radar.index_loading = False
    w._package_radar_view = {"product": "velocity", "tilt_index": 1}
    assert w.studio_frame_pending() == "radar product"
    w._package_radar_view = None
    w._time_ctrl.current_time = T0 + timedelta(minutes=5)
    w._archive_radar._scans.append(T0 + timedelta(minutes=4))
    assert w.studio_frame_pending() == "radar scan"           # the clock's scan hasn't arrived
    w._current_radar_scan = _scan(T0 + timedelta(minutes=4), field="velocity")
    assert w.studio_frame_pending() == "radar product"        # the old product's scan
    w._current_radar_scan = _scan(T0 + timedelta(minutes=4))
    w._archive_render_in_flight = True
    assert w.studio_frame_pending() == "radar image"
    w._archive_render_in_flight = False
    assert w.studio_frame_pending() == "radar sharp render"   # paused: the sharp render follows
    w._radar_settled_scan = w._current_radar_scan
    assert w.studio_frame_pending() is None


def test_trails_and_satellite_hold_the_frame():
    w = _host()
    w._trails_timer.active = True
    assert w.studio_frame_pending() == "trails"
    w._trails_timer.active = False
    w._archive_satellite._pending = {("conus", T0)}
    assert w.studio_frame_pending() == "satellite"


def test_before_the_first_scan_or_without_radar_nothing_waits_on_it():
    w = _host(_current_radar_scan=None)
    w._time_ctrl.current_time = T0 - timedelta(minutes=1)
    assert w.studio_frame_pending() is None
    assert _host(_archive_radar=None, _current_radar_scan=None).studio_frame_pending() is None
    assert _host(_archive_radar=None).studio_scan_times() == []


# ── the radar a keyframe shows ─────────────────────────────
def test_radar_state_and_applying_it():
    w = _host()
    assert w.studio_radar_state() == {"station": "KTLX", "product": "reflectivity", "tilt": 0}
    w.studio_apply_radar({"station": "KTLX", "product": "reflectivity", "tilt": 0})
    w.studio_apply_radar(None)
    assert w.radar_controls.calls == [] and w.calls == []
    w.studio_apply_radar({"station": "KTLX", "product": "velocity", "tilt": 2})
    assert w.radar_controls.calls == [("product", "velocity"), ("tilt", 2)]


def test_a_new_station_takes_product_and_tilt_on_its_first_scan():
    w = _host()
    w.studio_apply_radar({"station": "KOAX", "product": "velocity", "tilt": 1})
    assert w.calls == [("station", "KOAX")]
    assert w._package_radar_view == {"product": "velocity", "tilt_index": 1}
    assert w.radar_controls.calls == []
    assert w.studio_frame_pending() == "radar product"


# ── captions ───────────────────────────────────────────────
def test_the_data_line_names_the_scan_on_screen():
    w = _host()
    w._current_radar_scan.velocity_processing = "dealiased (region)"
    assert w.studio_data_line() == "KTLX · Reflectivity (REF) 0.5° · scan 20:00:00Z · dealiased"
    assert _host(_current_radar_scan=None).studio_data_line() == ""


def _capture(w, overlays):
    w.map_widget = SimpleNamespace()
    cap = MapCapture(w)
    cap._overlays = overlays
    return cap


def test_captions_follow_the_overlay_choices():
    w = _host()
    assert _capture(w, Overlays(time_stamp=True, status_line=True)).caption_lines() == [
        "2024-04-27 20:00:00 UTC", "KTLX · Reflectivity (REF) 0.5° · scan 20:00:00Z"]
    assert _capture(w, Overlays(time_stamp=False, status_line=True)).caption_lines() == [
        "KTLX · Reflectivity (REF) 0.5° · scan 20:00:00Z"]
    plain = QImage(320, 180, QImage.Format.Format_RGB32)
    plain.fill(QColor(255, 255, 255))
    assert _capture(w, Overlays(time_stamp=False, status_line=False)).decorate(plain) is plain
    boxed = _capture(w, Overlays()).decorate(plain.copy())
    assert boxed.pixelColor(14, 14) != QColor(255, 255, 255)     # the caption box, top left
    assert boxed.pixelColor(300, 170) == QColor(255, 255, 255)


def test_applying_a_frame_pauses_and_sets_the_clock():
    w = _host()
    w._time_ctrl.is_playing = True
    cap = _capture(w, Overlays())
    sent = []
    cap.js = lambda code, cb: sent.append(code)
    cap.apply(T0 + timedelta(minutes=3), B)
    assert w._time_ctrl.paused and w._time_ctrl.current_time == T0 + timedelta(minutes=3)
    assert "map.jumpTo" in sent[0] and "zoom: 9.0" in sent[0]
    cap._zoom_offset = 1.0                                    # rendering at twice the map's width
    cap.apply_view(B)
    assert "zoom: 10.0" in sent[-1]


# ── the sharp render marks the frame settled ───────────────
class ArchiveHost(MainWindowArchiveMixin):
    pass


def _archive_host():
    w = ArchiveHost()
    scan = _scan()
    w._archive_render_generation = 3
    w._archive_radar = FakeRadar()
    w._archive_superres_pending = (3, "KTLX", scan)
    w._radar_settled_scan = None
    w._radar_overlay = SimpleNamespace(injected=[], inject=lambda png, b: w._radar_overlay.injected.append(png))
    w._archive_superres_cache = SimpleNamespace(store={}, make_key=lambda *a: a,
                                                get=lambda k: w._archive_superres_cache.store.get(k),
                                                put=lambda k, png, b: w._archive_superres_cache.store.update({k: (png, b)}))
    w._time_ctrl = FakeClock()
    return w, scan


def test_the_sharp_render_settles_its_scan():
    w, scan = _archive_host()
    w._on_archive_superres_ready({"gen": 3, "station": "KTLX", "scan": scan, "key": "k",
                                  "png": b"sharp", "bounds": None})
    assert w._radar_overlay.injected == [b"sharp"] and w._radar_settled_scan is scan


def test_a_failed_sharp_render_still_settles_so_export_goes_on():
    w, scan = _archive_host()
    w._on_archive_superres_ready({"gen": 3, "station": "KTLX", "scan": scan, "error": "boom"})
    assert w._radar_overlay.injected == [] and w._radar_settled_scan is scan


def test_a_cached_or_skipped_sharp_render_settles(monkeypatch):
    w, scan = _archive_host()
    key = w._archive_superres_cache.make_key("KTLX", scan.pyart_field, scan.tilt_deg, scan.scan_time, "")
    w._archive_superres_cache.store[key] = (b"cached", None)
    w._on_archive_superres_timer_fired()
    assert w._radar_overlay.injected == [b"cached"] and w._radar_settled_scan is scan

    w, scan = _archive_host()
    from ui.map import radar_overlay
    monkeypatch.setattr(radar_overlay, "sharp_render_memory_ok", lambda: False)
    w._archive_superres_busy = False
    w._on_archive_superres_timer_fired()
    assert w._radar_settled_scan is scan


# ── the radar fetcher's listing ────────────────────────────
def test_the_fetcher_reports_its_listing(monkeypatch):
    from archive.fetchers import radar_archive_fetcher as raf

    def failing_get(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(raf.package_sources, "requests_get", failing_get)
    fetcher = raf.ArchiveRadarFetcher("KTLX", T0)
    try:
        assert fetcher.index_loading is False and fetcher.scan_times() == []
        fetcher._index_loading = True
        fetcher._fetch_index()                     # a failed listing isn't loading any more
        assert fetcher.index_loading is False
        fetcher._index = [T0, T0 + timedelta(minutes=4)]
        times = fetcher.scan_times()
        times.append(None)
        assert fetcher.scan_times() == [T0, T0 + timedelta(minutes=4)]
    finally:
        fetcher.shutdown()


# ── the export loop ────────────────────────────────────────
class FakeCapture(QObject):
    frame_ready = pyqtSignal(QImage)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._w = window
        self.size = None
        self.slow_frames = 0
        self.applied = []
        self.ended = False
        FakeCapture.last = self

    def begin(self, size, overlays, done):
        self.size = (65, 37)                       # odd: the exporter evens it for H.264
        done()

    def apply(self, when, view):
        self.applied.append((when, view))
        self._w._time_ctrl.set_time(when)

    def capture_when_ready(self):
        img = QImage(*self.size, QImage.Format.Format_RGB32)
        img.fill(QColor(10 * len(self.applied), 40, 60))
        self.frame_ready.emit(img)

    def cancel(self):
        pass

    def end(self):
        self.ended = True


class ExportWindow:
    def __init__(self):
        self._time_ctrl = FakeClock(T0 - timedelta(hours=1))
        self.radar = {"station": "KTLX", "product": "reflectivity", "tilt": 0}
        self.radar_calls = []

    def studio_scan_times(self):
        return []

    def studio_radar_state(self):
        return dict(self.radar)

    def studio_apply_radar(self, state):
        self.radar_calls.append(state)


def _run(exporter, timeout=10):
    result = {}
    exporter.finished.connect(lambda ok, msg: result.update(ok=ok, msg=msg))
    exporter.start()
    end = time.monotonic() + timeout
    while "ok" not in result and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)
    return result


def _movie():
    p = Project(fps=10, format="png", time_step="1 min")
    p.add(Keyframe(0.0, T0, A, hold=0.5))
    p.add(Keyframe(1.5, T0 + timedelta(minutes=10), A))
    p.keyframes[1].radar = {"station": "KOAX", "product": "velocity", "tilt": 1}
    return p


def test_export_draws_each_distinct_frame_once_and_writes_them_all(monkeypatch, tmp_path):
    monkeypatch.setattr(exporter_mod, "MapCapture", FakeCapture)
    w, p = ExportWindow(), _movie()
    out = tmp_path / "clip"
    result = _run(exporter_mod.StudioExporter(w, p, out))
    assert result.get("ok"), result
    cap = FakeCapture.last
    assert len(cap.applied) == p.distinct_frames() < p.frame_count()   # the hold reuses its image
    files = sorted(out.iterdir())
    assert len(files) == p.frame_count()
    assert QImage(str(files[0])).size().width() == 64                   # evened
    # the keyframe's radar from its place on, then the radar as it was
    assert w.radar_calls[-2] == {"station": "KOAX", "product": "velocity", "tilt": 1}
    assert w.radar_calls[-1] == {"station": "KTLX", "product": "reflectivity", "tilt": 0}
    assert cap.ended and w._time_ctrl.current_time == T0 - timedelta(hours=1)


def test_export_without_keyframes_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(exporter_mod, "MapCapture", FakeCapture)
    result = _run(exporter_mod.StudioExporter(ExportWindow(), Project(), tmp_path / "x.mp4"))
    assert result == {"ok": False, "msg": "No keyframes"}


def test_cancel_restores_the_clock_and_reports_once(monkeypatch, tmp_path):
    monkeypatch.setattr(exporter_mod, "MapCapture", FakeCapture)
    w = ExportWindow()
    exp = exporter_mod.StudioExporter(w, _movie(), tmp_path / "clip")
    seen = []
    exp.finished.connect(lambda ok, msg: seen.append((ok, msg)))
    exp.start()
    exp.cancel()
    exp.cancel()
    for _ in range(50):
        QApplication.processEvents()
    assert seen == [(False, "Export canceled")]
    assert w._time_ctrl.current_time == T0 - timedelta(hours=1) and FakeCapture.last.ended


# ── running an export from the window ──────────────────────
def test_one_export_at_a_time_and_closing_cancels(monkeypatch, tmp_path):
    from ui.dialogs import task_progress_dialog

    class Bar(QObject):
        canceled = pyqtSignal()

        def __init__(self, *a):
            super().__init__()

        def set_progress(self, *a):
            pass

        def show(self):
            pass

        def finish(self):
            pass

    started = []

    class Exporter(QObject):
        progress = pyqtSignal(int, int, str)
        finished = pyqtSignal(bool, str)

        def __init__(self, *a):
            super().__init__()

        def start(self):
            started.append(self)

        def cancel(self):
            self.finished.emit(False, "Export canceled")

    monkeypatch.setattr(task_progress_dialog, "TaskProgressDialog", Bar)
    monkeypatch.setattr(exporter_mod, "StudioExporter", Exporter)
    w = _host()
    w.status_msg_label = SimpleNamespace(text="", setText=lambda t: setattr(w.status_msg_label, "text", t))
    w._start_studio_export(Project(), tmp_path / "a.mp4")
    w._start_studio_export(Project(), tmp_path / "b.mp4")
    assert len(started) == 1 and w.status_msg_label.text == "A video export is already running"
    w._cancel_studio_export()
    assert w._studio_exporter is None and w.status_msg_label.text == "Video export canceled"
    w._cancel_studio_export()                      # nothing running: no-op
