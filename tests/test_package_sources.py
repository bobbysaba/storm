"""Archive downloads go through core/package_sources.py: an opened case
package first, provenance recorded only in ARCHIVE, plain downloads otherwise."""
import hashlib
import io
import threading
import zipfile

import pytest

from core import package_sources, provenance
from ui.app import main_window_case


@pytest.fixture(autouse=True)
def clean():
    provenance.reset(active=True)
    package_sources.deactivate()
    yield
    package_sources.deactivate()
    provenance.reset(active=False)


class _Resp:
    def __init__(self, content=b"", status=200, url=None):
        self.content, self.status_code, self.url = content, status, url


def _package(tmp_path, files: dict[str, bytes]):
    path = tmp_path / "case.zip"
    entries = []
    with zipfile.ZipFile(path, "w") as z:
        for i, (url, data) in enumerate(files.items()):
            member = f"data/radar/{i}"
            z.writestr(member, data)
            entries.append({"url": url, "member": member, "kind": "radar",
                            "sha256": hashlib.sha256(data).hexdigest()})
    return path, entries


def test_live_modes_record_nothing():
    provenance.reset(active=False)
    provenance.record("soundings", "https://iem/raob", b"x")
    assert provenance.snapshot() == [] and provenance.kept_bytes("https://iem/raob") is None


def test_requests_get_records_successes_only(monkeypatch):
    import requests
    replies = {"https://ok": _Resp(b"good"), "https://missing": _Resp(b"nope", status=404)}
    monkeypatch.setattr(requests, "get", lambda url, **kw: replies[url])
    package_sources.requests_get("hazards", "https://ok", timeout=5)
    package_sources.requests_get("hazards", "https://missing", timeout=5)
    (entry,) = provenance.snapshot()
    assert entry["url"] == "https://ok" and entry["kind"] == "hazards" and entry["bytes"] == 4
    assert provenance.kept_bytes("https://ok") == b"good"


def test_query_parameters_are_part_of_the_url(monkeypatch, tmp_path):
    import requests
    url = package_sources.canonical_url("https://iem/sbw.py", {"ts": "202404272000"})
    path, entries = _package(tmp_path, {url: b"{}"})
    package_sources.activate(path, entries)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("went to the network"))
    resp = package_sources.requests_get("hazards", "https://iem/sbw.py", params={"ts": "202404272000"})
    assert resp.status_code == 200 and resp.json() == {} and resp.text == "{}"
    assert package_sources.served()["radar"] == 1
    assert provenance.snapshot()[0]["source"] == "case package"


def test_read_url_serves_the_package_then_falls_back(tmp_path):
    path, entries = _package(tmp_path, {"https://t/a": b"A"})
    package_sources.activate(path, entries)
    opened = []

    class Opener:
        def __init__(self, request, **kwargs):
            opened.append(request)

        def __enter__(self):
            return io.BytesIO(b"B")

        def __exit__(self, *exc):
            return False
    assert package_sources.read_url("mesonet", "https://t/a", Opener) == b"A" and not opened
    assert package_sources.read_url("mesonet", "https://t/b", Opener) == b"B" and opened == ["https://t/b"]
    assert {e["url"] for e in provenance.snapshot()} == {"https://t/a", "https://t/b"}


def test_deactivate_stops_serving(tmp_path):
    path, entries = _package(tmp_path, {"https://t/a": b"A"})
    package_sources.activate(path, entries)
    assert package_sources.active_package() == path
    package_sources.deactivate()
    assert package_sources.get("https://t/a") is None and package_sources.active_package() is None


def test_a_broken_package_is_ignored(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    package_sources.activate(bad, [{"url": "https://t/a", "member": "data/x"}])
    assert package_sources.get("https://t/a") is None


def test_kept_copies_stop_at_the_total_cap(monkeypatch):
    monkeypatch.setattr(provenance, "KEEP_TOTAL_BYTES", 10)
    provenance.record("hazards", "https://a", b"123456")
    provenance.record("hazards", "https://b", b"123456")
    assert provenance.kept_bytes("https://a") and provenance.kept_bytes("https://b") is None
    assert len(provenance.snapshot()) == 2           # still recorded, re-downloaded at export


def test_packages_are_served_from_many_threads(tmp_path):
    files = {f"https://t/{i}": bytes([i]) * 1000 for i in range(20)}
    path, entries = _package(tmp_path, files)
    package_sources.activate(path, entries)
    errors = []

    def read(url, data):
        if package_sources.get(url) != data:
            errors.append(url)
    threads = [threading.Thread(target=read, args=item) for item in files.items() for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


class _Stream:
    def __init__(self, chunks, on_read=None):
        self._chunks, self._on_read = list(chunks), on_read

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _n):
        if self._on_read:
            self._on_read()
        return self._chunks.pop(0) if self._chunks else b""


def test_export_download_sends_the_api_key_only_to_the_api(monkeypatch):
    import urllib.request
    seen = []
    monkeypatch.setattr(main_window_case.config, "NSSL_API_KEY", "secret")
    monkeypatch.setattr(main_window_case.config, "NSSL_API_ROOT", "https://api.example")

    def fake_urlopen(request, timeout, context):
        seen.append((request.full_url, request.get_header("X-api-key")))
        return _Stream([b"abc", b"def"])
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert main_window_case.package_fetch("https://api.example/annotations/x") == b"abcdef"
    assert main_window_case.package_fetch("https://thredds.example/x") == b"abcdef"
    assert seen == [("https://api.example/annotations/x", "secret"), ("https://thredds.example/x", None)]


def test_export_download_stops_on_cancel(monkeypatch):
    import urllib.request
    cancel = threading.Event()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout, context: _Stream([b"a"] * 100, on_read=cancel.set))
    with pytest.raises(InterruptedError):
        main_window_case.package_fetch("https://s3/KFDR_V06", cancel)


def test_export_uses_the_kept_copy_first(monkeypatch):
    import urllib.request
    provenance.record("hazards", "https://iem/x", b"kept")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded"))
    assert main_window_case.package_fetch("https://iem/x") == b"kept"


def test_archive_radar_takes_the_packaged_volume(monkeypatch, tmp_path):
    import gzip
    from datetime import datetime, timezone
    import requests
    import archive.fetchers.radar_archive_fetcher as raf
    scan = datetime(2024, 4, 27, 20, 4, 45, tzinfo=timezone.utc)
    key = "2024/04/27/KTLX/KTLX20240427_200445_V06"
    url = f"{raf._S3_BASE}/{key}"
    path, entries = _package(tmp_path, {url: gzip.compress(b"level2 volume")})
    package_sources.activate(path, entries)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("went to the network"))
    fetcher = raf.ArchiveRadarFetcher("KTLX", scan)
    try:
        fetcher._index_keys = {scan: key}
        assert fetcher._download_scan(scan) == b"level2 volume"
    finally:
        fetcher.shutdown()


def test_archive_radar_records_what_it_downloads(monkeypatch):
    from datetime import datetime, timezone
    import requests
    import archive.fetchers.radar_archive_fetcher as raf
    scan = datetime(2024, 4, 27, 20, 4, 45, tzinfo=timezone.utc)
    key = "2024/04/27/KTLX/KTLX20240427_200445_V06"
    monkeypatch.setattr(requests, "get", lambda url, **k: _Resp(b"volume", url=url))
    fetcher = raf.ArchiveRadarFetcher("KTLX", scan)
    try:
        fetcher._index_keys = {scan: key}
        assert fetcher._download_scan(scan) == b"volume"
    finally:
        fetcher.shutdown()
    (entry,) = provenance.snapshot()
    assert entry["kind"] == "radar" and entry["url"].endswith(key)
