from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import ssl
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from socket import timeout as SocketTimeout
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Mozilla/5.0 STORM/1.0"
_CACHE_DIR = Path(tempfile.gettempdir()) / "storm_sfcoa_mbtiles"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


@dataclass(frozen=True)
class SfcoaTime:
    time_id: str
    label: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class SfcoaVariable:
    variable_id: str
    label: str
    long_name: str = ""
    units: str = ""
    group: str = "other"


_SFCOA_VARIABLE_OVERRIDES: dict[str, dict[str, str]] = {
    "tmpc": {"label": "sfc tmp", "group": "thermo", "units": "C"},
    "dwpc": {"label": "sfc dewp", "group": "thermo", "units": "C"},
    "sthe": {"label": "sfc θe", "group": "thermo"},
    "p": {"label": "sfc pres", "group": "thermo"},
    "swnd": {"label": "sfc wind", "group": "thermo"},
    "sbcp": {"label": "sfc cape", "group": "parcel", "units": "J/kg"},
    "sbcn": {"label": "sfc cin", "group": "parcel", "units": "J/kg"},
    "mucp": {"label": "mu cape", "group": "parcel", "units": "J/kg"},
    "mucn": {"label": "mu cin", "group": "parcel", "units": "J/kg"},
    "m1cp": {"label": "ml cape", "group": "parcel", "units": "J/kg"},
    "m1cn": {"label": "ml cin", "group": "parcel", "units": "J/kg"},
    "ml3k": {"label": "0-3 km cape", "group": "parcel", "units": "J/kg"},
    "dncp": {"label": "dcape", "group": "parcel", "units": "J/kg"},
    "lllr": {"label": "0-3 km", "group": "lapse"},
    "lr36": {"label": "3-6 km", "group": "lapse"},
    "lr38": {"label": "3-8 km", "group": "lapse"},
    "rh80": {"label": "800 mb rh", "group": "upper", "units": "%"},
    "rh70": {"label": "700 mb rh", "group": "upper", "units": "%"},
    "rh60": {"label": "600 mb rh", "group": "upper", "units": "%"},
    "h5ht": {"label": "500 mb height", "group": "upper"},
    "pvor": {"label": "vort", "group": "thermo"},
    "vort": {"label": "vort", "group": "thermo"},
    "s1mg": {"label": "0-1 km", "group": "shear"},
    "s3mg": {"label": "0-3 km", "group": "shear"},
    "s6mg": {"label": "0-6 km", "group": "shear"},
    "s8mg": {"label": "0-8 km", "group": "shear"},
    "bmag": {"label": "bulk", "group": "shear"},
    "srh1": {"label": "0-1 km", "group": "helicity"},
    "srh3": {"label": "0-3 km", "group": "helicity"},
    "sigt": {"label": "stp", "group": "composite"},
    "sccp": {"label": "scp", "group": "composite"},
    "wn30": {"label": "300 mb wind", "group": "upper"},
}


class SfcoaOverlayFetcher(QObject):
    """Fetches SFCOA valid-time catalogs and vector-tile metadata."""

    times_ready = pyqtSignal(object)             # list[SfcoaTime]
    variables_ready = pyqtSignal(str, object)    # time_id, list[SfcoaVariable]
    overlay_ready = pyqtSignal(object)           # metadata dict for MapLibre
    fetch_error = pyqtSignal(str)

    def __init__(self, base_url: str, parent=None):
        super().__init__(parent)
        self._base_url = _clean_base_url(base_url)
        self._times: list[SfcoaTime] = []
        self._variables_by_time: dict[str, list[SfcoaVariable]] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    def refresh_catalog(self) -> None:
        threading.Thread(target=self._fetch_times_worker, daemon=True).start()

    def fetch_variables(self, time_id: str) -> None:
        threading.Thread(
            target=self._fetch_variables_worker,
            args=(str(time_id or "").strip(),),
            daemon=True,
        ).start()

    def fetch_overlay(self, variable_id: str, time_id: str) -> None:
        threading.Thread(
            target=self._fetch_overlay_worker,
            args=(str(variable_id or "").strip(), str(time_id or "").strip()),
            daemon=True,
        ).start()

    def _fetch_times_worker(self) -> None:
        try:
            times = _times_from_index(self._base_url)
            if not times:
                times = _times_from_catalog(self._base_url)
            if not times:
                times = _times_from_recent_catalog_probes(self._base_url)
            self._times = sorted(times, key=lambda item: item.time_id)
            self.times_ready.emit(self._times)
        except Exception as exc:
            log.warning("SFCOA catalog fetch failed: %s", exc)
            self.fetch_error.emit(f"SFCOA catalog: {_friendly_error(exc, self._base_url)}")

    def _fetch_variables_worker(self, time_id: str) -> None:
        if not time_id:
            return
        try:
            variables = _variables_from_time_index(self._base_url, time_id)
            if not variables:
                metadata_variables = _variables_from_shared_metadata(self._base_url, time_id)
                catalog_variables = _variables_from_time_catalog(self._base_url, time_id)
                if metadata_variables and catalog_variables:
                    available = {var.variable_id for var in catalog_variables}
                    variables = [
                        var for var in metadata_variables
                        if var.variable_id in available
                    ]
                else:
                    variables = metadata_variables or catalog_variables
            if not variables:
                variables = _variables_from_time_catalog(self._base_url, time_id)
            self._variables_by_time[time_id] = variables
            self.variables_ready.emit(time_id, variables)
        except Exception as exc:
            log.warning("SFCOA variable fetch failed: %s", exc)
            self.fetch_error.emit(f"SFCOA {time_id}: {_friendly_error(exc, self._base_url)}")

    def _fetch_overlay_worker(self, variable_id: str, time_id: str) -> None:
        if not variable_id or not time_id:
            return
        try:
            variable_id = _clean_variable_id(variable_id)
            time_base = _abs_url(self._base_url, f"{time_id}/")
            metadata = _fetch_json(_abs_url(time_base, "metadata.json"))
            variable_meta = {}
            variables = metadata.get("variables")
            if isinstance(variables, dict) and isinstance(variables.get(variable_id), dict):
                variable_meta = dict(variables[variable_id])
            variable = _variable_from_metadata(variable_id, variable_meta)
            mbtiles_url = _abs_url(time_base, f"{variable_id}.mbtiles")
            mbtiles_path = _download_mbtiles(mbtiles_url, time_id, variable_id)
            mbtiles_metadata = _read_mbtiles_metadata(mbtiles_path)

            bounds = _parse_bounds(
                mbtiles_metadata.get("antimeridian_adjusted_bounds")
                or mbtiles_metadata.get("bounds")
                or metadata.get("bounds")
                or metadata.get("bbox")
            )
            source_layer = _source_layer(mbtiles_metadata, variable_id, time_id)
            tile_key = _sfcoa_tile_key(time_id, variable_id)
            tile_url = f"storm://app/sfcoatiles/{tile_key}/{{z}}/{{x}}/{{y}}.pbf"
            valid_time = str(metadata.get("valid_time") or _time_iso(time_id) or "")
            if len(bounds) != 4 or not source_layer:
                raise RuntimeError("metadata missing bounds or vector layer")

            metadata.update({
                "product": variable_id,
                "variable": variable_id,
                "label": variable.label,
                "long_name": variable.long_name,
                "units": variable.units or variable_meta.get("units") or "",
                "label_units": variable_meta.get("label_units") or variable.units or variable_meta.get("units") or "",
                "group": variable.group,
                "time": time_id,
                "time_label": _time_label(time_id),
                "valid_time": valid_time,
                "tile_url": tile_url,
                "tile_key": tile_key,
                "mbtiles_url": mbtiles_url,
                "mbtiles_path": str(mbtiles_path),
                "source_layer": source_layer,
                "bounds": bounds,
                "minzoom": int(mbtiles_metadata.get("minzoom") or metadata.get("minzoom") or 0),
                "maxzoom": int(mbtiles_metadata.get("maxzoom") or metadata.get("maxzoom") or 8),
            })
            self.overlay_ready.emit(metadata)
        except Exception as exc:
            log.warning("SFCOA overlay fetch failed: %s", exc)
            self.fetch_error.emit(f"SFCOA {variable_id}: {_friendly_error(exc, self._base_url)}")


def _clean_base_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("SFCOA base URL is empty")
    return base


def _abs_url(base_url: str, path: str) -> str:
    path = str(path or "").strip()
    if path.startswith(("http://", "https://", "file://")):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _catalog_url(base_url: str, *parts: str) -> str:
    base = base_url.rstrip("/")
    if "/fileServer/" in base:
        base = base.replace("/fileServer/", "/catalog/", 1)
    else:
        base = base.replace("/thredds/files/", "/thredds/catalog/", 1)
    for part in parts:
        base = f"{base}/{str(part).strip('/')}"
    return f"{base}/catalog.xml"


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


def _maybe_fetch_json(url: str) -> dict:
    try:
        return _fetch_json(url)
    except HTTPError as exc:
        if exc.code in (404, 410):
            return {}
        raise


def _times_from_index(base_url: str) -> list[SfcoaTime]:
    payload = _maybe_fetch_json(_abs_url(base_url, "index.json"))
    if not payload:
        payload = _maybe_fetch_json(_abs_url(base_url, "latest.json"))
    values = payload.get("times") or []
    if not values and payload.get("latest"):
        values = [payload["latest"]]
    return [_sfcoa_time(str(value)) for value in values if _is_time_id(str(value))]


def _times_from_catalog(base_url: str) -> list[SfcoaTime]:
    root = ET.fromstring(_fetch_text(_catalog_url(base_url)))
    times: list[SfcoaTime] = []
    for ref in root.findall(".//{*}catalogRef"):
        name = str(
            ref.get("name")
            or ref.get("{http://www.w3.org/1999/xlink}title")
            or ""
        ).strip()
        if _is_time_id(name):
            times.append(_sfcoa_time(name))
    return times


def _times_from_recent_catalog_probes(base_url: str, hours_back: int = 24) -> list[SfcoaTime]:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times: list[SfcoaTime] = []
    for offset in range(max(1, int(hours_back)) + 1):
        candidate = now - timedelta(hours=offset)
        time_id = candidate.strftime("%Y%m%d_%H")
        if _time_catalog_exists(base_url, time_id):
            times.append(_sfcoa_time(time_id))
    return times


def _time_catalog_exists(base_url: str, time_id: str) -> bool:
    try:
        text = _fetch_text(_catalog_url(base_url, time_id))
    except HTTPError as exc:
        if exc.code in (400, 404, 410):
            return False
        raise
    except (URLError, TimeoutError, SocketTimeout):
        raise
    return "TDS Catalog" in text or "<catalog" in text


def _variables_from_time_index(base_url: str, time_id: str) -> list[SfcoaVariable]:
    payload = _maybe_fetch_json(_abs_url(base_url, f"{time_id}/index.json"))
    values = payload.get("variables") if payload else None
    if isinstance(values, dict):
        return [
            _variable_from_metadata(var_id, meta if isinstance(meta, dict) else {})
            for var_id, meta in values.items()
        ]
    if isinstance(values, list):
        return [_variable_from_metadata(str(var_id), {}) for var_id in values]
    return []


def _variables_from_shared_metadata(base_url: str, time_id: str) -> list[SfcoaVariable]:
    payload = _maybe_fetch_json(_abs_url(base_url, f"{time_id}/metadata.json"))
    values = payload.get("variables") if payload else None
    if not isinstance(values, dict):
        return []
    variables = [
        _variable_from_metadata(_clean_variable_id(var_id), meta if isinstance(meta, dict) else {})
        for var_id, meta in values.items()
    ]
    return sorted(variables, key=lambda item: (item.group, item.label, item.variable_id))


def _variables_from_time_catalog(base_url: str, time_id: str) -> list[SfcoaVariable]:
    root = ET.fromstring(_fetch_text(_catalog_url(base_url, time_id)))
    variable_ids: set[str] = set()
    for ref in root.findall(".//{*}catalogRef"):
        name = str(
            ref.get("name")
            or ref.get("{http://www.w3.org/1999/xlink}title")
            or ""
        ).strip().strip("/")
        if name and not _is_time_id(name):
            variable_ids.add(name)
    for ds in root.findall(".//{*}dataset"):
        url_path = str(ds.get("urlPath") or "")
        match = re.search(rf"/?{re.escape(time_id)}/([^/]+)/", url_path)
        if match:
            variable_ids.add(_clean_variable_id(match.group(1)))
            continue
        name = str(ds.get("name") or "").strip().strip("/")
        if name and "/" not in name and name not in {"metadata.json", "index.json"}:
            variable_ids.add(_clean_variable_id(name))
    variables = [_variable_from_metadata(var_id, {}) for var_id in variable_ids]
    return sorted(variables, key=lambda item: (item.group, item.label, item.variable_id))


def _variable_from_metadata(variable_id: str, metadata: dict) -> SfcoaVariable:
    var_id = str(variable_id or "").strip()
    override = _SFCOA_VARIABLE_OVERRIDES.get(var_id.lower(), {})
    label = str(metadata.get("label") or metadata.get("title") or override.get("label") or _default_label(var_id))
    long_name = str(metadata.get("long_name") or metadata.get("description") or override.get("long_name") or "")
    units = str(metadata.get("units") or metadata.get("label_units") or override.get("units") or "")
    group = str(metadata.get("group") or override.get("group") or _variable_group(var_id, label, long_name))
    return SfcoaVariable(var_id, label, long_name, units, group)


def _source_layer(metadata: dict, variable_id: str, time_id: str) -> str:
    value = metadata.get("source_layer") or metadata.get("source-layer")
    if value:
        return str(value)
    try:
        tile_json = json.loads(str(metadata.get("json") or "{}"))
        layers = tile_json.get("vector_layers") or []
        if layers and layers[0].get("id"):
            return str(layers[0]["id"])
    except Exception:
        pass
    return f"{variable_id}_{time_id}"


def _clean_variable_id(value: str) -> str:
    var_id = str(value or "").strip().strip("/")
    if var_id.endswith(".mbtiles"):
        var_id = var_id[:-len(".mbtiles")]
    return var_id


def _sfcoa_tile_key(time_id: str, variable_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{time_id}-{variable_id}").strip("-")
    return safe or "sfcoa"


def _download_mbtiles(url: str, time_id: str, variable_id: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{_sfcoa_tile_key(time_id, variable_id)}.mbtiles"
    if path.exists() and path.stat().st_size > 0:
        return path

    tmp_path = path.with_suffix(".tmp")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CTX) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError(f"empty MBTiles file from {url}")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    os.replace(tmp_path, path)
    return path


def _read_mbtiles_metadata(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, value FROM metadata")
        values = {str(name): str(value) for name, value in cursor.fetchall()}
    finally:
        conn.close()
    return values


def _parse_bounds(bounds_value: object) -> list[float]:
    if isinstance(bounds_value, (list, tuple)) and len(bounds_value) == 4:
        return [float(v) for v in bounds_value]
    parts = str(bounds_value or "").split(",")
    if len(parts) != 4:
        return []
    return [float(part.strip()) for part in parts]


def _is_time_id(value: str) -> bool:
    return re.fullmatch(r"\d{8}_\d{2}", str(value or "")) is not None


def _sfcoa_time(time_id: str) -> SfcoaTime:
    return SfcoaTime(time_id=time_id, label=_time_label(time_id), timestamp=_parse_time_id(time_id))


def _parse_time_id(time_id: str) -> datetime | None:
    try:
        return datetime.strptime(time_id, "%Y%m%d_%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _time_label(time_id: str) -> str:
    ts = _parse_time_id(time_id)
    if ts is None:
        return time_id
    return ts.strftime("%H:%MZ")


def _time_iso(time_id: str) -> str:
    ts = _parse_time_id(time_id)
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_label(variable_id: str) -> str:
    override = _SFCOA_VARIABLE_OVERRIDES.get(variable_id.lower())
    if override and override.get("label"):
        return str(override["label"])
    labels = {
        "temp": "Temp",
        "tmpc": "Temp",
        "dwpt": "Dewpoint",
        "dwpc": "Dewpoint",
        "mslp": "MSLP",
        "pmsl": "MSLP",
        "sfccape": "SFC CAPE",
        "sbcp": "SFC CAPE",
        "sfccinh": "SFC CINH",
        "sbcn": "SFC CINH",
        "mlcape": "ML CAPE",
        "mlcp": "ML CAPE",
        "mlcinh": "ML CINH",
        "mlcn": "ML CINH",
        "mucape": "MU CAPE",
        "mucp": "MU CAPE",
        "mucinh": "MU CINH",
        "mucn": "MU CINH",
        "srh01": "0-1 SRH",
        "srh1": "0-1 SRH",
        "srh03": "0-3 SRH",
        "srh3": "0-3 SRH",
        "stpc": "STP",
        "stp": "STP",
        "scp": "SCP",
    }
    key = variable_id.lower()
    return labels.get(key, variable_id.upper())


def _variable_group(variable_id: str, label: str, long_name: str) -> str:
    override = _SFCOA_VARIABLE_OVERRIDES.get(variable_id.lower())
    if override and override.get("group"):
        return str(override["group"])
    text = f"{variable_id} {label} {long_name}".lower()
    if any(token in text for token in ("temp", "tmp", "dew", "dwpt", "dwpc", "mslp", "pmsl")):
        return "thermo"
    if any(token in text for token in ("sfc", "surface", "sbcp", "sbcn")):
        return "parcel"
    if any(token in text for token in ("ml", "mixed", "mu", "unstable")):
        return "parcel"
    if "lapse" in text or "lr" in text:
        return "lapse"
    if "shear" in text:
        return "shear"
    if "srh" in text or "helicity" in text:
        return "helicity"
    if any(token in text for token in ("stp", "scp", "sig", "supercell")):
        return "composite"
    if any(token in text for token in ("rh", "500", "700", "600", "800", "height")):
        return "upper"
    if any(token in text for token in ("wind", "storm")):
        return "kinematic"
    return "other"


def _friendly_error(exc: Exception, base_url: str) -> str:
    text = str(exc)
    reason = getattr(exc, "reason", None)
    if isinstance(exc, URLError) and reason:
        text = str(reason)
    if isinstance(exc, (TimeoutError, SocketTimeout)):
        return f"timeout connecting to {base_url}"
    return text
