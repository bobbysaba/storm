"""data/endpoints.py and data/thredds_paths.py hold the live viewer's data
addresses; the fetchers' URLs are built from them."""
import ast
import re
from pathlib import Path

import config
from data import endpoints
from data import thredds_paths as paths

ROOT = Path(__file__).resolve().parents[1]


def test_live_fetcher_urls_are_unchanged():
    from data.fetchers import (hazard_fetcher, radar_fetcher, surface_fetcher, vad_fetcher)
    api = config.NSSL_API_ROOT.rstrip("/")
    assert surface_fetcher.OK_API_URL == f"{api}/data/mesonet/ok_mesonet.json"
    assert surface_fetcher.CO_META_URL == f"{api}/data/mesonet/co_metadata.json"
    assert surface_fetcher.IEM_CURRENTS_URL == "https://mesonet.agron.iastate.edu/api/1/currents.json"
    assert radar_fetcher.THREDDS_CATALOG_ROOT == "https://thredds.ucar.edu/thredds/catalog/nexrad/level3"
    assert radar_fetcher.THREDDS_FILESERVER_ROOT == "https://thredds.ucar.edu/thredds/fileServer"
    assert vad_fetcher.THREDDS_FILE == "https://thredds.ucar.edu/thredds/fileServer/nexrad/level3/NVW"
    assert hazard_fetcher.WWA_WARNINGS_URL.startswith(
        "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer/0/query?")
    assert hazard_fetcher.SPC_MD_URL.startswith(
        "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/spc_mesoscale_discussion/MapServer/0/query?")


def test_nssl_api_joins_paths_with_one_slash():
    url = endpoints.nssl_api("/data/sonde/index.json")
    assert url == f"{config.NSSL_API_ROOT.rstrip('/')}/data/sonde/index.json"
    assert "//data" not in url


def test_thredds_helpers():
    assert paths.catalog_url("/nexrad/level3/", paths.UCAR) == \
        "https://thredds.ucar.edu/thredds/catalog/nexrad/level3/catalog.html"
    assert paths.file_url("FOFS/x.txt") == "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/x.txt"


def test_checker_lists_are_well_formed():
    checks = endpoints.all_endpoints()
    assert len({what for what, _ in checks}) == len(checks)          # names are unique
    assert all(url.startswith("https://") for _, url in checks)
    for what, host, path, required in paths.all_folders():
        assert host in (paths.NSSL, paths.UCAR) and not path.startswith("/") and isinstance(required, bool)


def _code_strings(path: Path):
    """Every string in the code itself, not docstrings."""
    tree = ast.parse(path.read_text(errors="replace"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def test_live_code_writes_no_data_address_of_its_own():
    # archive/ follows when archive mode is reworked; namespaces aren't addresses
    namespace = re.compile(r"https?://(?:www\.w3\.org|www\.unidata\.ucar\.edu/namespaces)")
    web = re.compile(r"https?://[A-Za-z0-9]")
    offenders = []
    for folder in ("data/fetchers", "ui", "core"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            for line, text in _code_strings(path):
                if web.search(text) and not namespace.search(text):
                    offenders.append(f"{path.relative_to(ROOT)}:{line}: {text[:70]}")
    assert not offenders, "\n".join(offenders)
