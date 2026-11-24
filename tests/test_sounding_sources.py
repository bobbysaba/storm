"""Sounding [[sources]] (data/sources.py, data/fetchers/source_sounding_fetcher.py)
and archive = true on overlays."""
import json
import math
import tomllib
from datetime import datetime, timedelta, timezone

import pytest

import settings
from data.fetchers.source_sounding_fetcher import SoundingSourceError, fetch_sounding_set
from data.sources import (
    SourceError,
    levels_to_sounding,
    parse_sources,
    profile_levels,
    read_launches,
    sounding_key,
)

NOW = datetime(2026, 5, 20, 22, 0, tzinfo=timezone.utc)

HEAD = '''
[[sources]]
id = "uni"
kind = "sounding"
name = "University Sondes"
short = "UNI"
url = "https://uni.test/sondes/index.json"
'''

SHARPPY = """%TITLE%
 UNI   260520/2100

   LEVEL       HGHT       TEMP       DWPT       WDIR       WSPD
-------------------------------------------------------------------
%RAW%
 970.00,   380.00,    28.0,    20.0,   170.00,    20.00
 950.00,   560.00,    26.0,    19.0,   175.00,    25.00
 900.00,  1030.00,    22.0,    17.0,   185.00,    30.00
 850.00,  1520.00,    18.0,    14.0,   200.00,    35.00
 700.00,  3120.00,     8.0,  -9999.0,   230.00,    40.00
 500.00,  5800.00,   -10.0,   -25.0,   250.00,    50.00
%END%
"""


def _sources(text):
    return parse_sources(tomllib.loads(text).get("sources", []), settings.PROJECT_DIR)


def _one(text):
    return _sources(text)[0]


# --- parsing -------------------------------------------------------------------

def test_a_minimal_sounding_source_reads_sharppy_profiles_from_an_index():
    src = _one(HEAD)
    assert (src.kind, src.format, src.index, src.lookback_hours, src.refresh_min, src.where) == \
        ("sounding", "sharppy", True, 12.0, 5.0, "drawer")


def test_a_json_sounding_source():
    src = _one(HEAD + '''format = "json"
shape = "arrays"
rows = "data"
lat = 35.2
lon = -97.4
[sources.fields]
pressure_mb = "p"
height_m = "z"
temperature_c = "t"
humidity_pct = "rh"
u_ms = "u"
v_ms = "v"
[sources.units]
temperature_c = "K"
[sources.launch_fields]
time = "launched"
url = "file"
''')
    assert (src.format, src.shape, src.rows, src.site) == ("json", "arrays", "data", (35.2, -97.4))
    assert dict(src.launch_fields) == {"time": ("launched",), "url": ("file",)}
    assert dict(src.units) == {"temperature_c": "k"}


@pytest.mark.parametrize("extra, message", [
    ('fields = { pressure_mb = "p" }\n', "fixed columns"),
    ('format = "csv"\nfields = { pressure_mb = "p", height_m = "z" }\n', "needs temperature_c"),
    ('format = "csv"\nfields = { pressure_mb = "p", height_m = "z", temperature_c = "t" }\n',
     "dewpoint_c or humidity_pct"),
    ('format = "csv"\nfields = { pressure_mb = "p", height_m = "z", temperature_c = "t", '
     'dewpoint_c = "d", wind_dir_deg = "wd" }\n', "or neither"),
    ('format = "csv"\nshape = "arrays"\n', "CSV profile"),
    ('where = "toolbar"\nicon = "sounding"\n', "SOUNDING drawer"),
    ('index = false\nlookback_hours = 6\n', "for an index"),
    ('time = "t"\n', "index = false"),
    ('lookback_hours = 0\n', "lookback_hours"),
    ('lat = 35.0\n', "lat and lon"),
    ('archive = true\n', "unknown key archive"),
])
def test_bad_sounding_sources(extra, message):
    with pytest.raises(SourceError, match=message):
        _one(HEAD + extra)


def test_archive_is_an_overlay_flag():
    text = '[[sources]]\nid = "fields"\nkind = "overlay"\nurl = "https://x.test/f.geojson"\n'
    assert _one(text).archive is False
    assert _one(text + "archive = true\n").archive is True
    with pytest.raises(SourceError, match="true or false"):
        _one(text + 'archive = "yes"\n')


# --- reading -------------------------------------------------------------------

def test_sharppy_levels_convert_knots_and_drop_missing_dewpoints():
    src = _one(HEAD)
    levels = profile_levels(src, SHARPPY.encode())
    assert len(levels) == 6
    assert levels[0]["wind_speed_ms"] == pytest.approx(20 * 0.514444)
    assert levels[4]["dewpoint_c"] is None
    snd = levels_to_sounding(levels, NOW, 35.0, -97.0)
    # the 700 hPa level has no dewpoint, so it's left out
    assert list(snd.pressure) == [970.0, 950.0, 900.0, 850.0, 500.0]
    # a south wind blows toward the north: v > 0
    assert snd.v_wind[0] > 0 and abs(snd.u_wind[0]) < 2


def test_too_few_levels_is_no_sounding():
    src = _one(HEAD)
    levels = profile_levels(src, SHARPPY.encode())[:3]
    assert levels_to_sounding(levels, NOW) is None


def test_json_rows_with_units_and_humidity():
    src = _one(HEAD + '''format = "json"
rows = "levels"
missing = [-999]
[sources.fields]
pressure_mb = "pres"
height_m = "alt"
temperature_c = "temp"
humidity_pct = "rh"
[sources.units]
height_m = "ft"
temperature_c = "F"
''')
    rows = [{"pres": 1000 - 50 * i, "alt": 1000 + 1500 * i, "temp": 80 - 5 * i, "rh": 60} for i in range(6)]
    rows[2]["rh"] = -999
    snd = levels_to_sounding(profile_levels(src, json.dumps({"levels": rows}).encode()), NOW)
    assert len(snd.pressure) == 5
    assert snd.height[0] == pytest.approx(1000 * 0.3048)
    assert snd.temperature[0] == pytest.approx((80 - 32) * 5 / 9)
    assert snd.dewpoint[0] < snd.temperature[0]
    # no wind fields: wind stays empty
    assert all(math.isnan(u) for u in snd.u_wind)


def test_csv_profile():
    src = _one(HEAD + 'format = "csv"\nfields = { pressure_mb = "P", height_m = "Z", temperature_c = "T", '
                      'dewpoint_c = "TD", wind_dir_deg = "DIR", wind_speed_ms = "SPD" }\n')
    text = "P,Z,T,TD,DIR,SPD\n" + "\n".join(f"{1000 - 100 * i},{100 + 1000 * i},{25 - 6 * i},{15 - 8 * i},270,10"
                                            for i in range(6))
    snd = levels_to_sounding(profile_levels(src, text.encode()), NOW)
    assert len(snd.pressure) == 6
    # a west wind blows toward the east: u > 0
    assert snd.u_wind[0] == pytest.approx(10.0)


def test_launches_within_the_lookback_resolve_against_the_index():
    src = _one(HEAD + 'launches = "items"\nlookback_hours = 6\n')
    index = {"items": [
        {"time": "2026-05-20T21:00:00Z", "url": "a.txt", "lat": 35.1, "lon": -97.2},
        {"time": "2026-05-20T12:00:00Z", "url": "old.txt"},
        {"time": "2026-05-20T19:30:00Z", "url": "https://elsewhere.test/b.txt", "lat": 0, "lon": 0},
        {"time": "not a time", "url": "c.txt"},
        {"time": "2026-05-20T20:00:00Z"},
    ]}
    launches = read_launches(src, index, NOW)
    assert [launch.url for launch in launches] == ["https://elsewhere.test/b.txt",
                                                   "https://uni.test/sondes/a.txt"]
    # 0, 0 is no position
    assert (launches[0].lat, launches[1].lat) == (None, 35.1)


# --- fetching ------------------------------------------------------------------

def _getter(files):
    calls = []

    def get(source, url):
        calls.append(url)
        if url not in files:
            raise SoundingSourceError(f"HTTP 404 from {url}")
        return files[url], None
    get.calls = calls
    return get


def test_fetch_builds_a_set_from_the_index():
    src = _one(HEAD + "lat = 36.0\nlon = -98.0\n")
    index = [
        {"time": (NOW - timedelta(hours=2)).isoformat(), "url": "one.txt", "lat": 35.5, "lon": -97.5},
        {"time": (NOW - timedelta(hours=1)).isoformat(), "url": "two.txt"},
        {"time": (NOW - timedelta(minutes=30)).isoformat(), "url": "gone.txt"},
    ]
    get = _getter({"https://uni.test/sondes/index.json": json.dumps(index).encode(),
                   "https://uni.test/sondes/one.txt": SHARPPY.encode(),
                   "https://uni.test/sondes/two.txt": SHARPPY.encode()})
    sset = fetch_sounding_set(src, get=get, now=NOW)
    assert sset.source == sounding_key("uni") == "source:uni"
    assert (sset.station_id, sset.station_name) == ("UNI", "University Sondes")
    assert [s.label for s in sset.soundings] == ["2000Z May 20", "2100Z May 20"]
    # the second has no position of its own: the source's site
    assert (sset.soundings[0].lat, sset.soundings[1].lat) == (35.5, 36.0)
    assert (sset.lat, sset.lon) == (36.0, -98.0)
    assert sset.is_time_series and not sset.is_provider


def test_fetch_one_profile_uses_its_time_path():
    src = _one(HEAD + '''index = false
format = "json"
rows = "levels"
time = "launched"
fields = { pressure_mb = "p", height_m = "z", temperature_c = "t", dewpoint_c = "d" }
''')
    rows = [{"p": 1000 - 100 * i, "z": 100 + 1000 * i, "t": 25 - 6 * i, "d": 15 - 8 * i} for i in range(6)]
    body = json.dumps({"launched": "2026-05-20T18:00:00Z", "levels": rows}).encode()
    sset = fetch_sounding_set(src, get=_getter({src.url: body}), now=NOW)
    assert sset.soundings[0].valid_time == datetime(2026, 5, 20, 18, tzinfo=timezone.utc)


def test_fetch_without_launches_says_so():
    src = _one(HEAD)
    get = _getter({src.url: b"[]"})
    with pytest.raises(SoundingSourceError, match="no launches in the last 12 hours"):
        fetch_sounding_set(src, get=get, now=NOW)
