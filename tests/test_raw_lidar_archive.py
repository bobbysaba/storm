"""Reading raw lidar PPI / CSM files (archive/fetchers/raw_lidar_archive_fetcher.py):
times, range units, fill values, the truck's heading and GPS placement."""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from archive.fetchers import clamps_common
from archive.fetchers import raw_lidar_archive_fetcher as raw
from archive.positions import PositionTrack
from core.observation import Observation


def _source(product='ppi', mobile=False):
    return next(s for s in raw.KNOWN_RAW_LIDAR_SOURCES if s.product == product and s.mobile == mobile)


def _file(path, source, bad_time=False, trailer_heading=None, range_units='km'):
    ds = xr.Dataset({
        'base_time': ((), 1782001159.),
        'time_offset': ('time', [120., np.nan if bad_time else 0., 360.]),
        'range': ('range', [.015, .045]),
        'velocity': (('time', 'range'), [[10., -999.], [20., 21.], [30., 31.]]),
        'intensity': (('time', 'range'), np.ones((3, 2)) * 1.05),
        'azimuth': ('time', [120., 121., 122.]),
        'elevation': ('time', [180., 90., .5]),
        'lat': ('time', [-999.] * 3), 'lon': ('time', [-999.] * 3),
        'heading': ('time', [-999.] * 3), 'snum': ('time', [9., 20., 4.]),
    }, attrs={'Site_latitude': -999. if source.mobile else 35.,
              'Site_longitude': -999. if source.mobile else -97.})
    ds['range'].attrs['units'] = range_units
    ds['velocity'].attrs['units'] = 'm/s'
    ds['azimuth'].attrs['comment'] = '0 degrees is north'
    if trailer_heading is not None:
        ds.attrs['Trailer_heading'] = trailer_heading
    ds.to_netcdf(path, engine='h5netcdf')


def _load(monkeypatch, path, source, track):
    monkeypatch.setattr(clamps_common, 'download_to_cache', lambda *a, **k: path)
    return raw.load_raw_lidar(raw.LidarAsset(source, 'r.cdf'), path.parent,
                              truck_track=PositionTrack(track))


@pytest.mark.parametrize('product', ['csm', 'ppi'])
def test_raw_ray_products_preserve_times_axes_scan_ids_and_fill(product, tmp_path):
    source = _source(product)
    path = tmp_path / 'r.cdf'
    _file(path, source)
    result = raw.parse_raw_lidar(path, source)
    assert np.diff(result.time_epoch).min() > 0
    np.testing.assert_allclose(result.distance_m, [15., 45.])
    assert result.fields['velocity']['data'].mask[1, 1]
    assert result.fields['velocity']['data'][0, 0] == 20.
    assert result.fields['velocity']['data'].dtype == np.float32
    assert result.scan_number.tolist() == [20., 9., 4.]  # never sort by scan number
    assert result.elevation_deg[1] == 180.  # rays are kept as stored; core/lidar_scans picks the PPIs
    assert result.latitude.tolist() == [35.] * 3
    when = datetime.fromtimestamp(result.time_epoch[1], timezone.utc)
    assert result.rays_at(when).tolist() == [1]
    assert not result.rays_at(datetime.fromtimestamp(result.time_epoch[1] + 90, timezone.utc)).size


def test_mobile_gps_is_matched_per_ray_without_inventing_heading(monkeypatch, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source)
    times = [datetime.fromtimestamp(t, timezone.utc) for t in (1782001159., 1782001279.)]
    track = [Observation('lid1', 35., -97., times[0]), Observation('lid1', 36., -98., times[1])]
    result = _load(monkeypatch, path, source, track)
    np.testing.assert_allclose(result.latitude[:2], [35., 36.])
    assert result.coordinate_source[0] == 'FOFS truck GPS within 60 s'
    assert np.isnan(result.latitude[2])  # stale fix is not carried forward
    assert np.isnan(result.heading_deg).all()
    assert not result.ground_geometry_valid.any()


def test_bad_time_drops_aligned_measurements(tmp_path):
    source = _source()
    path = tmp_path / 'r.cdf'
    _file(path, source, bad_time=True)
    result = raw.parse_raw_lidar(path, source)
    assert result.time_epoch.size == 2
    assert result.fields['velocity']['data'].shape == (2, 2)
    assert result.fields['velocity']['data'][0, 0] == 10.


def test_raw_registry_is_ppi_and_csm_only():
    assert len(raw.KNOWN_RAW_LIDAR_SOURCES) == 8          # 4 lidar streams x {ppi, csm}
    assert {s.product for s in raw.KNOWN_RAW_LIDAR_SOURCES} == {'ppi', 'csm'}
    assert not any(tag in s.datastream for s in raw.KNOWN_RAW_LIDAR_SOURCES for tag in ('hpl', 'dlfp', 'dlother'))
    assert all('/ingested/' in s.path for s in raw.KNOWN_RAW_LIDAR_SOURCES)
    from data.thredds_paths import all_folders
    checked = {path for what, _h, path, _r in all_folders() if what.startswith('raw lidar')}
    assert checked == {s.path for s in raw.KNOWN_RAW_LIDAR_SOURCES}


def test_discovery_lists_the_folder_without_assuming_a_midnight_filename(monkeypatch):
    source = _source()
    name = source.datastream + '.20260729.133415.cdf'
    other = 'clampsdlfpC1.b1.20260729.000000.cdf'                 # another stream in the folder
    monkeypatch.setattr(clamps_common, 'list_folder', lambda path: [other, name, name + '.md5'])
    assets = raw.discover_raw_lidar(source, date(2026, 7, 29))
    assert [a.filename for a in assets] == [name]
    assert assets[0].url.endswith(f'{source.path}/{name}')
    assert raw.discover_raw_lidar(source, date(2026, 7, 28)) == []


@pytest.mark.parametrize('heading, expected', [(265.2, [26.2, 25.2, 27.2]), (180.0, [301., 300., 302.])])
def test_truck_azimuths_are_rotated_by_the_recorded_heading(heading, expected, tmp_path):
    """The truck stores azimuth relative to the truck (0 = straight ahead)
    despite the '0 degrees is north' comment; true = stored + heading."""
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source, trailer_heading=heading)
    result = raw.parse_raw_lidar(path, source)
    np.testing.assert_allclose(result.azimuth_deg, np.array(expected) % 360)
    np.testing.assert_allclose(result.instrument_azimuth_deg, [121., 120., 122.])   # as pointed
    assert result.provenance['north_referenced'] is True
    assert result.provenance['truck_heading_deg'] == heading


@pytest.mark.parametrize('heading', [None, -999.0, float('nan'), 0.0])
def test_truck_scans_without_a_heading_are_not_mapped(heading, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source, trailer_heading=heading)
    result = raw.parse_raw_lidar(path, source)
    assert result.provenance['north_referenced'] is False
    np.testing.assert_allclose(result.azimuth_deg, [121., 120., 122.])     # left as stored (time order)
    result.latitude[:] = 35.0
    result.longitude[:] = -97.0
    assert not result.ground_geometry_valid.any()                            # even with GPS positions
    assert 'heading not recorded' in result.provenance['azimuth_reference']


def test_stationary_trailers_keep_file_azimuths(tmp_path):
    source = _source(mobile=False)
    path = tmp_path / 'r.cdf'
    _file(path, source, trailer_heading=151.0)
    result = raw.parse_raw_lidar(path, source)
    np.testing.assert_allclose(result.azimuth_deg, [121., 120., 122.])
    assert result.ground_geometry_valid.all()


def _compass_track(times, headings):
    return [Observation('lid1', 35., -97., t, heading_deg=h) for t, h in zip(times, headings)]


def test_missing_truck_heading_is_estimated_from_the_compass_and_says_so(monkeypatch, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source)                                       # no Trailer_heading
    ray_times = [datetime.fromtimestamp(t, timezone.utc) for t in (1782001159., 1782001279., 1782001519.)]
    result = _load(monkeypatch, path, source, _compass_track(ray_times, [90.0, 92.0, 94.0]))
    np.testing.assert_allclose(result.azimuth_deg, [121 + 90, 120 + 92, 122 + 94])   # rays in time order
    assert result.azimuth_known.all() and result.ground_geometry_valid.all()
    assert result.provenance['heading_missing_in_file'] is True
    assert result.provenance['truck_heading_estimate_deg'] == pytest.approx(92.0, abs=0.1)
    assert 'missing from this lidar file; estimated from the truck compass' in result.provenance['azimuth_reference']
    assert not any(w.startswith('Truck heading not recorded') for w in result.warnings)


def test_rays_without_a_compass_reading_stay_unmapped(monkeypatch, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source)
    first = datetime.fromtimestamp(1782001159., timezone.utc)
    track = _compass_track([first], [90.0]) + [Observation('lid1', 35., -97., first + timedelta(seconds=s))
                                               for s in (120, 360)]     # positions, no compass
    result = _load(monkeypatch, path, source, track)
    assert result.azimuth_known.tolist() == [True, False, False]
    assert result.ground_geometry_valid.tolist() == [True, False, False]
    assert '67% of rays had no compass reading' in result.provenance['azimuth_reference']


def test_no_compass_at_all_leaves_the_file_unoriented(monkeypatch, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source)
    t = datetime.fromtimestamp(1782001159., timezone.utc)
    result = _load(monkeypatch, path, source, [Observation('lid1', 35., -97., t)])
    assert not result.ground_geometry_valid.any()
    assert 'No truck compass reading covers these times either' in result.provenance['azimuth_reference']


def test_no_truck_track_at_all_leaves_the_file_unplaced(monkeypatch, tmp_path):
    source = _source(mobile=True)
    path = tmp_path / 'r.cdf'
    _file(path, source, trailer_heading=180.0)
    monkeypatch.setattr(clamps_common, 'download_to_cache', lambda *a, **k: path)
    result = raw.load_raw_lidar(raw.LidarAsset(source, 'r.cdf'), tmp_path, truck_track=None)
    assert np.isnan(result.latitude).all() and not result.ground_geometry_valid.any()


def test_a_file_without_slant_range_is_refused(tmp_path):
    source = _source()
    path = tmp_path / 'r.cdf'
    xr.Dataset({'base_time': ((), 1782001159.), 'time_offset': ('time', [0.]),
                'height': ('height', [.015]), 'velocity': (('time', 'height'), [[1.]])}).to_netcdf(path, engine='h5netcdf')
    with pytest.raises(ValueError, match='no slant range'):
        raw.parse_raw_lidar(path, source)


@pytest.mark.parametrize('units', ['km AGL', 'KM', 'kilometers'])
def test_2017_2020_files_labeled_km_agl_are_read_as_slant_range_in_km(units, tmp_path):
    # older CLAMPS files call the range "km AGL" though it is slant range
    # (their comment: height = range x sin(elevation))
    path = tmp_path / 'r.cdf'
    _file(path, _source(), range_units=units)
    np.testing.assert_allclose(raw.parse_raw_lidar(path, _source()).distance_m, [15., 45.])


def test_unknown_range_units_are_still_refused(tmp_path):
    path = tmp_path / 'r.cdf'
    _file(path, _source(), range_units='furlongs')
    with pytest.raises(ValueError, match='range units'):
        raw.parse_raw_lidar(path, _source())


def test_a_trailer_site_is_read_from_the_header_by_range_requests(monkeypatch, tmp_path):
    path = tmp_path / 'r.cdf'
    _file(path, _source())
    data = path.read_bytes()

    class Response:
        status = 206

        def __init__(self, request):
            first, last = map(int, request.get_header('Range').split('=')[1].split('-'))
            self.body = data[first:last + 1]
            self.headers = {'Content-Range': f'bytes {first}-{last}/{len(data)}'}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(raw, 'urlopen', lambda request, **kw: Response(request))
    site = raw.read_site(raw.LidarAsset(_source(), 'r.cdf'))
    assert (site['lat'], site['lon']) == (35.0, -97.0)
    monkeypatch.setattr(raw, 'urlopen', lambda request, **kw: (_ for _ in ()).throw(OSError('down')))
    assert raw.read_site(raw.LidarAsset(_source(), 'r.cdf')) is None
