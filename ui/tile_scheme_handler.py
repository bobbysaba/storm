# ui/tile_scheme_handler.py
# QWebEngineUrlSchemeHandler that serves the map HTML, static assets, fonts,
# and MBTiles vector tiles — replacing the Flask tile server entirely.
#
# Scheme:  storm://app/<path>
# Routes:
#   /                       — map HTML page
#   /static/<path>          — MapLibre JS/CSS, sprites, etc.
#   /tiles/z/x/y.pbf        — vector tiles from MBTiles SQLite
#
# Registration: QWebEngineUrlScheme.registerScheme() must be called in
# main.py BEFORE QApplication is created.  installUrlSchemeHandler() is
# called inside MapWidget.__init__() after QApplication exists.

import logging
import os
import sqlite3
import zlib

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler

log = logging.getLogger(__name__)

SCHEME = b"storm"
HOST   = "app"    # storm://app/...

_MIME_MAP = {
    ".js":    b"application/javascript",
    ".css":   b"text/css",
    ".html":  b"text/html; charset=utf-8",
    ".pbf":   b"application/x-protobuf",
    ".png":   b"image/png",
    ".json":  b"application/json",
    ".svg":   b"image/svg+xml",
    ".woff":  b"font/woff",
    ".woff2": b"font/woff2",
}


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

    def requestStarted(self, job: QWebEngineUrlRequestJob):
        path = job.requestUrl().path()

        if path in ("/", ""):
            self._reply(job, b"text/html; charset=utf-8", self._html)
        elif path.startswith("/static/"):
            self._serve_file(job, path[len("/static/"):])
        elif path.startswith("/fonts/"):
            # MapLibre sometimes requests /fonts/<stack>/<range>.pbf directly
            self._serve_file(job, "fonts/" + path[len("/fonts/"):])
        elif path.startswith("/tiles/"):
            self._serve_tile(job, path)
        else:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)

    # ── Static files ──────────────────────────────────────────────────────────

    def _serve_file(self, job: QWebEngineUrlRequestJob, rel_path: str):
        abs_path = os.path.normpath(os.path.join(self._static_path, rel_path))
        # Prevent path traversal outside static directory
        if not abs_path.startswith(os.path.normpath(self._static_path)):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            with open(abs_path, "rb") as f:
                data = f.read()
            self._reply(job, _mime_for(rel_path), data)
        except FileNotFoundError:
            if rel_path.endswith(".pbf"):
                # Missing glyph range — return empty PBF so MapLibre skips silently
                self._reply(job, b"application/x-protobuf", b"")
            else:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
        except Exception as exc:
            log.warning("scheme handler: static read error (%s): %s", rel_path, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)

    # ── Vector tiles ──────────────────────────────────────────────────────────

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
            # Open a new connection per request — safe across WebEngine IO threads
            conn   = sqlite3.connect(self._mbtiles_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tile_data FROM tiles "
                "WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, y_tms),
            )
            row = cursor.fetchone()
            conn.close()
        except Exception as exc:
            log.warning("scheme handler: tile DB error z=%d x=%d y=%d: %s", z, x, y, exc)
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        if row is None:
            # Empty tile — return 0-byte protobuf so MapLibre skips silently
            self._reply(job, b"application/x-protobuf", b"")
            return

        tile_data = bytes(row[0])
        try:
            tile_data = zlib.decompress(tile_data, 32 + zlib.MAX_WBITS)
        except Exception:
            pass   # not gzipped — use raw

        self._reply(job, b"application/x-protobuf", tile_data)

    # ── Helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _reply(job: QWebEngineUrlRequestJob, mime: bytes, data: bytes):
        buf = QBuffer(job)
        buf.setData(QByteArray(data))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime, buf)
