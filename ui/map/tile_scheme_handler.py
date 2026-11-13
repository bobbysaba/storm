
import logging
import os
import threading
import zlib
import base64
from urllib.parse import unquote

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler

from core import mbtiles

log = logging.getLogger(__name__)

SCHEME = b"storm"
HOST   = "app"    # storm://app/...

_MIME_MAP = {
    ".js":    b"application/javascript",
    ".css":   b"text/css",
    ".html":  b"text/html; charset=utf-8",
    ".pbf":   b"application/x-protobuf",
    ".png":   b"image/png",
    ".webp":  b"image/webp",
    ".json":  b"application/json",
    ".geojson": b"application/geo+json",
    ".svg":   b"image/svg+xml",
    ".woff":  b"font/woff",
    ".woff2": b"font/woff2",
}

# 1x1 transparent PNG (base64): tiny image used when radar overlay is hidden
TRANSPARENT_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)

# live keeps 10 frames per mode and archive 24 in total, so this covers both.
SAT_FRAMES_PER_MODE = 32


def _mime_for(filename: str) -> bytes:
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, b"application/octet-stream")


class StormSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serves all map assets from Python directly — no TCP socket required."""

    def __init__(self, mbtiles_path: str, static_path: str, html: str, parent=None):
        super().__init__(parent)
        self._mbtiles_path = mbtiles_path
        self._nlcd_mbtiles_path = os.path.join(
            os.path.dirname(mbtiles_path), "storm_nlcd.mbtiles"
        )
        self._satellite_mbtiles_path = os.path.join(
            os.path.dirname(mbtiles_path), "satellite.mbtiles"
        )
        self._static_path  = static_path
        self._html         = html.encode("utf-8")
        # thread-safe buffer for the latest radar overlay PNG.
        self._radar_png_lock  = threading.Lock()
        self._radar_png_bytes = b""
        # raw scan textures for the WebGL radar layer, keyed by name ("data", "az", "lut")
        self._radar_gl: dict[str, bytes] = {}
        # in-memory store for station plot PNGs — keyed by station id.
        self._plots_lock = threading.Lock()
        self._plots: dict[str, bytes] = {}
        self._sfcoa_mbtiles_lock = threading.Lock()
        self._sfcoa_mbtiles: dict[str, str] = {}
        # satellite frame PNGs keyed by (mode, frame id); oldest evicted per mode.
        self._sat_lock = threading.Lock()
        self._sat_frames: dict[tuple[str, str], bytes] = {}
        # the lidar sweep on the map (archive LIDAR): (id, PNG)
        self._lidar_png: tuple[str, bytes] = ("", b"")

    def set_radar_png(self, data: bytes) -> None:
        """Store the latest rendered radar PNG.  Thread-safe; called from any thread."""
        with self._radar_png_lock:
            self._radar_png_bytes = data

    def set_radar_gl(self, textures: dict[str, bytes]) -> None:
        """Store the WebGL radar textures.  Thread-safe; called from any thread."""
        with self._radar_png_lock:
            self._radar_gl = dict(textures)

    def set_station_plots(self, plots: dict[str, bytes]) -> None:
        """Bulk-update the station plot store.  Thread-safe; called from any thread."""
        with self._plots_lock:
            self._plots.update(plots)

    def remove_station_plots(self, ids) -> None:
        """Remove station plots by id.  Thread-safe; called from any thread."""
        with self._plots_lock:
            for sid in ids:
                self._plots.pop(sid, None)

    def set_satellite_frame(self, mode: str, frame_id: str, data: bytes) -> None:
        """Store a satellite frame PNG.  Thread-safe; called from any thread."""
        with self._sat_lock:
            self._sat_frames.pop((mode, frame_id), None)
            self._sat_frames[(mode, frame_id)] = data
            same_mode = [k for k in self._sat_frames if k[0] == mode]
            for key in same_mode[:max(0, len(same_mode) - SAT_FRAMES_PER_MODE)]:
                del self._sat_frames[key]

    def set_lidar_png(self, image_id: str, data: bytes) -> None:
        """Store the lidar sweep PNG served at /lidar/<image_id>.png."""
        with self._sat_lock:
            self._lidar_png = (image_id, data)

    def set_sfcoa_mbtiles(self, key: str, path: str) -> None:
        key = str(key or "").strip()
        path = str(path or "").strip()
        if not key or not path:
            return
        with self._sfcoa_mbtiles_lock:
            self._sfcoa_mbtiles[key] = path

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
        elif path.startswith("/nlcdtiles/"):
            self._serve_nlcd_tile(job, path)
        elif path.startswith("/satellitetiles/"):
            self._serve_satellite_tile(job, path)
        elif path.startswith("/satellite/"):
            self._serve_satellite_frame(job, path[len("/satellite/"):])
        elif path.startswith("/lidar/"):
            self._serve_lidar_png(job, path[len("/lidar/"):])
        elif path.startswith("/radar/gl/"):
            self._serve_radar_gl(job, path[len("/radar/gl/"):])
        elif path.startswith("/radar/overlay.png"):
            self._serve_radar_png(job)
        elif path.startswith("/sfcoatiles/"):
            self._serve_sfcoa_tile(job, path)
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
            conn = mbtiles.connect(self._mbtiles_path)
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


    def _serve_nlcd_tile(self, job: QWebEngineUrlRequestJob, path: str):
        # path: /nlcdtiles/z/x/y.png
        parts = path.strip("/").split("/")
        try:
            z = int(parts[1])
            x = int(parts[2])
            y = int(parts[3].replace(".png", ""))
        except (IndexError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return

        if not os.path.isfile(self._nlcd_mbtiles_path):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        y_tms = (1 << z) - 1 - y
        try:
            conn = mbtiles.connect(self._nlcd_mbtiles_path)
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
            log.warning("scheme handler: NLCD tile DB error z=%d x=%d y=%d: %s", z, x, y, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        if row is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        self._reply(job, b"image/png", bytes(row[0]))


    def _serve_satellite_tile(self, job: QWebEngineUrlRequestJob, path: str):
        # path: /satellitetiles/z/x/y.jpg
        parts = path.strip("/").split("/")
        try:
            z = int(parts[1])
            x = int(parts[2])
            y = int(parts[3].replace(".jpg", ""))
        except (IndexError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return

        if not os.path.isfile(self._satellite_mbtiles_path):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        y_tms = (1 << z) - 1 - y
        try:
            conn = mbtiles.connect(self._satellite_mbtiles_path)
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
            log.warning("scheme handler: satellite tile DB error z=%d x=%d y=%d: %s", z, x, y, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        if row is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        self._reply(job, b"image/jpeg", bytes(row[0]))


    def _serve_sfcoa_tile(self, job: QWebEngineUrlRequestJob, path: str):
        # path: /sfcoatiles/key/z/x/y.pbf
        parts = path.strip("/").split("/")
        try:
            key = unquote(parts[1])
            z = int(parts[2])
            x = int(parts[3])
            y = int(parts[4].replace(".pbf", ""))
        except (IndexError, ValueError):
            job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
            return

        with self._sfcoa_mbtiles_lock:
            mbtiles_path = self._sfcoa_mbtiles.get(key)
        if not mbtiles_path:
            self._reply(job, b"application/x-protobuf", b"")
            return

        y_tms = (1 << z) - 1 - y
        try:
            conn = mbtiles.connect(mbtiles_path)
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
            log.warning("scheme handler: SFCOA tile DB error key=%s z=%d x=%d y=%d: %s", key, z, x, y, exc)
            self._reply(job, b"application/x-protobuf", b"")
            return

        if row is None:
            self._reply(job, b"application/x-protobuf", b"")
            return

        tile_data = bytes(row[0])
        try:
            tile_data = zlib.decompress(tile_data, 32 + zlib.MAX_WBITS)
        except Exception:
            pass
        self._reply(job, b"application/x-protobuf", tile_data)


    def _serve_radar_png(self, job: QWebEngineUrlRequestJob):
        with self._radar_png_lock:
            data = self._radar_png_bytes
        if not data:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


    def _serve_radar_gl(self, job: QWebEngineUrlRequestJob, name: str):
        # name is "<texture>.png", optionally followed by a cache-bust query
        name = name.split("?", 1)[0]
        name = name[:-4] if name.endswith(".png") else name
        with self._radar_png_lock:
            data = self._radar_gl.get(name)
        if data is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


    def _serve_satellite_frame(self, job: QWebEngineUrlRequestJob, rel: str):
        # rel is "<mode>/<frame-id>.png"
        mode, _, name = rel.partition("/")
        frame_id = name[:-4] if name.endswith(".png") else name
        with self._sat_lock:
            data = self._sat_frames.get((mode, frame_id))
        if data is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


    def _serve_lidar_png(self, job: QWebEngineUrlRequestJob, name: str):
        image_id = name[:-4] if name.endswith(".png") else name
        with self._sat_lock:
            current, data = self._lidar_png
        if image_id != current or not data:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        self._reply(job, b"image/png", data)


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
