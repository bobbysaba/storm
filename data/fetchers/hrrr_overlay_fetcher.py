from __future__ import annotations

import json
import logging
import re
import ssl
import threading
from dataclasses import dataclass
from socket import timeout as SocketTimeout
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 STORM/1.0"

# NSSL THREDDS currently presents a certificate chain Python may reject. Match
# the existing NSSL soundings/current.json behavior elsewhere in the app.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


@dataclass(frozen=True)
class HrrrField:
    field_id: str
    label: str
    latest_url: str = ""


@dataclass(frozen=True)
class HrrrRun:
    run_id: str
    label: str


class HrrrOverlayFetcher(QObject):
    """Fetches static HRRR overlay manifests generated server-side.

    The server side publishes plain JSON + image files, usually through a
    THREDDS fileServer path. This fetcher only follows those JSON pointers and
    emits image metadata for MapLibre; it never decodes GRIB locally.
    """

    runs_ready = pyqtSignal(object)             # list[HrrrRun]
    catalog_ready = pyqtSignal(object)          # list[HrrrField]
    field_ready = pyqtSignal(str, object)       # field_id, field latest payload
    overlay_ready = pyqtSignal(object)          # metadata dict with absolute image URLs
    fetch_error = pyqtSignal(str)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self._base_url = _clean_base_url(base_url)
        self._catalog: dict | None = None
        self._runs: list[HrrrRun] = []
        self._run_hours: dict[str, list[int]] = {}
        self._selected_run: str = ""
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

    def set_run(self, run_id: str) -> None:
        run = str(run_id or "").strip()
        if run and run != self._selected_run:
            self._selected_run = run
            self._field_latest.clear()

    def _fetch_catalog_worker(self) -> None:
        try:
            catalog_url = urljoin(self._base_url + "/", "latest.json")
            catalog = _fetch_json(catalog_url)
            runs, run_hours = _fetch_runs(self._base_url, catalog)
            if runs:
                self._runs = runs
                self._run_hours = run_hours
                if not self._selected_run or self._selected_run not in {
                    run.run_id for run in runs
                }:
                    self._selected_run = str(catalog.get("run") or runs[0].run_id)
                self.runs_ready.emit(runs)

            fields = []
            for field_id, item in sorted((catalog.get("fields") or {}).items()):
                if isinstance(item, dict):
                    label = _short_label(str(item.get("label") or field_id))
                    latest_url = _abs_url(self._base_url, item.get("latest", ""))
                else:
                    label = _short_label(str(item or field_id))
                    latest_url = ""
                fields.append(HrrrField(
                    field_id=str(field_id),
                    label=label,
                    latest_url=latest_url,
                ))
            self._catalog = catalog
            self.catalog_ready.emit(fields)
        except Exception as exc:
            log.warning("HRRR catalog fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR catalog: {_friendly_error(exc, self._base_url)}")

    def _fetch_field_worker(self, field_id: str) -> None:
        try:
            payload = self._field_payload(field_id)
            self._field_latest[field_id] = payload
            self.field_ready.emit(field_id, payload)
        except Exception as exc:
            log.warning("HRRR field fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR {field_id}: {_friendly_error(exc, self._base_url)}")

    def _fetch_overlay_worker(self, field_id: str, forecast_hour: int) -> None:
        try:
            field_payload = self._field_latest.get(field_id)
            if field_payload is None:
                field_payload = self._field_payload(field_id)
                self._field_latest[field_id] = field_payload

            selected_item = None
            for item in field_payload.get("items", []):
                if int(item.get("forecast_hour", -1)) == int(forecast_hour):
                    selected_item = item
                    break
            if not selected_item or not selected_item.get("metadata"):
                raise RuntimeError(f"forecast hour f{forecast_hour:02d} not found")

            forecast_metadata_url = _abs_url(self._base_url, str(selected_item["metadata"]))
            forecast_metadata = _fetch_json(forecast_metadata_url)

            tile_metadata_url = _abs_url(self._base_url, str(selected_item["tile_metadata"]))
            tile_metadata = _fetch_json(tile_metadata_url)

            field_meta = (forecast_metadata.get("fields") or {}).get(field_id) or {}
            if not isinstance(field_meta, dict):
                field_meta = {}

            source_layer = _source_layer(
                tile_metadata,
                field_id,
                str(field_payload.get("run") or self._selected_run or ""),
                int(forecast_hour),
            )
            bounds = _parse_bounds(
                tile_metadata.get("antimeridian_adjusted_bounds")
                or tile_metadata.get("bounds")
                or forecast_metadata.get("bbox")
            )
            if len(bounds) != 4 or not source_layer:
                raise RuntimeError("metadata missing bounds or vector layer")

            metadata = dict(tile_metadata)
            metadata.update({
                "forecast_metadata_url": forecast_metadata_url,
                "metadata_url": tile_metadata_url,
                "field": field_id,
                "label": _short_label(field_meta.get("label", metadata.get("label", field_id))),
                "units": field_meta.get("units", metadata.get("units")),
                "level": field_meta.get("level", metadata.get("level")),
                "legend": field_meta.get("legend", metadata.get("legend")),
                "vmin": field_meta.get("vmin", metadata.get("vmin")),
                "vmax": field_meta.get("vmax", metadata.get("vmax")),
                "run": field_payload.get("run") or self._selected_run,
                "cycle_time": forecast_metadata.get("cycle_time") or field_payload.get("cycle_time"),
                "forecast_hour": int(forecast_metadata.get("forecast_hour", forecast_hour)),
                "valid_time": forecast_metadata.get("valid_time"),
                "tile_url": _abs_url(self._base_url, str(selected_item["tile_url"])),
                "source_layer": source_layer,
                "bounds": bounds,
                "bbox": _parse_bounds(forecast_metadata.get("bbox")) or bounds,
                "minzoom": int(metadata.get("minzoom") or 0),
                "maxzoom": int(metadata.get("maxzoom") or 8),
            })
            self.overlay_ready.emit(metadata)
        except Exception as exc:
            log.warning("HRRR overlay fetch failed: %s", exc)
            self.fetch_error.emit(f"HRRR overlay: {_friendly_error(exc, self._base_url)}")

    def _field_payload(self, field_id: str) -> dict:
        if not self._catalog:
            self._catalog = _fetch_json(urljoin(self._base_url + "/", "latest.json"))
        latest_url = self._field_latest_url(field_id)
        if latest_url:
            return _fetch_json(latest_url)
        return _payload_from_catalog(
            self._catalog,
            field_id,
            self._selected_run or str(self._catalog.get("run") or ""),
            self._run_hours,
        )

    def _field_latest_url(self, field_id: str) -> str:
        if self._catalog:
            item = (self._catalog.get("fields") or {}).get(field_id)
            if isinstance(item, dict) and item.get("latest"):
                return _abs_url(self._base_url, item["latest"])
            if "run" in self._catalog and "forecast_hours" in self._catalog:
                return ""
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


def _payload_from_catalog(
    catalog: dict,
    field_id: str,
    selected_run: str | None = None,
    run_hours: dict[str, list[int]] | None = None,
) -> dict:
    fields = catalog.get("fields") or {}
    if field_id not in fields:
        raise RuntimeError(f"unknown HRRR field: {field_id}")

    run = str(selected_run or catalog.get("run") or "").strip()
    if not run:
        raise RuntimeError("HRRR catalog missing run")

    hours = (run_hours or {}).get(run) or catalog.get("forecast_hours") or []
    items = []
    for hour in hours:
        hour_int = int(hour)
        hour_dir = f"{run}/f{hour_int:02d}"
        items.append({
            "forecast_hour": hour_int,
            "metadata": f"{hour_dir}/metadata.json",
            "tile_metadata": f"{hour_dir}/{field_id}/metadata.json",
            "tile_url": f"{hour_dir}/{field_id}/{{z}}/{{x}}/{{y}}.pbf",
        })

    return {
        "cycle": _cycle_label(_cycle_time_for_run(run, catalog)),
        "cycle_time": _cycle_time_for_run(run, catalog),
        "field": field_id,
        "label": _short_label(fields[field_id] if not isinstance(fields[field_id], dict)
                              else fields[field_id].get("label", field_id)),
        "run": run,
        "items": items,
    }


def _fetch_runs(base_url: str, catalog: dict) -> tuple[list[HrrrRun], dict[str, list[int]]]:
    runs: list[HrrrRun] = []
    run_hours: dict[str, list[int]] = {}

    try:
        root_xml = _fetch_text(_catalog_url(base_url))
        root = ET.fromstring(root_xml)
        for ref in root.findall(".//{*}catalogRef"):
            run_id = str(
                ref.get("name")
                or ref.get("{http://www.w3.org/1999/xlink}title")
                or ""
            ).strip()
            if not re.fullmatch(r"\d{8}_\d{2}", run_id):
                continue
            runs.append(HrrrRun(run_id, _run_label(run_id)))
    except Exception as exc:
        log.debug("HRRR run catalog unavailable: %s", exc)

    if not runs and catalog.get("run"):
        run_id = str(catalog["run"])
        runs.append(HrrrRun(run_id, _run_label(run_id)))

    latest_run = str(catalog.get("run") or "")
    runs.sort(key=lambda run: run.run_id, reverse=True)
    if latest_run:
        runs.sort(key=lambda run: run.run_id != latest_run)

    for run in runs:
        try:
            run_xml = _fetch_text(_catalog_url(base_url, run.run_id))
            root = ET.fromstring(run_xml)
            hours: list[int] = []
            for ref in root.findall(".//{*}catalogRef"):
                name = str(
                    ref.get("name")
                    or ref.get("{http://www.w3.org/1999/xlink}title")
                    or ""
                )
                match = re.fullmatch(r"f(\d{2})", name)
                if match:
                    hours.append(int(match.group(1)))
            if hours:
                run_hours[run.run_id] = sorted(hours)
        except Exception as exc:
            log.debug("HRRR run-hour catalog unavailable for %s: %s", run.run_id, exc)

    if latest_run and latest_run not in run_hours:
        run_hours[latest_run] = [int(h) for h in catalog.get("forecast_hours") or []]

    return runs, run_hours


def _catalog_url(base_url: str, *parts: str) -> str:
    base = base_url.rstrip("/")
    if "/fileServer/" in base:
        base = base.replace("/fileServer/", "/catalog/", 1)
    else:
        base = base.replace("/thredds/files/", "/thredds/catalog/", 1)
    for part in parts:
        base = f"{base}/{str(part).strip('/')}"
    return f"{base}/catalog.xml"


def _cycle_time_for_run(run: str, catalog: dict) -> str:
    if run == str(catalog.get("run") or "") and catalog.get("cycle_time"):
        return str(catalog["cycle_time"])
    try:
        dt = datetime.strptime(run, "%Y%m%d_%H").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:00:00Z")
    except ValueError:
        return str(catalog.get("cycle_time") or "")


def _cycle_label(cycle_time: object) -> str:
    text = str(cycle_time or "")
    if "T" in text:
        try:
            return text.split("T", 1)[1][:2]
        except IndexError:
            pass
    return "--"


def _run_label(run_id: str) -> str:
    try:
        dt = datetime.strptime(run_id, "%Y%m%d_%H")
        return f"{dt.strftime('%b')} {dt.day} {dt.strftime('%HZ')}"
    except ValueError:
        return run_id


def _short_label(label: object) -> str:
    return str(label or "").replace("Updraft Helicity", "UH").replace("updraft helicity", "UH")


def _parse_bounds(bounds_value: object) -> list[float]:
    if isinstance(bounds_value, (list, tuple)) and len(bounds_value) == 4:
        return [float(v) for v in bounds_value]
    parts = str(bounds_value or "").split(",")
    if len(parts) != 4:
        return []
    return [float(part.strip()) for part in parts]


def _source_layer(metadata: dict, field_id: str, run_id: str, forecast_hour: int) -> str:
    try:
        tile_json = json.loads(str(metadata.get("json") or "{}"))
        layers = tile_json.get("vector_layers") or []
        if layers and layers[0].get("id"):
            return str(layers[0]["id"])
    except Exception:
        pass
    return f"{field_id}_{run_id}_f{int(forecast_hour):02d}"


def _fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CTX) as resp:
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
