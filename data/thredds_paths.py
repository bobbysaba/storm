"""Where STORM's data lives on THREDDS: hosts and folders, in one place.

When a THREDDS tree is reorganized (a folder renamed or moved), change it
here; fetchers build their URLs from this file. NSSL's folders (FOFS, CLAMPS)
are in providers/nssl/paths.py. Run `python scripts/check_data_sources.py`
to see which of these locations still answer, and, for any that don't, what
their parent folder now holds.

Paths are relative to a THREDDS server's root, as they appear in its URLs:
    <host>/catalog/<path>/catalog.html      a folder's listing
    <host>/fileServer/<path>/<file>         a file
"""
from __future__ import annotations

# ---- servers -------------------------------------------------------------------
UCAR = "https://thredds.ucar.edu/thredds"        # Unidata: live NEXRAD Level 3, VAD
THREDDS_NS = "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"   # catalog.xml namespace


def catalog_root(host: str) -> str:
    """e.g. https://thredds.ucar.edu/thredds/catalog"""
    return f"{host}/catalog"


def file_root(host: str) -> str:
    """e.g. https://thredds.ucar.edu/thredds/fileServer"""
    return f"{host}/fileServer"


def catalog_url(path: str, host: str, page: str = "catalog.html") -> str:
    """A folder's listing: <host>/catalog/<path>/catalog.html (or catalog.xml)."""
    return f"{catalog_root(host)}/{path.strip('/')}/{page}"


def file_url(path: str, host: str) -> str:
    """A file: <host>/fileServer/<path>."""
    return f"{file_root(host)}/{path.strip('/')}"


# ---- UCAR (live mode) ---------------------------------------------------------------
UCAR_NEXRAD_LEVEL3 = "nexrad/level3"                    # <product>/<site>/<YYYYMMDD>/


def all_folders() -> list[tuple[str, str, str, bool]]:
    """(what, host, path, required) for every folder STORM reads outside the
    providers, for the checker (scripts/check_data_sources.py) and the tests."""
    return [("UCAR NEXRAD Level 3 (live)", UCAR, UCAR_NEXRAD_LEVEL3, True)]
