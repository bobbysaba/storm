"""The archive date index: a missing file is absent, a failed check stays unknown."""
from datetime import date, datetime, timezone
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import pytest

from archive import catalog as cat


def _page(spec, *names):
    return '<html><title>THREDDS Catalog</title>' + ''.join(
        f"<a href='catalog.html?dataset={quote(spec.path + '/' + name, safe='')}'>file</a>"
        for name in names) + '</html>'


@pytest.fixture
def _fofs(listed_platform):
    return listed_platform


def test_registry_unique_and_every_registered_source_has_catalogs():
    assert len({p.platform_id for p in cat.ALL_PLATFORMS}) == len(cat.ALL_PLATFORMS)
    assert sum(p.family == cat.MESONET for p in cat.ALL_PLATFORMS) == 17  # 16 vehicles + "other"
    for platform in cat.ALL_PLATFORMS:
        assert cat.catalogs_for_platform(platform)


def test_dates_ignore_invalid_dates_and_deduplicate():
    assert cat._dates_from_filenames(['x.20240427.nc', '20240427.txt', '20240230.nc', 'readme']) == [date(2024, 4, 27)]


def test_processed_and_raw_dates_are_unioned_with_encoded_and_direct_links(_fofs):
    p = _fofs()
    processed, raw = cat.catalogs_for_platform(p)
    pages = {processed.url: _page(processed, '20240427.nc'), raw.url:
             f'<a href="/thredds/fileServer/{raw.path}/20250501.txt">raw</a>'}
    result = list(cat.AvailabilityIndex([p], lambda url, _: pages[url], pace=0).scan(Event()))[-1]
    assert result.dates == {date(2024, 4, 27), date(2025, 5, 1)}
    assert result.platforms[p.platform_id].complete


def test_links_restrict_directory_host_and_file_type(_fofs):
    spec = cat.catalogs_for_platform(_fofs())[0]
    parser = cat._CatalogLinks(spec)
    parser.feed(_page(spec, '20240427.nc', '20240101.txt', '../other/20240102.nc') +
                f'<a href="https://other.test/catalog.html?dataset={spec.path}/20240103.nc">other</a>')
    assert parser.filenames == {'20240427.nc'}


def test_failed_catalog_keeps_positive_dates_and_unknown_completeness(_fofs):
    p = _fofs()
    processed, raw = cat.catalogs_for_platform(p)
    def fetch(url, _):
        if url == raw.url:
            raise TimeoutError('timeout')
        return _page(processed, '20240427.nc')
    result = list(cat.AvailabilityIndex([p], fetch, pace=0).scan(Event()))[-1]
    assert result.dates == {date(2024, 4, 27)}
    assert result.checked == result.total == 2
    assert not result.platforms[p.platform_id].complete
    assert 'timeout' in result.platforms[p.platform_id].errors[0]


def test_session_reuses_completed_and_failed_attempts_until_refresh(_fofs):
    calls = []
    def fetch(url, _):
        calls.append(url)
        raise TimeoutError('timeout')
    index = cat.AvailabilityIndex([_fofs()], fetch, pace=0)
    list(index.scan(Event()))
    list(index.scan(Event()))
    assert len(calls) == 2
    index.clear()
    list(index.scan(Event()))
    assert len(calls) == 4


def test_cancellation_retains_completed_listing_and_stops_remaining_requests(_fofs):
    cancel = Event()
    calls = []
    spec = cat.catalogs_for_platform(_fofs())[0]
    def fetch(url, token):
        calls.append(url)
        token.set()
        return _page(spec, '20240427.nc')
    index = cat.AvailabilityIndex([_fofs()], fetch, pace=0)
    with pytest.raises(cat.ScanCanceled):
        list(index.scan(cancel))
    assert calls == [spec.url]
    assert index.snapshot().dates == {date(2024, 4, 27)}
    assert index.snapshot().checked == 1


def test_http_404_is_absent_but_410_remains_unknown(monkeypatch):
    def missing(*args, **kwargs):
        raise HTTPError('url', 404, 'missing', {}, None)
    monkeypatch.setattr(cat, 'urlopen', missing)
    assert cat._fetch_catalog_html('https://example.test', Event()) == ''
    def unavailable(*args, **kwargs):
        raise HTTPError('url', 410, 'unavailable', {}, None)
    monkeypatch.setattr(cat, 'urlopen', unavailable)
    with pytest.raises(HTTPError):
        cat._fetch_catalog_html('https://example.test', Event())


def test_cancellation_stops_retry_backoff(monkeypatch):
    cancel = Event()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(True)
        cancel.set()
        raise URLError('timeout')
    monkeypatch.setattr(cat, 'urlopen', fetch)
    with pytest.raises(cat.ScanCanceled):
        cat._fetch_catalog_html('https://example.test', cancel)
    assert calls == [True]


def test_non_catalog_response_is_an_error(monkeypatch):
    import io
    monkeypatch.setattr(cat, 'urlopen', lambda *a, **kw: io.BytesIO(b'<html>Login required</html>'))
    with pytest.raises(ValueError, match='THREDDS catalog'):
        cat._fetch_catalog_html('https://example.test', Event())


def test_scan_prioritizes_recent_sources_without_losing_older_dates(_fofs):
    platforms = [_fofs('mg1'), _fofs('farfield'), _fofs('probe1'), _fofs('dltruck')]
    calls = []
    def fetch(url, _):
        calls.append(url)
        spec = next(s for p in platforms for s in cat.catalogs_for_platform(p) if s.url == url)
        return _page(spec, '20150501.txt' if '/raw/' in url else '20260501.nc')
    index = cat.AvailabilityIndex(platforms, fetch, pace=0)
    snapshots = list(index.scan(Event()))
    assert [url.split('/TEST/')[1] for url in calls[:4]] == [
        'probe1/processed/catalog.html', 'dltruck/processed/catalog.html',
        'probe1/raw/catalog.html', 'dltruck/raw/catalog.html',
    ]
    assert '/farfield/' in calls[4]
    assert '/mg1/' in calls[-1]
    assert snapshots[-1].checked == snapshots[-1].total == 8
    assert snapshots[-1].dates == {date(2015, 5, 1), date(2026, 5, 1)}


def test_refresh_updates_recency_hints_from_live_dates_without_skipping_sources(_fofs):
    platforms = [_fofs('dltruck'), _fofs('mg1')]
    def fetch(url, _):
        spec = next(s for p in platforms for s in cat.catalogs_for_platform(p) if s.url == url)
        stamp = '20270501' if '/mg1/' in url else '20260501'
        return _page(spec, stamp + ('.txt' if '/raw/' in url else '.nc'))
    index = cat.AvailabilityIndex(platforms, fetch, pace=0)
    list(index.scan(Event()))
    index.clear()
    assert '/mg1/' in index._scan_order()[0].url
    assert list(index.scan(Event()))[-1].checked == 4


def test_date_cache_shades_immediately_and_avoids_repeat_requests(tmp_path, _fofs):
    platform = _fofs()
    path = tmp_path / 'dates.json'
    calls = []
    def fetch(url, _):
        calls.append(url)
        spec = next(s for s in cat.catalogs_for_platform(platform) if s.url == url)
        return _page(spec, '20260517' + ('.txt' if '/raw/' in url else '.nc'))
    first = cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: 1000.)
    list(first.scan(Event()))
    calls.clear()
    second = cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: 1100.)
    assert second.snapshot().dates == {date(2026, 5, 17)}
    assert second.snapshot().cached
    list(second.scan(Event()))
    assert calls == []
    second.clear()
    list(second.scan(Event()))
    assert len(calls) == 2  # Explicit refresh bypasses the TTL.


@pytest.mark.parametrize("stamp, rechecked", [("20260517", True), ("20190517", False)])
def test_catalogs_with_dates_this_year_are_rechecked_after_an_hour(tmp_path, _fofs, stamp, rechecked):
    """New field days must appear without a manual refresh; older catalogs keep the day-long cache."""
    platform = _fofs()
    path = tmp_path / 'dates.json'
    calls = []
    def fetch(url, _):
        calls.append(url)
        spec = next(s for s in cat.catalogs_for_platform(platform) if s.url == url)
        return _page(spec, stamp + ('.txt' if '/raw/' in url else '.nc'))
    june_2026 = datetime(2026, 6, 1, 12).timestamp()
    list(cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: june_2026).scan(Event()))
    calls.clear()
    later = june_2026 + cat._ACTIVE_CACHE_TTL_SECONDS + 60
    list(cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: later).scan(Event()))
    assert bool(calls) is rechecked

def test_expired_cache_remains_visible_but_failed_refresh_is_not_complete(tmp_path, _fofs):
    platform = _fofs()
    path = tmp_path / 'dates.json'
    def fetch(url, _):
        spec = next(s for s in cat.catalogs_for_platform(platform) if s.url == url)
        return _page(spec, '20260517' + ('.txt' if '/raw/' in url else '.nc'))
    first = cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: 1000.)
    list(first.scan(Event()))
    def fail(*args):
        raise TimeoutError('offline')
    second = cat.AvailabilityIndex([platform], fail, pace=0, cache_path=path,
                                   now=lambda: 1000. + cat._CACHE_TTL_SECONDS + 1)
    assert second.snapshot().dates == {date(2026, 5, 17)}
    assert second.snapshot().checked == 0
    result = list(second.scan(Event()))[-1]
    assert result.dates == {date(2026, 5, 17)}
    assert not result.platforms[platform.platform_id].complete


@pytest.mark.parametrize("content", ["{not json", "[]", '{"version":1,"catalogs":[]}'])
def test_corrupt_date_cache_cannot_block_discovery(tmp_path, content, _fofs):
    path = tmp_path / 'dates.json'
    path.write_text(content)
    index = cat.AvailabilityIndex([_fofs()], lambda *a: '', pace=0, cache_path=path)
    assert index.snapshot().checked == 0
    assert not index.snapshot().dates
    assert list(index.scan(Event()))[-1].checked == 2


def test_calendar_parser_keeps_one_presence_token_per_day_not_all_files():
    spec = cat.CatalogSpec('FRDD/CLAMPS/example', '.cdf')
    parser = cat._CatalogLinks(spec, dates_only=True)
    parser.feed(_page(spec, 'r.20260517.000000.cdf', 'r.20260517.120000.cdf', 'r.20260518.000000.cdf'))
    assert parser.filenames == set()
    assert parser.date_stamps == {'20260517', '20260518'}


def _install_fofs_index(monkeypatch, paths):
    from archive import fofs_index
    files = [fofs_index.FofsFile(fofs_index.vehicle_for_path(p), fofs_index._date_from_name(p.rsplit("/", 1)[-1]), p, "")
             for p in paths]
    monkeypatch.setattr(fofs_index, "get_index", lambda *a, **k: fofs_index.FofsIndex(files, 0.0))


def test_mesonet_dates_come_from_the_crawled_index_wherever_files_live(monkeypatch):
    _install_fofs_index(monkeypatch, [
        "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt",
        "FOFS/Mobile-Mesonet/data/probe1/moved_here/20250605.txt",
        "FOFS/Mobile-Mesonet/data/dltruck/raw/20250605.txt",
    ])
    probe1 = next(p for p in cat.ALL_PLATFORMS if p.platform_id == "FOFS-probe1")
    result = list(cat.AvailabilityIndex([probe1], lambda *a: "", pace=0).scan(Event()))[-1]
    assert result.dates == {date(2024, 4, 27), date(2025, 6, 5)}
    assert result.platforms["FOFS-probe1"].complete


def test_unregistered_vehicle_folders_still_put_their_dates_on_the_calendar(monkeypatch):
    _install_fofs_index(monkeypatch, [
        "FOFS/Mobile-Mesonet/data/probe1/raw/20240427.txt",
        "FOFS/Mobile-Mesonet/data/newtruck/raw/20250701.txt",
    ])
    other = next(p for p in cat.ALL_PLATFORMS if p.platform_id == "FOFS-other")
    result = list(cat.AvailabilityIndex([other], lambda *a: "", pace=0).scan(Event()))[-1]
    assert result.dates == {date(2025, 7, 1)}


def test_unavailable_mesonet_index_is_unknown_not_empty():
    # conftest leaves no index available
    probe1 = next(p for p in cat.ALL_PLATFORMS if p.platform_id == "FOFS-probe1")
    result = list(cat.AvailabilityIndex([probe1], lambda *a: "", pace=0).scan(Event()))[-1]
    assert not result.platforms["FOFS-probe1"].complete
    assert "index unavailable" in result.platforms["FOFS-probe1"].errors[0]


def test_each_mesonet_vehicle_is_its_own_site_by_its_field_name():
    by_site = cat.platforms_by_site()
    assert [p.platform_id for p in by_site["LiDAR Truck"]] == ["FOFS-dltruck"]
    assert [p.platform_id for p in by_site["Probe 1"]] == ["FOFS-probe1"]
    assert "" not in by_site


def test_catalog_urls_come_from_thredds_paths(_fofs):
    from data import thredds_paths as paths
    spec = cat.catalogs_for_platform(_fofs())[0]
    assert spec.url == paths.catalog_url(spec.path)
    probe1 = next(p for p in cat.ALL_PLATFORMS if p.platform_id == "FOFS-probe1")
    assert cat.catalogs_for_platform(probe1)[0].url.startswith(paths.catalog_url(paths.FOFS_MESONET))


def test_a_failed_check_is_cached_with_its_old_dates_but_retried_next_launch(tmp_path, _fofs):
    platform = _fofs()
    path = tmp_path / "dates.json"

    def fetch(url, _):
        spec = next(s for s in cat.catalogs_for_platform(platform) if s.url == url)
        return _page(spec, "20240517" + (".txt" if "/raw/" in url else ".nc"))
    list(cat.AvailabilityIndex([platform], fetch, pace=0, cache_path=path, now=lambda: 1000.).scan(Event()))

    def fail(*args):
        raise TimeoutError("offline")
    list(cat.AvailabilityIndex([platform], fail, pace=0, cache_path=path, now=lambda: 1000. + cat._CACHE_TTL_SECONDS + 1)
         .scan(Event()))
    calls = []

    def count(url, _):
        calls.append(url)
        return fetch(url, _)
    third = cat.AvailabilityIndex([platform], count, pace=0, cache_path=path, now=lambda: 1000. + cat._CACHE_TTL_SECONDS + 2)
    assert third.snapshot().dates == {date(2024, 5, 17)}          # shown at once
    list(third.scan(Event()))
    assert len(calls) == 2                                        # and checked again
