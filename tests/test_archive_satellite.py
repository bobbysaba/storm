"""Archive GOES imagery: VIS / IR bands, the IR color table, indexing only once
SATELLITE is on, days with no imagery, and the leaner band 2 path (raw
counts, range reads of the map's window)."""
import io
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

h5py = pytest.importorskip("h5py")
pytest.importorskip("PyQt6")

from PIL import Image
from PyQt6.QtWidgets import QApplication

import archive.fetchers.satellite_archive_fetcher as saf
from core.goes_ir import IR_MAX_K, ir_rgb

CONUS_BBOX = [-116.0, 28.0, -82.0, 49.0]


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


# --- a synthetic GOES-16 CMI file -------------------------------------------

def _goes_file(counts, *, units="K", scale=0.06145332, offset=89.62, step=2.24e-4, chunks=None,
               unsigned=False):
    """A CMI file on GOES-16's CONUS fixed grid (coarser by ``step``)."""
    ny, nx = counts.shape
    bio = io.BytesIO()
    with h5py.File(bio, "w") as f:
        kw = {"chunks": chunks, "compression": "gzip"} if chunks else {}
        cmi = f.create_dataset("CMI", data=counts.astype(np.int16), **kw)
        cmi.attrs["scale_factor"] = np.array([scale], np.float32)
        cmi.attrs["add_offset"] = np.array([offset], np.float32)
        cmi.attrs["_FillValue"] = np.array([-1], np.int16)
        cmi.attrs["units"] = np.bytes_(units.encode())
        if unsigned:
            cmi.attrs["_Unsigned"] = np.bytes_(b"true")
        for name, n, start, sign in (("x", nx, -0.101332, 1), ("y", ny, 0.128212, -1)):
            ds = f.create_dataset(name, data=np.arange(n, dtype=np.int16))
            ds.attrs["scale_factor"] = np.array([sign * step], np.float32)
            ds.attrs["add_offset"] = np.array([start], np.float32)
        proj = f.create_dataset("goes_imager_projection", data=np.int32(0))
        proj.attrs["semi_major_axis"] = np.array([6378137.0])
        proj.attrs["semi_minor_axis"] = np.array([6356752.31414])
        proj.attrs["perspective_point_height"] = np.array([35786023.0])
        proj.attrs["longitude_of_projection_origin"] = np.array([-75.0])
    return bio.getvalue()


def _counts_for(kelvin, scale=0.06145332, offset=89.62):
    return int(round((kelvin - offset) / scale))


def _png(data):
    return np.asarray(Image.open(io.BytesIO(data)))


# --- IR color table -----------------------------------------------------------

def test_ir_table_is_grey_when_warm_and_colored_when_cold():
    warm, cool, cold, coldest = ir_rgb(np.array([310.0, 250.0, 213.0, 180.0]))
    assert warm[0] == warm[1] == warm[2] < 60                 # warm ground: dark grey
    assert cool[0] == cool[1] == cool[2] > warm[0]             # -23 °C: lighter grey
    assert cold[0] > 200 and cold[1] > 200 and cold[2] < 80    # -60 °C: yellow
    assert coldest.tolist() == [255, 255, 255]                 # overshooting tops: white


def test_ir_table_steps_into_color_at_minus_30():
    above, below = ir_rgb(np.array([243.2, 242.8]))
    assert above[0] == above[1] == above[2]
    assert below[2] > below[0] + 60                            # blue starts


def test_ir_table_takes_its_ends_outside_and_nan_as_warm():
    assert (ir_rgb(np.array([100.0, 400.0, np.nan])) == ir_rgb(np.array([173.0, 323.0, IR_MAX_K]))).all()


# --- rendering ----------------------------------------------------------------

def test_infrared_kelvins_go_through_the_ir_table_not_reflectance():
    """The fork drew band 13's kelvins as 0..1 reflectance: all white."""
    counts = np.full((300, 500), _counts_for(213.0), np.int16)
    png, bbox = saf._render_goes_png(_goes_file(counts), "meso1", band="ir")
    rgba = _png(png)
    assert rgba.shape[2] == 4
    opaque = rgba[rgba[..., 3] == 255]
    assert len(opaque) > 0.9 * rgba.shape[0] * rgba.shape[1]
    assert (abs(opaque[:, :3].astype(int) - ir_rgb(np.array([213.0]))[0]) <= 1).all()
    assert bbox[0] < bbox[2] and bbox[1] < bbox[3]


def test_visible_stays_grey_with_gamma():
    scale = 0.00031746
    counts = np.full((300, 500), int(round(0.25 / scale)), np.int16)
    png, _ = saf._render_goes_png(_goes_file(counts, units="1", scale=scale, offset=0.0), "meso1")
    la = _png(png)
    assert la.ndim == 3 and la.shape[2] == 2                   # grey + alpha
    grey = la[..., 0][la[..., 1] == 255]
    assert abs(int(np.median(grey)) - 128) <= 1                # sqrt(0.25) = 0.5


def test_fill_is_transparent():
    counts = np.full((300, 500), -1, np.int16)
    png, _ = saf._render_goes_png(_goes_file(counts), "meso1", band="ir")
    assert (_png(png)[..., 3] == 0).all()


def test_unsigned_counts_and_their_fill():
    counts = np.full((300, 500), _counts_for(213.0), np.int16)
    counts[:, :250] = -1                                       # 65535 once read unsigned
    png, _ = saf._render_goes_png(_goes_file(counts, unsigned=True), "meso1", band="ir")
    alpha = _png(png)[..., 3]
    assert (alpha == 0).any() and (alpha == 255).any()


def test_box_average_of_counts_skips_fill():
    rng = np.random.default_rng(1)
    counts = rng.integers(0, 4000, (37, 50)).astype(np.int16)
    counts[:4, :4] = -1                                        # one whole block of fill
    counts[10, 10] = -1
    out = saf._box_average_counts(counts, -1, 4)
    assert out.shape == (9, 12) and out.dtype == np.float32
    assert np.isnan(out[0, 0])
    block = counts[8:12, 8:12].astype(float)
    assert out[2, 2] == pytest.approx(block[block != -1].mean())
    assert out[5, 7] == pytest.approx(counts[20:24, 28:32].mean())


def test_box_average_works_in_strips(monkeypatch):
    rng = np.random.default_rng(2)
    counts = rng.integers(0, 4000, (2100 * 2 + 3, 40)).astype(np.int16)
    out = saf._box_average_counts(counts, -1, 2)               # strips of 1024 block rows
    expect = counts[:4202].reshape(2101, 2, 20, 2).mean(axis=(1, 3))
    assert np.allclose(out, expect)


def test_window_render_matches_a_full_read(monkeypatch):
    """CONUS reads only the map's window of the file, averaged on the same
    blocks as a full read, so the picture doesn't change."""
    monkeypatch.setitem(saf._OUTPUT_WIDTH, "conus", 120)
    rng = np.random.default_rng(3)
    counts = rng.integers(900, 3500, (720, 1200)).astype(np.int16)
    nc = _goes_file(counts, step=1.12e-4 * 2)
    windowed = saf._read_goes(nc, "conus", CONUS_BBOX)
    assert windowed.k > 1
    assert windowed.counts.shape[0] < 720 and windowed.counts.shape[1] < 1200

    monkeypatch.setattr(saf, "_scan_window", lambda g, bbox, k: (0, g.ny, 0, g.nx))
    saf._INDEX_CACHE.clear()
    full = saf._read_goes(nc, "conus", CONUS_BBOX)
    assert full.counts.shape == (720, 1200)
    assert saf._render_scan(windowed, "ir") == saf._render_scan(full, "ir")


def test_window_starts_on_a_block_boundary():
    g = saf._GoesGrid(lon0=np.radians(-75.0), a=6378137.0, b=6356752.31414, H=35786023.0 + 6378137.0,
                      x0=-0.101332, dx=5.6e-5, nx=10000, y0=0.128212, dy=-5.6e-5, ny=6000)
    r0, r1, c0, c1 = saf._scan_window(g, CONUS_BBOX, 6)
    assert r0 % 6 == 0 and c0 % 6 == 0
    assert 0 < c0 < c1 < 10000 and 0 <= r0 < r1 < 6000


# --- range reads --------------------------------------------------------------

class _RangeServer:
    """requests.Session stand-in serving byte ranges of one file."""

    def __init__(self, data, honor_range=True):
        self.data, self.honor_range = data, honor_range
        self.requests = []

    def __call__(self):
        return self

    def mount(self, *args):
        pass

    def close(self):
        pass

    def get(self, url, headers=None, timeout=None):
        if not self.honor_range:
            return SimpleNamespace(status_code=200, headers={}, content=self.data, raise_for_status=lambda: None)
        start, stop = map(int, re.match(r"bytes=(\d+)-(\d+)", headers["Range"]).groups())
        body = self.data[start:stop + 1]
        self.requests.append((start, len(body)))
        return SimpleNamespace(status_code=206, content=body, raise_for_status=lambda: None,
                               headers={"Content-Range": f"bytes {start}-{start + len(body) - 1}/{len(self.data)}"})


@pytest.fixture
def chunked_vis(monkeypatch):
    monkeypatch.setitem(saf._OUTPUT_WIDTH, "conus", 200)
    rng = np.random.default_rng(4)
    counts = rng.integers(0, 3150, (1440, 2400)).astype(np.int16)
    return _goes_file(counts, units="1", scale=0.00031746, offset=0.0, step=1.12e-4, chunks=(60, 60))


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(saf.package_sources, "get", lambda url: None)
    monkeypatch.setattr(saf.provenance, "record", lambda kind, url, data=None, **kw: calls.append(
        {"kind": kind, "url": url, "data": data, **kw}))
    return calls


def test_range_read_fetches_only_the_window(monkeypatch, chunked_vis, recorded):
    server = _RangeServer(chunked_vis)
    monkeypatch.setattr(saf.requests, "Session", server)
    monkeypatch.setattr(saf, "_RANGE_MERGE_GAP", 0)
    scan = saf._load_scan("https://bucket/f.nc", "conus", CONUS_BBOX, ranged=True)

    fetched = sum(n for _, n in server.requests)
    assert fetched < 0.75 * len(chunked_vis)
    assert np.array_equal(scan.counts, saf._read_goes(chunked_vis, "conus", CONUS_BBOX).counts)
    # recorded by size only, so an export downloads the whole file
    assert recorded == [{"kind": "satellite", "url": "https://bucket/f.nc", "data": None,
                         "size": len(chunked_vis), "source": "network (ranges)"}]


def test_range_read_merges_nearby_chunks_into_few_requests(monkeypatch, chunked_vis, recorded):
    server = _RangeServer(chunked_vis)
    monkeypatch.setattr(saf.requests, "Session", server)
    calls = []
    real = saf._RangeFile.prefetch
    monkeypatch.setattr(saf._RangeFile, "prefetch", lambda self, spans: (calls.append(len(server.requests)),
                                                                          real(self, spans))[1])
    saf._load_scan("https://bucket/f.nc", "conus", CONUS_BBOX, ranged=True)
    assert len(server.requests) - calls[0] <= 5                # the window's chunks


def test_server_ignoring_range_gives_the_whole_file(monkeypatch, chunked_vis, recorded):
    monkeypatch.setattr(saf.requests, "Session", _RangeServer(chunked_vis, honor_range=False))
    scan = saf._load_scan("https://bucket/f.nc", "conus", CONUS_BBOX, ranged=True)
    assert scan.counts.size > 0
    assert recorded[0]["data"] == chunked_vis


def test_failed_range_read_falls_back_to_a_download(monkeypatch, chunked_vis, recorded):
    def broken():
        raise OSError("no ranges here")
    monkeypatch.setattr(saf.requests, "Session", broken)
    got = []

    def download(kind, url, **kw):
        got.append(url)
        return SimpleNamespace(content=chunked_vis, raise_for_status=lambda: None)

    monkeypatch.setattr(saf.package_sources, "requests_get", download)
    scan = saf._load_scan("https://bucket/f.nc", "conus", CONUS_BBOX, ranged=True)
    assert got == ["https://bucket/f.nc"] and scan.counts.size > 0


def test_a_case_package_copy_is_read_whole(monkeypatch, chunked_vis):
    monkeypatch.setattr(saf.package_sources, "get", lambda url: chunked_vis)

    def no_network():
        raise AssertionError("no range requests with a packaged copy")
    monkeypatch.setattr(saf.requests, "Session", no_network)
    assert saf._load_scan("https://bucket/f.nc", "conus", CONUS_BBOX, ranged=True).counts.size > 0


# --- the fetcher --------------------------------------------------------------

def _key(product, sector, channel, when):
    stamp = f"{when:%Y%j%H%M%S}0"
    return (f"{product}/{when:%Y/%j/%H}/OR_{product}{sector}-M6{channel}_G16_s{stamp}_e{stamp}_c{stamp}.nc")


def _listing(*keys):
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{contents}</ListBucketResult>'


class _SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class _SyncPool:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def fetcher_env(monkeypatch, app):
    """A fetcher whose listings, threads and downloads run inline."""
    listed = []
    keys = {
        "ABI-L2-CMIPC": [_key("ABI-L2-CMIPC", "", ch, _utc(2024, 5, 6, 20, m))
                         for m in (1, 6) for ch in ("C01", "C02", "C13")],
        "ABI-L2-CMIPM": [_key("ABI-L2-CMIPM", s, ch, _utc(2024, 5, 6, 20, 1))
                         for s in ("1", "2") for ch in ("C02", "C13")],
    }

    def fake_get(url, timeout=None):
        prefix = url.split("prefix=")[1]
        listed.append(prefix)
        product = prefix.split("/")[0]
        body = _listing(*keys[product]) if "2024/127/20" in prefix else _listing()
        return SimpleNamespace(content=body.encode(), raise_for_status=lambda: None)

    loaded = []

    def fake_load(url, mode, bbox, ranged=False):
        loaded.append((url.rsplit("/", 1)[-1], ranged))
        return SimpleNamespace(bbox=list(bbox or [-100.0, 30.0, -95.0, 35.0]))

    monkeypatch.setattr(saf.requests, "get", fake_get)
    monkeypatch.setattr(saf.threading, "Thread", _SyncThread)
    monkeypatch.setattr(saf, "_load_scan", fake_load)
    monkeypatch.setattr(saf, "_render_scan", lambda scan, band: f"png-{band}".encode())
    return SimpleNamespace(listed=listed, loaded=loaded, keys=keys)


def _fetcher(when=_utc(2024, 5, 6, 20)):
    f = saf.ArchiveSatelliteFetcher(when)
    f._pool = _SyncPool()
    return f


def test_nothing_is_listed_until_the_layer_is_on(fetcher_env):
    f = _fetcher()
    f.set_mode("conus")
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    assert fetcher_env.listed == [] and fetcher_env.loaded == []

    frames = []
    f.frame_ready.connect(frames.append)
    f.set_active(True)
    assert f._indexed_modes == {"conus", "meso1", "meso2"}
    assert frames and frames[-1].band == "vis" and frames[-1].mode == "conus"


def test_one_listing_indexes_both_bands(fetcher_env):
    f = _fetcher()
    f.set_active(True)
    vis, ir = f._refs("conus", "vis"), f._refs("conus", "ir")
    assert [r.timestamp.minute for r in vis] == [1, 6] and all("M6C02_" in r.key for r in vis)
    assert [r.timestamp.minute for r in ir] == [1, 6] and all("M6C13_" in r.key for r in ir)
    assert len([p for p in fetcher_env.listed if p.startswith("ABI-L2-CMIPC")]) == 30   # once per hour


def test_band_switch_shows_that_bands_frame(fetcher_env):
    f = _fetcher()
    frames = []
    f.frame_ready.connect(frames.append)
    f.set_mode("conus")
    f.set_active(True)
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_band("ir")
    assert frames[-1].band == "ir" and frames[-1].png == b"png-ir"
    assert frames[-1].frame_id.startswith("ir-") and frames[-1].frame_id != frames[0].frame_id
    assert any("M6C13_" in name for name, _ in fetcher_env.loaded)
    # back to VIS: from the cache, no new download
    n = len(fetcher_env.loaded)
    f.set_band("vis")
    assert frames[-1].band == "vis" and len(fetcher_env.loaded) == n


def test_only_conus_visible_uses_range_reads(fetcher_env):
    f = _fetcher()
    f.set_mode("conus")
    f.set_active(True)
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_band("ir")
    f.set_mode("meso1")
    ranged = {("C02" in name and "CMIPC" in name): r for name, r in fetcher_env.loaded}
    assert ranged == {True: True, False: False}


def test_turned_off_fetches_nothing_more(fetcher_env):
    f = _fetcher()
    f.set_mode("conus")
    f.set_active(True)
    f.set_active(False)
    n = len(fetcher_env.loaded)
    f.on_time_changed(_utc(2024, 5, 6, 20, 8))
    f.set_band("ir")
    assert len(fetcher_env.loaded) == n


def test_before_goes16_is_none_without_asking(fetcher_env):
    f = _fetcher(_utc(2016, 5, 24, 20))
    assert f.unavailable_reason.startswith("No GOES-16")
    reasons, errors = [], []
    f.unavailable.connect(reasons.append)
    f.error.connect(errors.append)
    f.set_mode("conus")
    f.set_active(True)
    assert reasons == [f.unavailable_reason] and errors == [] and fetcher_env.listed == []


def test_the_day_before_the_first_data_still_looks_next_morning(fetcher_env):
    f = _fetcher(_utc(2017, 2, 27, 20))
    assert f.unavailable_reason == ""
    f.set_active(True)
    conus = [p for p in fetcher_env.listed if p.startswith("ABI-L2-CMIPC")]
    assert conus and all("/2017/059/" in p for p in conus)      # not hours before the first data


def test_an_empty_day_is_none_not_an_error(fetcher_env):
    f = _fetcher(_utc(2024, 7, 1, 20))                          # the fake lists nothing then
    reasons, errors = [], []
    f.unavailable.connect(reasons.append)
    f.error.connect(errors.append)
    f.set_active(True)
    assert reasons == ["No GOES-East imagery for 2024-07-01"] and errors == []


def test_meso_sector_boxes_come_from_the_small_infrared_file(fetcher_env):
    f = _fetcher()
    boxes = []
    f.meso_sectors_updated.connect(boxes.append)
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_active(True)
    meso = [name for name, _ in fetcher_env.loaded if "CMIPM" in name]
    assert meso and all("C13" in name for name in meso)
    assert boxes[-1][1] is not None and boxes[-1][2] is not None


def test_meso_boxes_come_from_the_clocks_hour_before_the_day_is_listed(fetcher_env):
    f = _fetcher()
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_active(True)
    assert fetcher_env.listed[0] == "ABI-L2-CMIPM/2024/127/20/"
    first_load = fetcher_env.loaded[0][0]
    assert "CMIPM1-M6C13_" in first_load
    assert fetcher_env.listed.index("ABI-L2-CMIPC/2024/127/00/") > 0


def test_both_meso_sectors_share_one_listing(fetcher_env):
    f = _fetcher()
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_active(True)
    meso = [p for p in fetcher_env.listed if p.startswith("ABI-L2-CMIPM")]
    assert len(meso) == 1 + 30                                  # the clock's hour, then the day once
    assert {"meso1", "meso2"} <= f._indexed_modes


def test_meso_boxes_follow_the_clock(fetcher_env):
    f = _fetcher()
    f.on_time_changed(_utc(2024, 5, 6, 20, 3))
    f.set_active(True)
    n = len(fetcher_env.loaded)
    f.on_time_changed(_utc(2024, 5, 6, 20, 20))                 # within the half hour: kept
    assert len(fetcher_env.loaded) == n
    f.on_time_changed(_utc(2024, 5, 6, 22, 0))                  # 20:01 is still the nearest: kept
    assert len(fetcher_env.loaded) == n
    later = _key("ABI-L2-CMIPM", "1", "C13", _utc(2024, 5, 6, 23, 1))
    f._indexes["meso1"]["ir"].append(saf._FrameRef(timestamp=_utc(2024, 5, 6, 23, 1), key=later))
    f.on_time_changed(_utc(2024, 5, 6, 23, 3))                  # a newer frame: looked up again
    assert fetcher_env.loaded[-1][0] == later.rsplit("/", 1)[-1]
    n = len(fetcher_env.loaded)
    f.on_time_changed(_utc(2024, 5, 6, 23, 8))
    assert len(fetcher_env.loaded) == n                         # not asked again every tick


# --- the drawer ---------------------------------------------------------------

def _controls():
    from ui.controls.satellite_controls import SatelliteControls
    return SatelliteControls()


def test_band_buttons_only_in_archive(app):
    live = _controls()
    assert live.band_buttons() == {}
    arch = _controls()
    arch.configure_for_archive(True)
    assert set(arch.band_buttons()) == {"vis", "ir"} and arch.current_band() == "vis"


def test_bands_are_exclusive_and_ir_shows_its_legend(app):
    c = _controls()
    c.configure_for_archive(True)
    bands = []
    c.band_changed.connect(bands.append)
    c._btn_ir.setChecked(True)
    assert c.current_band() == "ir" and not c._btn_vis.isChecked()
    assert c._ir_legend.isVisibleTo(c)
    c._btn_ir.setChecked(False)                                 # can't leave no band chosen
    assert c.current_band() == "ir"
    c.select_band("vis")
    assert c.current_band() == "vis" and not c._ir_legend.isVisibleTo(c)
    assert bands == ["ir", "vis"]


def test_unavailable_disables_the_drawer_with_its_reason(app):
    c = _controls()
    c.configure_for_archive(True)
    c._btn_conus.setChecked(True)
    c.set_unavailable("No GOES-16 imagery before 28 Feb 2017")
    assert c.current_mode() == ""
    assert not any(b.isEnabled() for b in (*c.sector_buttons().values(), *c.band_buttons().values()))
    assert c._note.isVisibleTo(c) and "2017" in c._note.text()
    assert c._btn_conus.toolTip() == c._note.text()


# --- the window ---------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args, kwargs))


def _host():
    from ui.app.main_window import MainWindow

    class Host:
        _on_satellite_toggled = MainWindow._on_satellite_toggled
        _on_archive_satellite_band_changed = MainWindow._on_archive_satellite_band_changed
        _on_archive_satellite_unavailable = MainWindow._on_archive_satellite_unavailable

        def __init__(self):
            self._archive = True
            self._archive_sat_has_data = True
            self._archive_satellite = _Recorder()
            self.satellite_controls = _Recorder()
            self.satellite_controls.current_mode = lambda: "conus"
            self.map_widget = _Recorder()
            self.status_msg_label = _Recorder()
            self._archive_controls = _Recorder()
            self.status = _Recorder()
            self._layer_status = {"satellite": self.status}
            self.active = []

        def _set_layer_active(self, key, on):
            self.active.append((key, on))

    return Host()


def test_satellite_button_turns_the_archive_fetcher_on_and_off():
    h = _host()
    h._on_satellite_toggled(True)
    h._on_satellite_toggled(False)
    assert [c[1] for c in h._archive_satellite.calls if c[0] == "set_active"] == [(True,), (False,)]


def test_band_change_drops_the_old_frame():
    h = _host()
    h._on_archive_satellite_band_changed("ir")
    assert ("set_band", ("ir",), {}) in h._archive_satellite.calls
    assert ("clear_satellite_frame", (), {}) in h.map_widget.calls
    assert h._archive_sat_has_data is False


def test_unavailable_reads_none_not_error():
    h = _host()
    h._on_archive_satellite_unavailable("No GOES-16 imagery before 28 Feb 2017")
    assert ("set_satellite_status", ("Sat: none",), {"tooltip": "No GOES-16 imagery before 28 Feb 2017"}) \
        in h._archive_controls.calls
    assert ("set_source_active", ("mode", False), {}) in h.status.calls
    assert not any(name == "mark_error" for name, _, _ in h.status.calls)
    assert ("set_unavailable", ("No GOES-16 imagery before 28 Feb 2017",), {}) in h.satellite_controls.calls
