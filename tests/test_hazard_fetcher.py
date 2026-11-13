"""HazardFetcher payload size: server-side coordinate rounding, compact JSON, no raw-byte cache."""
import json
import os
from email.message import Message
from unittest import mock
from urllib.error import HTTPError

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from data.fetchers import hazard_fetcher as hf


@pytest.mark.parametrize("url", [
    hf.SPC_URLS["cat"],
    hf.SPC_SIG_URLS["tor"],
    hf._spc_layer_url(19),
    hf.SPC_MD_URL,
    hf.WWA_WARNINGS_URL,
    hf.WWA_WATCHES_URL,
])
def test_queries_request_rounded_geometry(url):
    assert "geometryPrecision=4" in url


def test_empty_fc_json_matches_sentinel():
    assert hf._fc_json([]) == hf._EMPTY_FC_STR
    assert not hf._fc_has_features(hf._fc_json([]))


def test_fc_json_is_compact():
    s = hf._fc_json([{"type": "Feature", "geometry": None, "properties": {"a": 1}}])
    assert ", " not in s and ": " not in s
    assert json.loads(s)["features"][0]["properties"] == {"a": 1}


class _Resp:
    def __init__(self, body, etag="abc"):
        self._body = body
        self.headers = {"ETag": etag}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_raw_304_returns_unchanged_without_keeping_bytes():
    f = hf.HazardFetcher()
    url = hf.SPC_MD_URL
    with mock.patch.object(hf, "urlopen", return_value=_Resp(b'{"features":[]}')):
        raw, changed = f._get_raw(url)
    assert (raw, changed) == (b'{"features":[]}', True)
    assert not hasattr(f, "_raw_cache")

    not_modified = HTTPError(url, 304, "Not Modified", Message(), None)
    with mock.patch.object(hf, "urlopen", side_effect=not_modified) as m:
        raw, changed = f._get_raw(url)
    assert (raw, changed) == (b"", False)
    assert m.call_args[0][0].get_header("If-none-match") == "abc"


def test_spc_mds_cached_compact():
    f = hf.HazardFetcher()
    body = json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[-97.71, 39.922], [-97.5, 39.9], [-97.71, 39.922]]]},
        "properties": {"name": "MD 2101"},
    }]}).encode()
    with mock.patch.object(f, "_get_raw", return_value=(body, True)):
        f._fetch_spc_mds()
    assert ", " not in f._mds_cache
    assert json.loads(f._mds_cache)["features"][0]["properties"]["name"] == "MD 2101"
