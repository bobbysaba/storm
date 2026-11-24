# Code providers

When a feed is more than a `[[sources]]` table can describe (a login
handshake, paging, a binary format, values spread across several files), you
can write a few lines of Python instead: a **code provider**. It's a package
of your own that hands STORM the same kinds of data a `[[sources]]` table
does, and it can add ARCHIVE sources for your recorded traffic and vehicle
obs. Your code never goes into this repository.

NSSL's provider (`providers/nssl/`) ships with STORM and uses this same
interface, instrument hooks included: it's the full worked example.

## Turning one on

Put the package at `providers/<name>/` in your STORM folder (everything in
`providers/` except `nssl/` is git-ignored) or anywhere else, and turn it on
in `storm.toml` (NSSL's ships with STORM and is on without a table;
`[providers.nssl] enabled = false` turns it off):

```toml
[providers.farm]
enabled = true
# path = "~/farm-storm"    # a folder outside the STORM folder, if you'd rather
api_key_env = "FARM_API_KEY"
region = "central"
```

The name is lower-case letters, digits and `_`, starting with a letter.
`enabled` and `path` are STORM's; every other key is yours, handed to your
functions as the `options` dict. Keep keys and passwords in `.env` and pass
the variable's name, as above.

STORM reports a missing folder (no `__init__.py`) when it starts, like any
other `storm.toml` error. A provider that fails to import, or whose function
fails, is left out and logged (`storm_errors.log`); STORM still starts.

## What the package defines

Every function is optional. Each takes `options`. The three below are
data; the [instrument hooks](#instrument-hooks) further down feed STORM's
own windows (VAD, LIDAR, the provider's sounding button).

| Function | Returns | Used for |
|----------|---------|----------|
| `sources(options)` | a list of `code_source(...)` | Station plots, overlays and soundings in live mode, like `[[sources]]` |
| `history_source(options)` | an object with `name` and `fetch(kind, date_str)` | ARCHIVE's recorded broker traffic, when `[archive] history` lists the provider by name (or leaves `history` out) |
| `vehicle_obs_source(options)` | an object with `name`, `vehicles(session_day)` and `load(vehicle_id, icon_type, session_day)` | ARCHIVE's vehicle obs, when `[archive] vehicle_obs` lists the provider by name (or leaves it out) |

`sources()` is called once, when STORM starts. STORM calls each source's
`fetch()` on a worker thread, every `refresh_min` while the source is on, so
it can block on the network. A `fetch()` that raises shows its message in
the status bar.

### `code_source(kind, id, fetch, **options)`

From `data.sources`. `id` follows the `[[sources]]` rules and must not clash
with a `[[sources]]` table's (a clash is logged and the provider's source is
left out). `options` are the display keys that kind's table has:

| Kind | Options |
|------|---------|
| `station_obs` | `name`, `short`, `refresh_min`, `where`, `icon`, `fresh_min` |
| `overlay` | `name`, `short`, `refresh_min`, `where`, `icon`, `style`, `archive` |
| `sounding` | `name`, `short`, `refresh_min`, `lat`, `lon` |

They mean what they mean in [data-sources.md](data-sources.md). What
`fetch()` returns, by kind:

**`station_obs`**: a list of dicts, one per station, keyed by STORM's field
names (`station`, `name`, `lat`, `lon`, `time`, `elevation_m`,
`temperature_c`, `dewpoint_c`, `humidity_pct`, `wind_speed_ms`,
`wind_dir_deg`, `pressure_mb`, `station_pressure_mb`) in STORM's units (°C,
m/s, hPa, m). `time` is a `datetime` (UTC when it has no zone) or ISO 8601
text. STORM applies the same checks and derivations as for a `[[sources]]`
feed.

**`overlay`**: GeoJSON, a `FeatureCollection`, `Feature` or geometry, as a
dict.

**`sounding`**: a list of launches, each a dict with `time`, `lat`, `lon`
(optional) and `levels`, a list of dicts keyed by `pressure_mb`, `height_m`,
`temperature_c`, `dewpoint_c` or `humidity_pct`, and `wind_dir_deg` +
`wind_speed_ms` or `u_ms` + `v_ms`, in STORM's units. Return the launches you
want on the time slider; STORM doesn't apply a lookback.

### Archive sources

`history_source` returns an object like `archive/history.py`'s
`FolderSource`: `fetch(kind, date_str)` returns the text of the
`<root>.<kind>.<YYYYMMDD>` file ([archive-data.md](archive-data.md)), `None`
when there's none, and raises when it couldn't tell.

`vehicle_obs_source` returns an object like `archive/vehicle_obs.py`'s
`FolderVehicleObsSource`: `vehicles(session_day)` (a set of vehicle IDs, or
`None` when it can't tell) and `load(...)` (a list of
`core.observation.Observation`, or `None` when it has no file for that
vehicle). STORM cleans up and cuts the track to the session. An optional
`calendar_platforms()` returning `archive.catalog.KnownPlatform`s shades the
launch window's calendar.

## Instrument hooks

These hand STORM an object for one of its windows. STORM asks the providers
in `storm.toml` order and uses the first that returns something; a hook that
returns `None` (an API without its key, say) passes the turn to the next.
`archive_lidar` and `archive_wind_profiles` are the exception: every
provider's are used, together with any provider drawer's (below), in one
lidar drawer and one wind-profile list (`archive/combined.py`). A lidar
instrument or wind-profile id two providers both use goes to the first.
Each object is a `QObject` whose signals STORM connects; NSSL's classes,
named below, are the reference for each. (NSSL itself hands these over
inside its own drawer, below.)

| Function | Returns | Turns on |
|----------|---------|----------|
| `live_sounding_fetcher(options, parent)` | `sounding_ready(SoundingSet)`, `fetch_error(str)`, `fetch()` (`ClampsSoundingFetcher`) | The sounding drawer's provider button, its pills and the map's right-click item |
| `archive_sounding_fetcher(options, parent)` | an `archive.fetchers.sounding_archive_fetcher.ArchiveSoundingFetcher` subclass with `fetch_provider_sounding()`; optionally `track_vehicle` + `set_truck_position(lookup)`, and `uas_ready(dict)` + `fetch_uas()` | The provider button in ARCHIVE, sondes placed on a vehicle's track, a **UAS** button for uncrewed-aircraft ascents |
| `archive_wind_profiles(options, archive_time, parent)` | `sets_ready({id: VADSet})`, `error(str)`, `fetch()`, `sets`, `is_loading()`, `source_name(id)` (`ArchiveClampsWindFetcher`); a `VADProfile` may carry per-level `intensity` (SNR + 1), and the VAD window then offers its min-intensity floor (from 1.002) | ARCHIVE's **VAD**, and the lidar drawer's **WINDS** |
| `archive_surface_playback(options, parent)` | `loaded(rows)`, `error(str)`, `load(archive_time)`, `install(rows)`, `rows`, `at(key, when)` (`ClampsSurfacePlayback`) | Fixed instruments' surface obs as vehicles on the map |
| `archive_lidar(options, archive_time, parent)` | the `ArchiveRawLidarFetcher` signals and methods, plus `sources` (`RawLidarSource`s) and `track_vehicle` (the vehicle a mobile lidar rides on, placed from its track); optionally `stares`, an object like NSSL's `ArchiveLidarStareFetcher` (`found`, `ready`, `failed`, `fetch()`, `load(instrument)`, `name(instrument)`, and optionally `progress(instrument, text)` while a download runs) | **LIDAR**, and its **STARES** |

Soundings a provider returns have `source = "provider"`. The button,
pills and legend use the package's `LABEL` (NSSL's is `"NSSL"`), else the
provider's name in capitals. In ARCHIVE a provider can also return
`source = "retrieval"` (remote-sensing profiles with no wind, such as
NSSL's CLAMPS TROPoe) and `source = "uas"` (uncrewed-aircraft ascents),
each with its own comparison pill.

STORM's own text names none of a provider's instruments; each object names
its own, and the generic word is used when it doesn't:

| Attribute | On | Used for | Without it | NSSL's |
|-----------|----|----------|------------|--------|
| `label` | a `SoundingSet` (`retrieval` and `uas` sets) | The Skew-T window's title and header | "Retrieved" / "UAS" | `"CLAMPS TROPoe"` |
| `uas_name`, `uas_tooltip` | the archive sounding fetcher | The UAS button (in capitals) and its status lines, and its tooltip | "UAS" | |
| `pill_labels` | the archive sounding fetcher | The Skew-T's comparison pills, `{"retrieval": ..., "uas": ...}` | "RETRIEVAL", "UAS" | `{"retrieval": "TROPoe"}` |
| `description` | the wind-profile and lidar fetchers | The VAD and LIDAR tooltips and the VAD status line ("Lidar wind profiles: …") | "Lidar wind profiles", "Doppler lidar" | `"the lidar truck and the CLAMPS trailers"` |
| `name`, `marker` | each lidar in `sources` | The LIDAR drawer and hover ("Lidar truck · stop 2 of 4"), and the map marker, numbered by stop when the lidar is `mobile` ("Truck 2") | the instrument id | `"Lidar truck"`, `"Truck"` |

## A drawer of its own: `archive_drawer`

A provider can have its own ARCHIVE button and drawer, as NSSL's does:
`archive_drawer(options, archive_time, parent)` returns an object with

| Attribute | Meaning |
|-----------|---------|
| `label` | The button's name ("NSSL") |
| `team` | `True` when the provider is the setup's own team (NSSL's `team = true`): VEHICLES starts on and its vehicles come through `[archive] history` / `vehicle_obs` as usual |
| `tag` | What a guest's vehicles from it are tagged with ("NSSL probe1") |
| `items` | `(key, label, tooltip)` rows, in order, from `vehicles`, `lidar`, `sounding`, `uas`, `retrievals` |
| `lidar`, `wind_profiles` | As `archive_lidar` / `archive_wind_profiles` return: its LIDAR row opens the lidar drawer, whose WINDS opens the wind profiles. They join any other provider's there; with a team lidar, the row opens the same drawer as the LIDAR button next to RADAR |
| `soundings` | An archive sounding fetcher with `fetch_provider_sounding()` (SOUNDING), `fetch_retrievals()` (RETRIEVALS) and `fetch_uas()` + `uas_ready` (UAS) |
| `surface` | As `archive_surface_playback` returns: its fixed instruments, with VEHICLES |
| `history_sources`, `vehicle_obs_sources`, `vehicle_renames` | For a guest's VEHICLES: its recorded traffic (vehicles and scan sectors only) and one-second obs, and recorded IDs to read as others (NSSL's `lid1` → `dltruck`) |
| `availability_ready(dict)`, `check_availability()` | Which items have data on the day (`True` / `False` / `None` for unknown); `False` greys a row out |

`providers/nssl/archive/drawer.py` is the reference. Every provider's
`archive_drawer` is asked, so two providers get two buttons.

## Trusting a server: `AUTH`

A provider whose server needs an extra CA (NSSL's THREDDS leaves an
intermediate out of its chain) can declare `[[auth]]`-style tables in a
module-level `AUTH` list, so it works without one in `storm.toml`:

```python
AUTH = [{"url": "https://data.example.org", "ca_cert": str(Path(__file__).parent / "certs" / "extra-ca.pem")}]
```

They're used only for URLs no `storm.toml` `[[auth]]` table covers.

## Case packages

Files a provider's fetchers record for case packages
(`core.package_sources.read_url(kind, ...)`) are offered under their kind:
`instrument surface`, `instrument profiles`, `uas` and `raw lidar` have
labels, and any other kind is offered under its own name.

Some things that used to be hooks are settings, so a team needs no code for
them ([configuration.md](configuration.md)): a server's key and CA
(`[[auth]]`), the day's shared items at launch (`[mqtt] shared_items_url`),
past deployment locations (`[map] deployment_locations`) and recorded
history at URLs (`[archive] history`). Mesoanalysis contours such as NSSL's
SFCOA are a `contours` source ([data-sources.md](data-sources.md#contours-contour-lines-from-vector-tiles)). A provider's own fetchers ask
`data.auth.request(url, headers)` for a URL's `[[auth]]` key and CA, as
NSSL's do, instead of handling keys themselves.

## An example

`providers/farm/__init__.py`:

```python
import json
import os
from urllib.request import Request, urlopen

from data.sources import code_source

BASE = "https://data.example.org/farm"


def _get(path, options):
    key = os.environ.get(options.get("api_key_env", ""), "")
    req = Request(f"{BASE}/{path}", headers={"Authorization": f"Bearer {key}"})
    with urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _stations(options):
    rows = []
    page = 1
    while page:                                   # a paged API
        data = _get(f"obs?region={options['region']}&page={page}", options)
        for st in data["stations"]:
            rows.append({
                "station": st["id"], "name": st["label"],
                "lat": st["loc"][1], "lon": st["loc"][0],
                "time": st["observed"],
                "temperature_c": (st["temp_f"] - 32) * 5 / 9,
                "wind_speed_ms": st["wind_kt"] * 0.514444,
                "wind_dir_deg": st["wind_dir"],
            })
        page = data.get("next_page")
    return rows


def sources(options):
    return [
        code_source("station_obs", "farm", lambda: _stations(options),
                    name="Farm Network", short="FARM", fresh_min=[15, 30]),
        code_source("overlay", "farm_fields", lambda: _get("fields.geojson", options),
                    name="Farm fields", short="FIELDS", archive=True,
                    style={"color": "#8BC34A", "label": "name"}),
    ]
```

STORM's own modules are importable from a provider (`data.sources`,
`core.observation`, `core.sounding`); anything else it needs must be in
STORM's conda environment.

## Checking one

```bash
python -c "import settings; print([(s.id, s.kind) for s in settings.SETTINGS.sources_of('station_obs') + settings.SETTINGS.sources_of('overlay') + settings.SETTINGS.sources_of('sounding')])"
```

lists every source STORM sees, yours included. A provider that didn't load
is in `storm_errors.log` with its traceback.
