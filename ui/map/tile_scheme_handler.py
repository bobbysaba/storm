
import logging
from collections import OrderedDict
import os
import sqlite3
import ssl
import threading
import zlib
import base64
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler

log = logging.getLogger(__name__)

SCHEME = b"storm"
HOST   = "app"    # storm://app/...
HRRR_FILESERVER_BASE = "https://data.nssl.noaa.gov/thredds/fileServer/"
REQUEST_TIMEOUT_SECONDS = 8
USER_AGENT = "Mozilla/5.0 STORM/1.0"
HRRR_TILE_CACHE_MAX_ITEMS = 512

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_MIME_MAP = {
    ".js":    b"application/javascript",
    ".css":   b"text/css",
    ".html":  b"text/html; charset=utf-8",
    ".pbf":   b"application/x-protobuf",
    ".png":   b"image/png",
    ".webp":  b"image/webp",
    ".json":  b"application/json",
    ".svg":   b"image/svg+xml",
    ".woff":  b"font/woff",
    ".woff2": b"font/woff2",
}

# 1x1 transparent PNG (base64): tiny image used when radar overlay is hidden
TRANSPARENT_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def _mime_for(filename: str) -> bytes:
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, b"application/octet-stream")


class StormSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves all map assets from Python directly — no TCP socket required."""

    def __init__(self, mbtiles_path: str, static_path: str, html: str, parent=None):
        super().__init__(parent)
        self._mbtiles_path = mbtiles_path
        self._static_path  = static_path
        self._html         = html.encode("utf-8")
        # thread-safe buffer for the latest radar overlay PNG.
        self._radar_png_lock  = threading.Lock()
        self._radar_png_bytes = b""
        # in-memory store for station plot PNGs — keyed by station id.
        self._plots_lock = threading.Lock()
        self._plots: dict[str, bytes] = {}
        self._hrrr_tile_cache_lock = threading.Lock()
        self._hrrr_tile_cache: OrderedDict[str, bytes] = OrderedDict()

    def set_radar_png(self, data: bytes) -> None:
        """Store the latest rendered radar PNG.  Thread-safe; called from any thread."""
        with self._radar_png_lock:
            self._radar_png_bytes = data

    def set_station_plots(self, plots: dict[str, bytes]) -> None:
        """Bulk-update the station plot store.  Thread-safe; called from any thread."""
        with self._plots_lock:
            self._plots.update(plots)

    def remove_station_plots(self, ids) -> None:
        """Remove station plots by id.  Thread-safe; called from any thread."""
        with self._plots_lock:
            for sid in ids:
                self._plots.pop(sid, None)

    def requestStarted(self, job: QWebEngineUrlRequestJob):
        path = job.requestUrl().path()

        if path in ("/", ""):
            self._reply(job, b"text/html; charset=utf-8", self._html)
        elif path.startswith("/static/"):
            self._serve_file(job, path[len("/static/"):])
        elif path.startswith("/fonts/"):
            # mapLibre sometimes requests /fonts/<stack>/<range>.pbf directly
            self._serve_file(job, "fonts/" + path[len("/fonts/"):])
        elif path.startswith("/tiles/"):
            self._serve_tile(job, path)
        elif path.startswith("/radar/overlay.png"):
            self._serve_radar_png(job)
        elif path.startswith("/hrrrtiles/"):
            self._serve_hrrr_tile(job, path[len("/hrrrtiles/"):])
        elif path.startswith("/plots/"):
            self._serve_station_plot(job, path[len("/plots/"):])
        else:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)


    def _serve_file(self, job: QWebEngineUrlRequestJob, rel_path: str):
        abs_path = os.path.normpath(os.path.join(self._static_path, rel_path))
        # prevent path traversal outside static directory
        if not abs_path.startswith(os.path.normpath(self._static_path)):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            with open(abs_path, "rb") as f:
                data = f.read()
            self._reply(job, _mime_for(rel_path), data)
        except FileNotFoundError:
            if rel_path.endswith(".pbf"):
                # missing glyph range — return empty PBF so MapLibre skips silently
                self._reply(job, b"application/x-protobuf", b"")
            else:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
        except Exception as exc:
            log.warning("scheme handler: static read error (%s): %s", rel_path, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)


    def _serve_tile(self, job: QWebEngineUrlRequestJob, path: str):
        # path: /tiles/z/x/y.pbf
        parts = path.strip("/").split("/")
        try:
            z = int(parts[1])
            x = int(parts[2])
            y = int(parts[3].replace(".pbf", ""))
        except (IndexError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return

        y_tms = (1 << z) - 1 - y   # flip Y: MBTiles TMS → XYZ

        try:
            # open a new connection per request — safe across WebEngine IO threads
            conn = sqlite3.connect(self._mbtiles_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT tile_data FROM tiles "
                    "WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                    (z, x, y_tms),
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("scheme handler: tile DB error z=%d x=%d y=%d: %s", z, x, y, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        if row is None:
            # empty tile — return 0-byte protobuf so MapLibre skips silently
            self._reply(job, b"application/x-protobuf", b"")
            return

        tile_data = bytes(row[0])
        try:
            tile_data = zlib.decompress(tile_data, 32 + zlib.MAX_WBITS)
        except Exception:
            pass   # not gzipped — use raw

        self._reply(job, b"application/x-protobuf", tile_data)


    def _serve_radar_png(self, job: QWebEngineUrlRequestJob):
        with self._radar_png_lock:
            data = self._radar_png_bytes
        if not data:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


    def _serve_hrrr_tile(self, job: QWebEngineUrlRequestJob, rel_path: str):
        rel_path = unquote(rel_path).lstrip("/")
        if not rel_path.endswith(".pbf") or ".." in rel_path.split("/"):
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return

        cached = self._hrrr_tile_from_cache(rel_path)
        if cached is not None:
            self._reply(job, b"application/x-protobuf", cached)
            return

        url = HRRR_FILESERVER_BASE + rel_path
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CTX) as resp:
                data = resp.read()
        except HTTPError as exc:
            if exc.code == 404:
                self._store_hrrr_tile(rel_path, b"")
                self._reply(job, b"application/x-protobuf", b"")
            else:
                log.warning("scheme handler: HRRR tile HTTP %s (%s)", exc.code, rel_path)
                self._reply(job, b"application/x-protobuf", b"")
            return
        except (TimeoutError, URLError) as exc:
            log.warning("scheme handler: HRRR tile fetch error (%s): %s", rel_path, exc)
            self._reply(job, b"application/x-protobuf", b"")
            return
        except Exception as exc:
            log.warning("scheme handler: HRRR tile read error (%s): %s", rel_path, exc)
            self._reply(job, b"application/x-protobuf", b"")
            return

        try:
            data = zlib.decompress(data, 32 + zlib.MAX_WBITS)
        except Exception:
            pass
        self._store_hrrr_tile(rel_path, data)
        self._reply(job, b"application/x-protobuf", data)


    def _hrrr_tile_from_cache(self, key: str) -> bytes | None:
        with self._hrrr_tile_cache_lock:
            data = self._hrrr_tile_cache.get(key)
            if data is not None:
                self._hrrr_tile_cache.move_to_end(key)
            return data


    def _store_hrrr_tile(self, key: str, data: bytes) -> None:
        with self._hrrr_tile_cache_lock:
            self._hrrr_tile_cache[key] = data
            self._hrrr_tile_cache.move_to_end(key)
            while len(self._hrrr_tile_cache) > HRRR_TILE_CACHE_MAX_ITEMS:
                self._hrrr_tile_cache.popitem(last=False)


    def _serve_station_plot(self, job: QWebEngineUrlRequestJob, filename: str):
        # filename is e.g. "surface:asos:OKC.png" — strip the .png suffix to get the id
        sid = filename[:-4] if filename.endswith(".png") else filename
        sid = unquote(sid)
        with self._plots_lock:
            data = self._plots.get(sid)
        if data is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


    @staticmethod
    def _reply(job: QWebEngineUrlRequestJob, mime: bytes, data: bytes):
        buf = QBuffer(job)
        buf.setData(QByteArray(data))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime, buf)
