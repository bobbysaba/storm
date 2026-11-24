# Data sources

Your team's own data can go on the map without changing STORM: describe
each feed in `storm.toml` as a `[[sources]]` table, and STORM fetches it,
converts it and draws it the way it draws its built-in layers. Your settings
file stays private: it lives in your STORM folder (or wherever
`STORM_CONFIG` points), not in this repository.

Four kinds:

| `kind` | What it draws | Where the button goes |
|--------|---------------|-----------------------|
| `station_obs` | Station plots (temperature, dewpoint, wind barb, pressure) from a JSON or CSV feed of current observations | SURFACE drawer, next to ASOS |
| `overlay` | GeoJSON points, lines and polygons | MAP drawer |
| `sounding` | Upper-air profiles in the Skew-T viewer, from an index of launches | SOUNDING drawer, after OBS |
| `contours` | Contour lines of gridded fields (a mesoanalysis, a model), run by run, from vector tiles | Its own toolbar button and drawer; one per settings file |

`station_obs` and `overlay` can have their own toolbar button instead
(`where = "toolbar"`). All four are live only, except an overlay with
`archive = true`, which ARCHIVE shows too.

A feed these keys can't describe can come from a
[code provider](providers.md) instead: a Python package of your own that
returns the same kinds. [agent-setup.md](agent-setup.md) is a questionnaire
an AI agent can work through with you to write either.

NSSL's six regional mesonets are `station_obs` sources in
[`profiles/nssl.toml`](../profiles/nssl.toml): six worked examples of
different response layouts. Its SFCOA mesoanalysis is a `contours` source.

## Keys every source has

```toml
[[sources]]
id = "farm"
kind = "station_obs"
name = "Farm Network"
short = "FARM"
url = "https://data.example.org/farm/current.json"
api_key_env = "FARM_API_KEY"
refresh_min = 5
```

| Key | Default | Meaning |
|-----|---------|---------|
| `id` | required | Lower-case letters, digits, `-` and `_`; unique. Station plot IDs, saved sessions and the launch window use it. `asos` is taken |
| `kind` | required | `station_obs`, `overlay`, `sounding` or `contours` |
| `name` | the id in capitals | Long label: tooltips, the layer list |
| `short` | the id in capitals | Button label |
| `url` | required | `http://` or `https://` address of the data |
| `api_key_env` | none | Name of the environment variable that holds the key. Put the key in `.env` (`FARM_API_KEY=...`), never in `storm.toml`. Without it set, the button is greyed out and says which variable it needs |
| `api_key_header` | `"X-API-Key"` | Header the key goes in |
| `api_key_param` | none | Send the key as this query parameter instead (`?token=...`). STORM keeps it out of its status line and logs |
| `ca_cert` | none | Extra CA certificate to trust for this server, for a chain your system doesn't trust on its own. Relative to the STORM folder |

A source without `api_key_env` or `ca_cert` of its own is asked with the
`[[auth]]` table covering its URLs, if one does
([configuration.md](configuration.md#auth-keys-and-certificates-by-server)):
one table for a server serves all its sources, and its key goes to that
server only.

| Key | Default | Meaning |
|-----|---------|---------|
| `refresh_min` | `5` (`station_obs`, `sounding`), `10` (`overlay`) | Minutes between fetches while the source is on (for `sounding`, while the Skew-T viewer shows it). Not for `contours`, whose drawer has a REFRESH button |
| `where` | `"drawer"` | `"toolbar"` gives the source its own toolbar button (not for `sounding`; `contours` always has one) |
| `icon` | none (`"contours"` for `contours`) | With `where = "toolbar"`, and for `contours`: one of `surface`, `radar`, `satellite`, `hazards`, `contours`, `sounding`, `lidar`, `track`, `vehicles`, `prev_locs`, `annotate`, `map` (`sfcoa`, its name before 3.0, still reads as `contours`) |

A mistake in a `[[sources]]` table is reported when STORM starts, with
every other `storm.toml` problem. An unknown key or field is skipped (with
a suggestion when it's close to a real one); a source that can't be used
(a missing `url`, a bad value) is left out, and the others load. See
[configuration.md](configuration.md#when-something-in-the-file-is-wrong).

## `station_obs`: station plots

STORM needs, for each station: an ID, a position, and at least one reading.
You tell it where to find those in the response.

### Response layouts (`shape`)

Most feeds are one of three layouts. `rows` (a path, dotted for nested
objects) says where the stations are inside the response; leave it out when
they're at the top level.

**`rows`** (the default): a list with one object per station.

```json
{"results": [{"id": "F1", "lat": 35.2, "lon": -97.4, "t": 75, "time": "2026-05-20T20:00:00Z"}]}
```
```toml
rows = "results"
fields = { station = "id", lat = "lat", lon = "lon", time = "time", temperature_c = "t" }
```

**`keyed`**: an object whose keys are station IDs. Values that aren't
objects (a list of station names, a units note) are skipped.

```json
{"units": "us", "F1": {"t": 75}, "F2": {"t": 80}}
```
```toml
shape = "keyed"
fields = { temperature_c = "t" }
```

**`columns`**: an object per variable, each keyed by station.

```json
{"time": "2026-05-20T20:00:00Z", "data": {"tair": {"f1": 24.1, "f2": 26.0}, "wspd": {"f1": 5.2}}}
```
```toml
shape = "columns"
rows = "data"
time = "time"
fields = { temperature_c = "tair", wind_speed_ms = "wspd" }
```

For `keyed` and `columns` the key is the station ID unless `fields.station`
says otherwise.

`format = "csv"` reads a CSV file instead (a header row, then one row per
station); it's always `rows`.

### `[sources.fields]`

STORM's field on the left, the name in the response on the right:

| Field | Meaning |
|-------|---------|
| `station` | Station ID (required for `rows`) |
| `name` | Display name; the ID if missing |
| `lat`, `lon` | Position, decimal degrees (here or in the station list) |
| `time` | Observation time; see [Times](#times) |
| `elevation_m` | Station elevation, for `station_pressure_mb` |
| `temperature_c` | Air temperature |
| `dewpoint_c` | Dewpoint |
| `humidity_pct` | Relative humidity, %: STORM works out the dewpoint when there's no `dewpoint_c` |
| `wind_speed_ms` | Wind speed |
| `wind_dir_deg` | Wind direction, degrees (from) |
| `pressure_mb` | Sea-level pressure |
| `station_pressure_mb` | Station pressure: STORM reduces it to sea level with `elevation_m` and the temperature, when there's no `pressure_mb` |

A name can be:

- a key: `"t"`, or one with spaces and brackets: `"TA [C]"`;
- a dotted path into nested objects: `"meta.lat"`, `"data.wind.tenMeter.speed.avg"`;
  list items by index: `"pos.0"`;
- a list of names to try in order, for a value that's sometimes missing:
  `wind_speed_ms = ["wind.10m", "wind.3m"]`.

Inline tables (`fields = { ... }`) and a separate `[sources.fields]` table
under the `[[sources]]` header are the same thing in TOML.

### `[sources.units]`

Readings are taken as STORM's units (°C, m/s, hPa, m) unless you say
otherwise:

| Field | Units |
|-------|-------|
| `temperature_c`, `dewpoint_c` | `C`, `F`, `K` |
| `wind_speed_ms` | `m/s`, `kt`, `mph`, `km/h` |
| `pressure_mb`, `station_pressure_mb` | `hPa`, `mb`, `kPa`, `Pa`, `inHg` |
| `elevation_m` | `m`, `ft` |

```toml
units = { temperature_c = "F", dewpoint_c = "F", wind_speed_ms = "mph", pressure_mb = "inHg" }
```

### Missing and bad values

`missing = [-999, "M"]` lists the values your feed uses for "no reading".
Whatever you list, STORM also drops what no sensor reports: values at or
below -900, 999 or more for wind, and anything outside physical limits
(temperature -60 to 60 °C, dewpoint -80 to 40 °C, wind 0 to 120 m/s,
pressure 500 to 1100 hPa), a dewpoint above the temperature, and positions
at or near 0, 0. A station without a position is skipped.

### Times

`time` is read as ISO 8601 (`2026-05-20T20:00:00Z`, `2026-05-20 15:00`), or
as Unix seconds when it's a number. Other layouts: `time_format` with a
Python [`strptime`](https://docs.python.org/3/library/datetime.html#format-codes)
format, such as `"%m/%d/%Y %H:%M"`. A time without a zone is UTC unless
`timezone` names another (`"America/Chicago"`, daylight time included).

A top-level `time` key (outside `fields`) is one time for the whole
response, used for stations without their own. Without either, STORM uses
the time it fetched the data.

### A separate station list (`stations_url`)

When the observations don't carry positions or names, point STORM at the
station list:

| Key | Default | Meaning |
|-----|---------|---------|
| `stations_url` | none | Where the list is (the source's own key and `ca_cert`, if it sets them; else the `[[auth]]` table covering this URL, if any) |
| `stations_format` | `"json"` | `json` or `csv` |
| `stations_shape` | `"rows"` | `rows` or `keyed` |
| `stations_rows` | top level | Path to the list |
| `[sources.station_fields]` | | The same fields as `[sources.fields]`, read from the list: at least `station` (for `rows`), usually `lat`, `lon`, `name`, `elevation_m` |
| `stations_refresh_min` | `1440` | Minutes between reads of the list |

Stations are matched by ID, ignoring case. A value missing from an
observation is taken from its station's row, so the list can carry readings
too: the Oklahoma Mesonet's public list has each station's sea-level
pressure, so NSSL's `ok` source reads `pressure_mb` from it and reloads it
every 5 minutes. When positions come only from the list, stations not in it
are skipped.

### Freshness

`fresh_min = [green, amber]` (default `[5, 10]`) colors each station plot's
center dot by the age of its observation: green up to the first number of
minutes, amber up to the second, red after. ASOS uses `[70, 90]`. Set it to
match how often your stations report.

### A complete example

A CSV of farm weather stations in US units and local time, with an API key
in a query parameter:

```toml
[[sources]]
id = "farm"
kind = "station_obs"
name = "Farm Network"
short = "FARM"
url = "https://data.example.org/farm/latest.csv"
api_key_env = "FARM_API_KEY"
api_key_param = "token"
format = "csv"
time_format = "%Y-%m-%d %H:%M"
timezone = "America/Chicago"
missing = ["M"]
fields = { station = "STN", name = "Name", lat = "Lat", lon = "Lon", time = "Time", temperature_c = "TempF", humidity_pct = "RH", wind_speed_ms = "WindKt", wind_dir_deg = "Dir", pressure_mb = "Altim" }
units = { temperature_c = "F", wind_speed_ms = "kt", pressure_mb = "inHg" }
fresh_min = [15, 30]
```

```
STN,Name,Lat,Lon,Time,TempF,RH,WindKt,Dir,Altim
F1,North Field,35.20,-97.40,2026-05-20 14:05,86,48,20,180,29.92
```

## `overlay`: GeoJSON on the map

```toml
[[sources]]
id = "targets"
kind = "overlay"
name = "Target areas"
short = "TGT"
url = "https://data.example.org/targets.geojson"
refresh_min = 15
style = { color = "#ff8800", width = 2, fill_opacity = 0.15, label = "name" }
```

The URL returns a GeoJSON `FeatureCollection`, a single `Feature` or a bare
geometry, in longitude/latitude. Polygons are filled and outlined, lines
drawn, points shown as dots. `[sources.style]`:

| Key | Default | Meaning |
|-----|---------|---------|
| `color` | `"#4FC3F7"` | Hex color for everything |
| `width` | `2` | Line width, pixels |
| `fill_opacity` | `0.15` | Polygon fill, 0 to 1 |
| `radius` | `5` | Point radius, pixels |
| `label` | none | Feature property to print next to each feature |

The overlay sits under the road labels and moves with the others in the
LAYERS list.

`archive = true` shows the overlay in ARCHIVE too, for layers that don't
change with the day: field-program boundaries, instrument sites, county
lines. ARCHIVE fetches the URL as it is now, not as it was on the session's
day, so leave it off for anything that changes.

## `sounding`: profiles in the Skew-T viewer

A sounding source is an index of launches, each pointing at a profile file.
Picking its button in the SOUNDING drawer fetches the launches in the last
`lookback_hours` and opens the newest in the Skew-T viewer, with the others
on its time slider. While the viewer shows it, STORM fetches it again every
`refresh_min`. Once fetched, it's offered as a comparison pill on the other
soundings within 150 km, and HRRR and the nearest radiosonde are offered on
it.

```toml
[[sources]]
id = "uni"
kind = "sounding"
name = "University Sondes"
short = "UNI"
url = "https://data.example.org/sondes/index.json"
lookback_hours = 12
```

```json
[
  {"time": "2026-05-20T21:02:00Z", "url": "20260520_2102.txt", "lat": 35.18, "lon": -97.44},
  {"time": "2026-05-20T22:30:00Z", "url": "20260520_2230.txt"}
]
```

### The index

| Key | Default | Meaning |
|-----|---------|---------|
| `url` | required | The index: JSON, a list of launches |
| `launches` | top level | Path to the list inside the index |
| `[sources.launch_fields]` | `time`, `url`, `lat`, `lon` | Where each launch's values are, as in `[sources.fields]` |
| `lookback_hours` | `12` | Launches older than this are left out (at most 240) |
| `lat`, `lon` | none | The site, for launches without a position of their own |
| `index` | `true` | `false`: `url` is one profile, not an index |

Each launch needs a `time` (read as [Times](#times) describes, with
`time_format` and `timezone`) and a `url`; a relative `url` is relative to
the index's. A launch's `lat`/`lon` is optional: without one it gets the
source's `lat`/`lon`, and the sounding without either has no place on the
map, so it can't be compared with others by distance.

With `index = false`, `url` is a single profile ("latest.txt"). Its time is
the JSON profile's `time` path when you give one, else the server's
Last-Modified header, else the time STORM fetched it.

### Profiles

`format` says what each profile file is:

**`sharppy`** (the default): the SHARPpy / SPC text format, the `%RAW%`
block of `PRES, HGHT, TEMP, DWPT, WDIR, WSPD` lines (hPa, m, °C, °C,
degrees, knots), `-9999` for missing. It has fixed columns, so `fields`,
`units`, `shape` and `rows` aren't used.

```
%RAW%
 970.00,   380.00,    28.0,    20.0,   170.00,    20.00
 950.00,   560.00,    26.0,    19.0,   175.00,    25.00
%END%
```

**`csv`**: a header row, then one row per level.

**`json`**: `shape = "rows"` (a list of level objects) or `shape = "arrays"`
(an object of equal-length lists, one per variable), at the `rows` path.

```toml
format = "json"
shape = "arrays"
rows = "profile"
fields = { pressure_mb = "p", height_m = "z", temperature_c = "T", humidity_pct = "RH", u_ms = "u", v_ms = "v" }
units = { temperature_c = "K" }
missing = [-999]
```

`[sources.fields]` for `csv` and `json` profiles:

| Field | Meaning |
|-------|---------|
| `pressure_mb` | Pressure (required) |
| `height_m` | Height above sea level (required) |
| `temperature_c` | Temperature (required) |
| `dewpoint_c` | Dewpoint (this or `humidity_pct`) |
| `humidity_pct` | Relative humidity, %: STORM works out the dewpoint |
| `wind_dir_deg`, `wind_speed_ms` | Wind direction (from) and speed, both or neither |
| `u_ms`, `v_ms` | Wind components (toward the east and north), both or neither |

`[sources.units]` takes the units in [the station_obs table](#sourcesunits),
plus `height_m` (`m`, `ft`) and `u_ms`/`v_ms` (as wind speed).

A level is kept when it has pressure, height, temperature and a dewpoint;
levels without wind keep an empty wind. A profile needs 5 such levels. Levels
are put surface first, and a dewpoint above the temperature is lowered to it.
A profile that fails to load is skipped; the others still show.

## `contours`: contour lines from vector tiles

A contours source is a folder of runs (analysis or forecast times), each
with a set of fields drawn as labeled contour lines: a mesoanalysis, a
model's fields. Its toolbar button (under the overflow menu) opens a drawer
with the runs (step through them with the arrows) and a button per field,
in columns by group. Several fields can be on at once; they stay on as you
change run. A settings file can have one contours source.

```toml
[[sources]]
id = "meso"
kind = "contours"
name = "Regional mesoanalysis"
short = "MESO"
url = "https://data.example.org/meso"
api_key_env = "MESO_API_KEY"
```

| Key | Default | Meaning |
|-----|---------|---------|
| `tiles` | `"pmtiles"` | The tile file each field comes in: `"pmtiles"` or `"mbtiles"` |
| `run_format` | `"%Y%m%d_%H"` | How the run ids in `index.json` read as UTC times ([Python's date codes](https://docs.python.org/3/library/datetime.html#format-codes)). Ids that don't match are skipped |

The folder `url` points at:

```
index.json              {"runs": ["20260507_15", "20260507_14", "20260507_13"]}
<run>/metadata.json     the run's fields
<run>/<field>.pmtiles   one vector tile set per field listed in metadata.json
```

`index.json` lists the runs in any order; the drawer puts them oldest to
newest and opens on the newest. `metadata.json`:

```json
{
  "valid_time": "2026-05-07T15:00:00Z",
  "bounds": [-110, 28, -85, 45],
  "variables": {
    "tmpc": {"label": "sfc tmp", "long_name": "Surface temperature", "units": "C", "group": "thermo"},
    "sbcp": {"label": "sfc cape", "long_name": "Surface-based CAPE", "units": "J/kg", "group": "parcel"}
  }
}
```

| Field key | Default | Meaning |
|-----------|---------|---------|
| `label` | the field's id | Its button |
| `long_name` | none | The button's tooltip |
| `units` | none | After each contour's value in its label, and in the tooltip |
| `group` | `"other"` | The drawer column it goes in, its fields in the order `variables` lists them. `thermo` (headed SURFACE), `parcel`, `lapse`, `composite`, `helicity` (SRH), `shear`, `upper` (UPPER AIR) and `kinematic` (WINDS) have their own columns, in that order; any other group shares a column after them |

`valid_time` (default: the run id read with `run_format`) is what the
status line and the layer's age go by. `bounds` is used only when a tile
file gives none.

Each tile file is a vector tile set, made from contour lines with
[tippecanoe](https://github.com/felt/tippecanoe) for instance, whose first
layer holds the lines. Each line carries `level-value` (the number its label
shows), and optionally `stroke` (its color, such as `"#ff0000"`; default
yellow) and `stroke-width` (pixels, default 1). STORM downloads a field's file whole the
first time it's shown and keeps it for a day, so keep files small: building
to zoom 7 (tippecanoe `--maximum-zoom 7`) is plenty for regional contours.
Keep tippecanoe's `--no-feature-limit --no-tile-size-limit`, or it drops
lines in dense tiles. To write `.pmtiles`, name the output `<field>.pmtiles`
(recent tippecanoe releases), or convert an `.mbtiles` with
[`pmtiles convert`](https://github.com/protomaps/go-pmtiles). STORM reads
PMTiles version 3 with gzip or no compression.

The key, if any, goes with every request: the index, each run's metadata
and each tile file.

## Checking a source

- `python scripts/check_data_sources.py <word>` asks every source in your
  `storm.toml` whose name or URL has that word whether it still answers (no
  `api_key_env` keys sent; a 401 or 403 counts as there).
  `python providers/nssl/check_sources.py` checks NSSL's, the profile's
  mesonets included.
- In STORM, the SURFACE drawer's status line shows each source's count,
  valid time and errors, and the SURFACE button gets an amber or red dot
  while one is failing. An overlay or sounding that fails says so in the
  status bar; a sounding profile that's skipped is in the log. A contours
  source's errors show in its drawer's status line.
- A response STORM can't read (not JSON, no list where `rows` points) shows
  as an error in the status line with what it expected.

## When a feed doesn't fit

If your data needs more than these keys (a login handshake, paging, a binary
format, a launch position read from a second file), it can be a
[code provider](providers.md) instead: a Python package of your own, kept
out of this repository, that returns the `station_obs`, `overlay` and
`sounding` kinds and can add ARCHIVE sources too.
