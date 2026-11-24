"""Recorded-history sources for ARCHIVE (archive/history.py), and NSSL's
profile: THREDDS, then the API with its [[auth]] key."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

import pytest

import settings
from archive import history
from archive.fetchers import mqtt_reader as mr


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class _Source:
    def __init__(self, name, files=None, error=None):
        self.name, self.files, self.error, self.asked = name, files or {}, error, []

    def fetch(self, kind, date_str):
        self.asked.append((kind, date_str))
        if self.error:
            raise self.error
        return self.files.get((kind, date_str))


# --- [archive] in storm.toml -------------------------------------------------

def _load(tmp_path, text):
    path = tmp_path / "storm.toml"
    path.write_text(text)
    return settings.load(path)


def test_archive_defaults(tmp_path):
    a = _load(tmp_path, "").archive
    assert a.recordings == settings.PROJECT_DIR / "recordings"
    assert a.history is None and a.history_prefix == ""


def test_archive_settings_are_read(tmp_path):
    a = _load(tmp_path, '[archive]\nrecordings = "/data/rec"\n'
                        'history = ["nssl", "https://x.org/storm/"]\nhistory_prefix = "team"\n').archive
    assert a.recordings == Path("/data/rec")
    assert a.history == ("nssl", "https://x.org/storm/") and a.history_prefix == "team"


@pytest.mark.parametrize("text, message", [
    ('[archive]\nhistory = "recordings"\n', "must be a list"),
    ('[archive]\nhistory_prefix = "a/b"\n', "file-name prefix"),
    ('[archive]\nfolder = "x"\n', "unknown key folder"),
])
def test_bad_archive_settings_are_errors(tmp_path, text, message):
    assert message in _load(tmp_path, text).error


# --- sources -----------------------------------------------------------------

def test_default_is_the_providers_then_the_recordings_folder():
    # NSSL's provider has no history_source: its history is URLs in the profile
    (source,) = history.build_sources(archive=settings.ArchiveSettings(), topic_root="storm")
    assert isinstance(source, history.FolderSource)
    assert source.folder == settings.PROJECT_DIR / "recordings" and source.prefix == "storm"


def test_without_the_provider_the_default_is_the_recordings_folder(nssl_provider_off):
    (source,) = history.build_sources(archive=settings.ArchiveSettings(), topic_root="team/storm")
    assert isinstance(source, history.FolderSource) and source.prefix == "team.storm"


def test_a_listed_provider_that_is_off_is_skipped(nssl_provider_off):
    archive = settings.ArchiveSettings(history=("nssl", "https://x.org/a/"), history_prefix="old")
    (source,) = history.build_sources(archive=archive, topic_root="storm")
    assert isinstance(source, history.UrlSource) and source.root == "https://x.org/a"
    assert source.prefix == "old"


def test_folder_source_reads_plain_and_jsonl_names(tmp_path):
    (tmp_path / "team.vehicles.20260506").write_text("a")
    (tmp_path / "team.cones.20260506.jsonl").write_text("b")
    source = history.FolderSource(tmp_path, "team")
    assert source.fetch("vehicles", "20260506") == "a"
    assert source.fetch("cones", "20260506") == "b"
    assert source.fetch("drawings", "20260506") is None
    assert history.FolderSource(tmp_path / "missing", "team").fetch("cones", "20260506") is None


def test_url_source_tries_the_jsonl_name_after_a_miss(monkeypatch):
    asked = []
    monkeypatch.setattr(history, "fetch_url", lambda url, **kw: asked.append(url) or (
        "x" if url.endswith(".jsonl") else None))
    assert history.UrlSource("https://h/r/", "storm").fetch("cones", "20260506") == "x"
    assert asked == ["https://h/r/storm.cones.20260506", "https://h/r/storm.cones.20260506.jsonl"]


def test_the_first_source_with_the_file_wins():
    first = _Source("first")
    second = _Source("second", {("cones", "20260506"): "data"})
    third = _Source("third", {("cones", "20260506"): "other"})
    assert history.fetch_text([first, second, third], "cones", "20260506") == ("data", "second")
    assert third.asked == []


def test_a_failed_source_falls_through():
    ok = _Source("ok", {("cones", "20260506"): "data"})
    assert history.fetch_text([_Source("down", error=TimeoutError("t")), ok], "cones", "20260506") == ("data", "ok")


def test_nothing_anywhere_is_a_clean_miss_unless_a_source_failed():
    assert history.fetch_text([_Source("a"), _Source("b")], "cones", "20260506") == (None, "")
    with pytest.raises(RuntimeError, match="down: t"):
        history.fetch_text([_Source("a"), _Source("down", error=TimeoutError("t"))], "cones", "20260506")


def test_the_reader_replays_a_folder_of_recordings(tmp_path):
    record = {"vehicle_id": "WX1", "active": True, "mode": "point", "lat": 35.0, "lon": -97.0,
              "timestamp": "2024-04-27T23:00:00Z"}
    (tmp_path / "storm.scan_sectors.20240427").write_text(json.dumps(record) + "\n")
    reader = mr.ArchiveMQTTReader(session_date=_utc(2024, 4, 27, 20),
                                  sources=[history.FolderSource(tmp_path, "storm")])
    reader._fetch_all()
    assert [ts for ts, _ in reader._data["scan_sectors"]] == [_utc(2024, 4, 27, 23)]


# --- NSSL's profile: THREDDS, then recordings; no API --------------------------

_THREDDS = "https://data.nssl.noaa.gov/thredds/fileServer/FOFS/STORM/annotations"


def _profile_sources():
    profile = settings.load(settings.PROJECT_DIR / "profiles" / "nssl.toml")
    return history.build_sources(archive=profile.archive, topic_root="storm")


def test_the_profile_reads_thredds_then_recordings_without_a_key(monkeypatch):
    monkeypatch.delenv("NSSL_API_KEY")
    thredds, folder = _profile_sources()
    assert thredds.root == _THREDDS
    assert isinstance(folder, history.FolderSource) and folder.prefix == "storm"


def test_thredds_gets_no_key_but_the_providers_ca_without_the_profile(monkeypatch, nssl_provider_off):
    import dataclasses
    asked = []

    def opener(request, timeout, context):
        asked.append((request.get_header("X-api-key"), context))
        raise URLError("offline")
    monkeypatch.setattr(history, "urlopen", opener)
    # no [[auth]] tables, only the built-in provider: its AUTH covers THREDDS
    nssl = settings.CodeProvider("nssl", settings.PROJECT_DIR / "providers" / "nssl", builtin=True)
    monkeypatch.setattr(settings, "SETTINGS", dataclasses.replace(settings.SETTINGS, code_providers=(nssl,)))
    with pytest.raises(URLError):
        history.fetch_url(f"{_THREDDS}/storm.cones.20260517")
    ((key, context),) = asked
    assert key is None and context is not None


def test_only_an_http_404_is_a_clean_miss(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("request failed for 20240427")
    monkeypatch.setattr(history, "urlopen", fail)
    with pytest.raises(URLError):
        history.fetch_url("https://example.test/20240427")


def test_folder_files_are_recorded_for_case_packages(tmp_path, monkeypatch):
    from core import package_sources
    (tmp_path / "storm.cones.20260506").write_text("a")
    seen = []
    monkeypatch.setattr(package_sources.provenance, "record", lambda kind, url, data: seen.append((kind, url, data)))
    assert history.FolderSource(tmp_path, "storm").fetch("cones", "20260506") == "a"
    assert seen == [("mqtt history", (tmp_path / "storm.cones.20260506").resolve().as_uri(), b"a")]


def test_an_open_case_package_serves_folder_files(tmp_path, monkeypatch):
    from core import package_sources
    uri = (tmp_path / "storm.cones.20260506").resolve().as_uri()
    monkeypatch.setattr(package_sources, "get", lambda url: b"packed" if url == uri else None)
    assert history.FolderSource(tmp_path, "storm").fetch("cones", "20260506") == "packed"
