"""The NSSL API's paths (root: providers.nssl.API_ROOT, which the
NSSL_API_ROOT environment variable can override). Every request needs
NSSL_API_KEY; see providers/nssl/__init__.py. THREDDS folders are in
providers/nssl/paths.py.
"""
from __future__ import annotations

import providers.nssl as nssl

CURRENT_OBS = "current.json"
SONDE_INDEX = "data/sonde/index.json"                       # CLAMPS / lidar-truck soundings
# the regional mesonets are [[sources]] in profiles/nssl.toml


def api_url(path: str) -> str:
    """A full NSSL API URL for one of the paths above."""
    return f"{nssl.API_ROOT.rstrip('/')}/{path.lstrip('/')}"


def all_endpoints() -> list[tuple[str, str]]:
    """(what, URL to probe) for scripts/check_data_sources.py."""
    return [
        ("NSSL API current obs", api_url(CURRENT_OBS)),
        ("NSSL API sonde index", api_url(SONDE_INDEX)),
    ]
