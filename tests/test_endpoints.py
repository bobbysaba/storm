"""data/endpoints.py and data/thredds_paths.py hold the live viewer's data
addresses (providers/nssl/endpoints.py and paths.py NSSL's); the fetchers'
URLs are built from them."""
import ast
import re
from pathlib import Path

import providers.nssl as nssl
from data import endpoints
from data import thredds_paths as paths
from providers.nssl import endpoints as nssl_endpoints
from providers.nssl import paths as nssl_paths

ROOT = Path(__file__).resolve().parents[1]


def test_live_fetcher_urls_are_unchanged():
    from data.fetchers import (hazard_fetcher, radar_fetcher, surface_fetcher, vad_fetcher)
    assert surface_fetcher.IEM_CURRENTS_URL == "https://mesonet.agron.iastate.edu/api/1/currents.json"
    assert radar_fetcher.THREDDS_CATALOG_ROOT == "https://thredds.ucar.edu/thredds/catalog/nexrad/level3"
    assert radar_fetcher.THREDDS_FILESERVER_ROOT == "https://thredds.ucar.edu/thredds/fileServer"
    assert vad_fetcher.THREDDS_FILE == "https://thredds.ucar.edu/thredds/fileServer/nexrad/level3/NVW"
    assert hazard_fetcher.WWA_WARNINGS_URL.startswith(
        "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer/0/query?")
    assert hazard_fetcher.SPC_MD_URL.startswith(
        "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/spc_mesoscale_discussion/MapServer/0/query?")


def test_nssl_api_joins_paths_with_one_slash():
    url = nssl_endpoints.api_url("/data/sonde/index.json")
    assert url == f"{nssl.API_ROOT.rstrip('/')}/data/sonde/index.json"
    assert "//data" not in url


def test_thredds_helpers():
    assert paths.catalog_url("/nexrad/level3/", paths.UCAR) == \
        "https://thredds.ucar.edu/thredds/catalog/nexrad/level3/catalog.html"
    assert nssl_paths.file_url("FOFS/x.txt") == "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/x.txt"
    assert nssl_paths.catalog_url("FOFS") == paths.catalog_url("FOFS", nssl_paths.NSSL)


def test_checker_lists_are_well_formed():
    checks = endpoints.all_endpoints() + nssl_endpoints.all_endpoints()
    assert len({what for what, _ in checks}) == len(checks)          # names are unique
    assert all(url.startswith("https://") for _, url in checks)
    assert not any("nssl" in url for _, url in endpoints.all_endpoints())
    for what, host, path, required in paths.all_folders():
        assert host == paths.UCAR and not path.startswith("/") and isinstance(required, bool)
    for what, host, path, required in nssl_paths.all_folders():
        assert host == nssl_paths.NSSL and not path.startswith("/") and isinstance(required, bool)


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
    for folder in ("data/fetchers", "ui", "core", "providers/nssl/live"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            for line, text in _code_strings(path):
                if web.search(text) and not namespace.search(text):
                    offenders.append(f"{path.relative_to(ROOT)}:{line}: {text[:70]}")
    assert not offenders, "\n".join(offenders)


def test_the_generic_layers_never_import_a_provider():
    """data/, core/ and network/ work for any team; providers/ builds on them,
    not the other way round."""
    offenders = []
    for folder in ("data", "core", "network", "settings"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                offenders += [f"{path.relative_to(ROOT)}: {n}" for n in names if n.split(".")[0] == "providers"]
    assert not offenders, "\n".join(offenders)
