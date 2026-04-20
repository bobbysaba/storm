from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from socket import timeout as SocketTimeout
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class HrrrField:
    field_id: str
    label: str
    latest_url: str


class HrrrOverlayFetcher(QObject):
    """Fetches static HRRR overlay manifests generated server-side.

    The server side publishes plain JSON + image files, usually through a
    THREDDS fileServer path. This fetcher only follows those JSON pointers and
    emits image metadata for MapLibre; it never decodes GRIB locally.
    """

    catalog_ready = pyqtSignal(object)          # list[HrrrField]
    field_ready = pyqtSignal(str, object)       # field_id, field latest payload
    overlay_ready = pyqtSignal(object)          # metadata dict with absolute image URLs
    fetch_error = pyqtSignal(str)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self._base_url = _clean_base_url(base_url)
        self._catalog: dict | None = None
        self._field_latest: dict[str, dict] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    def refresh_catalog(self) -> None:
        threading.Thread(target=self._fetch_catalog_worker, daemon=True).start()

    def fetch_field(self, field_id: str) -> None:
        threading.Thread(target=self._fetch_field_worker, args=(field_id,), daemon=True).start()

    def fetch_overlay(self, field_id: str, forecast_hour: int) -> None:
        threading.Thread(
            target=self._fetch_overlay_worker,
            args=(field_id, forecast_hour),
            daemon=True,
        ).start()

    def _fetch_catalog_worker(self) -> None:
        try:
            catalog_url = urljoin(self._base_url + "/", "latest.json")
            catalog = _fetch_json(catalog_url)
            fields = []
            for field_id, item in sorted((catalog.get("fields") or {}).items()):
                latest_url = _abs_url(self._base_url, item.get("latest", ""))
                fields.append(HrrrField(
                    field_id=str(field_id),
                    label=str(item.get("label") or field_id),
                    latest_url=latest_url,
                ))
            self._catalog = catalog
            self.catalog_ready.emit(fields)
        except Exception as exc:
            log.warning("HRRR catalog fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR catalog: {_friendly_error(exc, self._base_url)}")

    def _fetch_field_worker(self, field_id: str) -> None:
        try:
            latest_url = self._field_latest_url(field_id)
            payload = _fetch_json(latest_url)
            self._field_latest[field_id] = payload
            self.field_ready.emit(field_id, payload)
        except Exception as exc:
            log.warning("HRRR field fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR {field_id}: {_friendly_error(exc, self._base_url)}")

    def _fetch_overlay_worker(self, field_id: str, forecast_hour: int) -> None:
        try:
            field_payload = self._field_latest.get(field_id)
            if field_payload is None:
                field_payload = _fetch_json(self._field_latest_url(field_id))
                self._field_latest[field_id] = field_payload

            metadata_rel = None
            for item in field_payload.get("items", []):
                if int(item.get("forecast_hour", -1)) == int(forecast_hour):
                    metadata_rel = item.get("metadata")
                    break
            if not metadata_rel:
                raise RuntimeError(f"forecast hour f{forecast_hour:02d} not found")

            metadata_url = _abs_url(self._base_url, str(metadata_rel))
            metadata = _fetch_json(metadata_url)
            metadata["metadata_url"] = metadata_url
            if metadata.get("image_png"):
                metadata["image_png_url"] = _abs_url(self._base_url, metadata["image_png"])
            if metadata.get("image_webp"):
                metadata["image_webp_url"] = _abs_url(self._base_url, metadata["image_webp"])
            self.overlay_ready.emit(metadata)
        except Exception as exc:
            log.warning("HRRR overlay fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR overlay: {_friendly_error(exc, self._base_url)}")

    def _field_latest_url(self, field_id: str) -> str:
        if self._catalog:
            item = (self._catalog.get("fields") or {}).get(field_id)
            if item and item.get("latest"):
                return _abs_url(self._base_url, item["latest"])
        return _abs_url(self._base_url, f"{field_id}/latest.json")


def _clean_base_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("HRRR base URL is empty")
    return base


def _abs_url(base_url: str, path: str) -> str:
    path = str(path or "").strip()
    if path.startswith(("http://", "https://", "file://")):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "STORM/HRRR-overlay"})
    with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON root is not an object: {url}")
    return payload


def _friendly_error(exc: Exception, base_url: str) -> str:
    text = str(exc)
    reason = getattr(exc, "reason", None)
    if isinstance(exc, URLError) and reason:
        text = str(reason)
    if "Connection refused" in text or "[Errno 61]" in text:
        if "localhost" in base_url or "127.0.0.1" in base_url:
            return (
                f"connection refused at {base_url}; start "
                "`python -m http.server 8080 --directory /Users/bobbysaba/Documents/hrrr_test` "
                "or set HRRR_BASE_URL to the THREDDS fileServer URL"
            )
        return f"connection refused at {base_url}"
    if isinstance(exc, (TimeoutError, SocketTimeout)):
        return f"timeout connecting to {base_url}"
    return text
