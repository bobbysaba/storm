# Configuration

STORM reads its per-install settings from `storm.toml` in the STORM folder.
Secrets (passwords, API keys) go in `.env` next to it. Neither file is in the
repository.

To start:

```bash
cp storm.example.toml storm.toml
cp .env.example .env
```

Or start from a ready-made profile for a known setup:

```bash
cp profiles/nssl.toml storm.toml
```

To keep the file somewhere else, set `STORM_CONFIG` to its path. Only
public setups ship in `profiles/`; a team's own setup (its broker, its data
sources) stays in its own `storm.toml`, which can be kept anywhere and passed
around privately. The README's
[Setting up your team's laptops](../README.md#setting-up-your-teams-laptops)
lists what each vehicle needs.

Without a `storm.toml`, STORM runs with no broker: VIEWER and ARCHIVE work,
but there are no live vehicles, annotations, drawings, cones or scan sectors,
and VEHICLE and MONITOR won't launch.

### When something in the file is wrong

STORM lists every problem in one window at startup (and in
`storm_errors.log`), and uses the rest of the file:

| Problem | What STORM does |
|---------|-----------------|
| The file isn't valid TOML (a missing `]` or quote) | Runs as if there were no file: no broker until it's fixed |
| A table it doesn't know (`[unit]`, `[mqtt.veiwer]`) | Skips it, suggesting the name you probably meant ("did you mean [units]?") |
| A key it doesn't know (`pasword`, `temprature`) | Skips that key, with a suggestion, and uses the rest of the table |
| An invalid value in `[mqtt]` (`port = "x"`) | No broker until it's fixed; every other table still applies |
| An invalid value in `[mqtt.viewer]` | VIEWER uses `[mqtt]`'s login, read-only |
| An invalid value in `[archive]`, `[map]` or `[units]` | That table keeps its defaults |
| An invalid `[[sources]]` or `[[auth]]` entry, `[providers.<name>]` or named `[obs_columns.<name>]` layout | That one is left out; the others load (with no layout left, the default one) |

So a mistake in `[units]` costs you the units, not the broker. The tables
are `[mqtt]` (with `[mqtt.viewer]`), `[providers.<name>]`, `[archive]`,
`[obs_columns]`, `[[sources]]`, `[[auth]]`, `[map]` and `[units]`. The
headless publisher's own `storm-headless.toml` is stricter: its
`--check` reports the first problem and it won't start until it's fixed.

Relative paths in `storm.toml` are resolved against the STORM folder.

## `[mqtt]`: the broker

STORM shares vehicles, annotations, drawings, storm cones and scan sectors
through an MQTT v5 broker your team runs (Mosquitto, EMQX, HiveMQ, AWS IoT
Core and so on). Every STORM client and headless publisher on the team points
at the same one. To set one up, see [broker.md](broker.md); it also covers
who can see what and connection problems.

A client that connects late gets the day's annotations, drawings, cones and
scan sectors from retained messages, which needs a broker that sends them on
wildcard subscriptions. AWS IoT Core doesn't; see
[wire-format.md](wire-format.md#retained-messages). To check yours, see
[Checking your broker](#checking-your-broker).

| Key | Default | Meaning |
|-----|---------|---------|
| `host` | (none) | Broker host name. Empty means no broker. |
| `port` | `8883`, or `1883` with `tls = "none"` | Broker port. |
| `tls` | `"server"` | `"none"`: plain TCP. `"server"`: TLS, with the broker's certificate verified. `"mutual"`: TLS plus a client certificate (AWS IoT Core needs this). |
| `ca_cert` | system trust store | CA bundle that signed the broker's certificate. Only needed for a private CA. |
| `cert_file` | (none) | Client certificate. Required with `tls = "mutual"`. |
| `key_file` | (none) | Client private key. Required with `tls = "mutual"`. |
| `username` | (none) | Broker login name, if the broker uses one. |
| `password` | (none) | Better kept in `.env` as `STORM_MQTT_PASSWORD`, which takes precedence. |
| `client_id_prefix` | `""` | Put in front of every MQTT client ID. STORM's client ID is the vehicle ID, so this is for brokers that only allow certain prefixes. |
| `read_only` | `false` | Never publish. For a login the broker only lets subscribe; VEHICLE and MONITOR won't launch with it. |
| `topic_root` | `"storm"` | The first level of every topic STORM uses (`<topic_root>/vehicles/<id>` and so on). Every client and headless publisher on the team needs the same value. It may have several levels (`"teams/ou/storm"`) but no `+`, `#`, empty level, or leading `$`. See [wire-format.md](wire-format.md). |
| `daily_reset_utc` | `8` | Hour (UTC, 0–23) when shared annotations, drawings and scan sectors expire from the broker. Pick one between your field days; 8 is 2–3 a.m. in the US Central time zone. |
| `shared_items_url` | (none) | For a broker that doesn't send retained messages on wildcard subscriptions (AWS IoT Core): a URL serving the day's annotations, drawings and cones as one JSON object of payloads by item ID, read at launch and by the annotation drawer's **↻ Refresh** (shown only with this set). Asked with its `[[auth]]` key. `profiles/nssl.toml` points it at the NSSL API's `current.json`. [wire-format.md](wire-format.md#retained-messages) |

Which modes need it:

| Mode | Needs |
|------|-------|
| VEHICLE, MONITOR | A host, a login that may publish, and every file the TLS mode names. The launch window lists what's missing. |
| VIEWER | Nothing. With a broker it subscribes through `[mqtt.viewer]` (below) and never publishes; without one it has no live data. |
| ARCHIVE | Nothing. It never connects to the broker; it reads recorded history instead. |

Set `topic_root` when several teams share one broker, or when the broker's
ACLs only allow your logins under some prefix. Its ACLs then need to let
publishing logins publish and subscribe under `<topic_root>/#`, and the viewer
login subscribe only.

The status pill shows **MQTT OK** while connected, **MQTT OFFLINE** while
disconnected, and **NO BROKER** when no host is set.

### Examples

A Mosquitto broker with a login:

```toml
[mqtt]
host = "mqtt.myteam.org"
username = "storm"
```

```bash
# .env
STORM_MQTT_PASSWORD=...
```

AWS IoT Core (client certificates):

```toml
[mqtt]
host = "xxxxxxxxxxxx-ats.iot.us-east-1.amazonaws.com"
tls = "mutual"
ca_cert = "certs/AmazonRootCA1.pem"
cert_file = "certs/client.pem.crt"
key_file = "certs/client-private.pem.key"
```

The certificate's policy needs `iot:RetainPublish` as well as `iot:Publish`
for the topics STORM publishes. AWS IoT won't send retained messages on
STORM's wildcard subscriptions, so late joiners get the current scan sectors
but only new annotations, drawings and cones.

A broker on your own machine, for testing:

```toml
[mqtt]
host = "localhost"
tls = "none"
```

### Checking your broker

```bash
python scripts/check_broker.py
```

reads `storm.toml` (or the file you name, like `profiles/nssl.toml`), logs in
with `[mqtt]` and `[mqtt.viewer]`, and checks that the broker takes retained
messages, sends them on wildcard and exact-topic subscriptions, drops them when
their MQTT v5 expiry runs out, and clears them on an empty payload. Each
failure says what it means for STORM. It publishes two retained deletes under
`<root>/annotations/check-*`, which STORM clients ignore, and clears them at
the end; it takes about a minute. On AWS IoT Core every check passes except
the wildcard one.

## `[mqtt.viewer]`: a read-only login for VIEWER

Optional. VIEWER mode connects with this login, so people who don't have the
team login can still watch. Keys left out come from `[mqtt]`, so usually you
only give the parts that differ, such as a username or a client certificate.
It's always read-only, whatever `read_only` says. Its password goes in `.env`
as `STORM_MQTT_VIEWER_PASSWORD`.

Without this table, VIEWER uses the `[mqtt]` login and never publishes.
It always uses `[mqtt]`'s `topic_root`, `daily_reset_utc` and
`shared_items_url`; setting one here is an error.

The broker should enforce the read-only part (an ACL or policy that only lets
this login subscribe): STORM's code is public, so a client-side setting is
only a convenience.

```toml
[mqtt.viewer]
username = "viewer"
client_id_prefix = "viewer-"
```

## `[providers.nssl]`: NSSL's data

```toml
[providers.nssl]
enabled = true
team = true      # the NSSL profile: NSSL is this setup's own team
```

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | on (built in) | `false` leaves NSSL's provider out. Without a `[providers.nssl]` table it's loaded for ARCHIVE's NSSL button but isn't one of your default `[archive] history` / `vehicle_obs` sources |
| `team` | `false` | NSSL is this setup's own team: NSSL > VEHICLES starts on and its vehicles aren't tagged, and pre-3.0 vehicle IDs in the team's recorded traffic (`lid1`, `p1`) read as their FOFS folder names (`dltruck`, `probe1`) |

NSSL's provider is a code provider like any team's (below), loaded from
`providers/nssl/`; it ships with STORM and, since its THREDDS data is
public, is there for every team. In ARCHIVE it adds the **NSSL** button
(the `archive_drawer` hook): NSSL's recorded vehicles and scan sectors, the
FOFS mobile mesonet's one-second obs, the CLAMPS trailers, the lidar truck's
and trailers' Doppler lidar and wind profiles, its radiosondes and CLAMPS
TROPoe, all from THREDDS and none from the NSSL API. Without
`team = true`, NSSL > VEHICLES starts off and NSSL's vehicles are tagged
"NSSL" ("NSSL probe1"). The provider trusts the Sectigo intermediate
THREDDS leaves out of its TLS chain itself (its `AUTH`; a storm.toml
`[[auth]]` table for the server wins). The layer that reads the NSSL API
(live CLAMPS soundings) also needs the API's key: the profile's `[[auth]]`
table for `https://api.nssl.noaa.gov` reads `NSSL_API_KEY` from `.env`, and
the API checks it.

The rest of NSSL's setup is ordinary settings in `profiles/nssl.toml`, not
part of the provider:

- the regional mesonets and SFCOA: `[[sources]]` (`station_obs` and
  `contours`), greyed out without `NSSL_API_KEY`;
- the key and the Sectigo intermediate CA NSSL's servers leave out of their
  TLS chains: two `[[auth]]` tables;
- recorded team traffic: `[archive] history` (THREDDS);
- the day's shared items at launch: `[mqtt] shared_items_url`;
- past deployment locations (PREV LOCS): `[map] deployment_locations`;
- (no `[archive] vehicle_aliases`: NSSL's vehicles publish under their FOFS folder names, and the provider maps the pre-3.0 IDs itself).

The profile also has NSSL's broker and certificate paths: the broker CA and the read-only viewer login ship in
`providers/nssl/certs/`, and the team certificates the STORM team hands out
go in `providers/nssl/certs/team/` (git-ignored, as is a top-level `certs/`
for your own broker's files).

`NSSL_API_ROOT` (default `https://api.nssl.noaa.gov`), as an environment
variable, points the provider's API layers somewhere else, for testing. The
profile's `[[sources]]` (mesonets, SFCOA) have their URLs in the profile.

## `[providers.<name>]`: your own code provider

```toml
[providers.farm]
enabled = true
path = "~/farm-storm"
api_key_env = "FARM_API_KEY"
```

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `false` | Load the package and use what it provides |
| `path` | `providers/<name>` | The package's folder (it has an `__init__.py`). Relative to the STORM folder. Everything in `providers/` but `nssl/` is git-ignored |
| any other key | | The provider's own options, handed to its functions |

A provider's name is lower-case letters, digits and `_`, starting with a
letter. It adds sources of the `[[sources]]` kinds, `[archive] history` and
`vehicle_obs` can list it by name, and its instrument hooks feed STORM's
VAD, LIDAR, SFCOA and sounding windows. With several providers on, the first
in the file that offers a hook supplies it. A folder without
an `__init__.py` is a settings error; a package that fails to load is left
out and logged. The interface and an example are in
[providers.md](providers.md).

## `[archive]`: recorded history and vehicle obs

```toml
[archive]
recordings = "recordings"
history = ["recordings", "https://data.example.org/storm/history"]
# history_prefix = "storm"
vehicle_obs = ["vehicle-obs"]
vehicle_obs_path = "{vehicle}/{date}.csv"
# session_start_utc = 0
# session_hours = 30
# session_activity_after_hours = 12

[[archive.campaigns]]
name = "Spring 2026"
years = [2026]
```

| Key | Default | Meaning |
|-----|---------|---------|
| `recordings` | `"recordings"` | Folder the recorder writes to (`python -m archive.recorder`, or MONITOR's *File > Record Broker Traffic*) |
| `history` | the providers that are on, then `recordings` | Where ARCHIVE reads recorded broker traffic, tried in order for each file: folders, `http(s)://` URL roots (asked with their `[[auth]]` key and CA; one whose key isn't set is skipped), or the names of code providers with `history_source` |
| `history_prefix` | `topic_root`, with `/` as `.` | File-name prefix the folder and URL sources read, for recordings made under another topic root |
| `vehicle_obs` | the providers that are on | Where ARCHIVE reads one-second vehicle obs, tried in order for each vehicle: folders, `http(s)://` URL roots (asked with their `[[auth]]` key and CA; one whose key isn't set is skipped), or provider names (`nssl`: the FOFS mesonet files; a code provider with `vehicle_obs_source`). Empty, or no provider on: vehicles move on their recorded positions |
| `vehicle_obs_path` | `"{vehicle}/{date}.csv"` | Where a vehicle's file for a UTC day sits below a folder or URL root: a relative path with `{vehicle}` and `{date}` (YYYYMMDD) |
| `vehicle_aliases` | none | For loggers whose files use another name than the vehicle's broker ID: `{ p1 = "probe1" }` reads `p1`'s files as `probe1` (in `{vehicle}`, and in the files' names on the calendar). Lower case; two vehicles can't share a name |
| `[[archive.campaigns]]` | none | Field campaigns for the case finder (*Browse available cases…*): one table per campaign with `name` and `years` (a list). `profiles/nssl.toml` lists NSSL's |
| `session_start_utc` | `0` | Hour (UTC, 0-23) a session starts on the date you pick |
| `session_hours` | `30` | A session's longest length in hours; `session_start_utc + session_hours` can be at most 48, so a session ends by the end of the next UTC day |
| `session_activity_after_hours` | `12` | Hours after the start before activity (driving, annotations, cones, drawings, scan sectors) counts toward where the timeline ends; earlier activity belongs to the previous field day. Less than `session_hours` |

The session window defaults suit a field day in the Americas: 00Z to 06Z the
next day, with activity counted from 12Z. A team whose afternoons fall at
other UTC hours moves it; for example, a team in central Europe (field day
about 10Z to 20Z) might use `session_start_utc = 6`, `session_hours = 20`,
`session_activity_after_hours = 2`.

The file layouts, the sources, the calendar and the recorder are described in
[archive-data.md](archive-data.md).

## `[obs_columns]`: vehicle logger columns

Which columns of a vehicle logger's CSV file hold what, and which file in the
logger's folder is today's. VEHICLE mode's obs folder (the launch window's
data directory), the headless publisher and ARCHIVE's `vehicle_obs` files all
read files with this table. Every key is optional; the defaults read a plain
CSV named `*YYYYMMDD.csv` with an ISO 8601 `time` column and STORM's field
names as column names:

```csv
time,lat,lon,temperature_c,dewpoint_c,wind_speed_ms,wind_dir_deg,pressure_mb,heading_deg
2026-05-06T21:30:04Z,35.2134,-97.4410,27.5,18.2,6.1,205,962.4,190
```

`""` means the file has no such column.

```toml
[obs_columns]
file = "{date}_truck.csv"
timestamp = "time_utc"
temperature_c = "temp_c"
dewpoint_c = "dewpoint_c"
wind_speed_ms = "wspd"
wind_dir_deg = "wdir"
pressure_mb = ""
heading_deg = ""
```

| Key | Default | Meaning |
|-----|---------|---------|
| `file` | `"*{date}.csv"` | Today's file in the folder: a file-name pattern (`*` any characters, `?` one) where `{date}` is the UTC day as YYYYMMDD. Several matches: the shortest name. Without `{date}`: the most recently changed matching file, for loggers that keep writing the file they opened past midnight |
| `lat`, `lon` | `"lat"`, `"lon"` | Position, decimal degrees. Can't be `""` |
| `timestamp` | `"time"` | One ISO 8601 column (`2026-05-06T21:30:04Z`); a time without a zone is UTC |
| `date`, `time` | `""` | Or a UTC date column and a time column, read with `date_format` and `time_format`. Giving them without `timestamp` turns `timestamp` off |
| `date_format`, `time_format` | `"%Y%m%d"`, `"%H%M%S"` | Python [`strptime`](https://docs.python.org/3/library/datetime.html#format-codes) formats. With `%d%m%y` and `%H%M%S`, dropped leading zeros (`50625`) are put back |
| `temperature_c` | `"temperature_c"` | Air temperature, °C |
| `dewpoint_c` | `"dewpoint_c"` | Dewpoint, °C |
| `wind_speed_ms` | `"wind_speed_ms"` | Wind speed, m/s |
| `wind_dir_deg` | `"wind_dir_deg"` | Wind direction, degrees (from) |
| `pressure_mb` | `"pressure_mb"` | Pressure, hPa |
| `heading_deg` | `"heading_deg"` | Which way the vehicle faces, degrees |
| `label` | the layout's name | Its name in the launch window, with named layouts |
| `[obs_columns.units]` | none | Columns not in the units above, converted as they're read |

```toml
[obs_columns.units]
temperature_c = "F"      # C, F, K
dewpoint_c = "F"
wind_speed_ms = "kt"     # m/s, kt, mph, km/h
pressure_mb = "inHg"     # hPa, mb, kPa, Pa, inHg
```

Live, a row whose time can't be read is taken as just logged; in ARCHIVE
it's skipped.

### Named layouts

A team with more than one kind of logger names a layout for each, with the
same keys (`units` included) under `[obs_columns.<name>]`; then every key goes
in a layout. The launch window lists them under **LOGGER FILE**, and the
headless publisher picks one with `layout = "<name>"`. ARCHIVE's
`vehicle_obs` files are read with the first. `profiles/nssl.toml` has NSSL's
two:

```toml
[obs_columns.fofs]
label = "FOFS mesonet logger"
file = "*{date}.txt"
date = "gps_date"
time = "gps_time"
date_format = "%d%m%y"
temperature_c = "t_fast"
# ...

[obs_columns.gps_ka]
label = "GPS Ka (position only)"
file = "*_????????.txt"
lat = "Latitude"
lon = "Longitude"
temperature_c = ""
# ...
```

## `[map]`: home location and local time

Where the map opens, and the position STORM falls back to when your vehicle
has none yet: the radar site ARCHIVE starts on, the satellite meso sector
picked at launch, a new scan sector and the command palette's nearest-first
deployment locations.

```toml
[map]
home_lat = 35.22
home_lon = -97.44
zoom = 6
time_zones = ["America/Chicago", "America/Denver"]
deployment_locations = "locs/deployments.csv"
```

| Key | Default | Meaning |
|-----|---------|---------|
| `home_lat`, `home_lon` | `39.5`, `-98.35` (the middle of the contiguous US) | Home position, decimal degrees; set both or neither. The map starts here, and live radar starts on the nearest site |
| `zoom` | `4` | The map's starting zoom, 1-18 |
| `time_zones` | this computer's zone | The local clocks ARCHIVE's bar (and the Video Studio) show beside UTC: up to 3 IANA zone names, each shown with its abbreviation (`16:04 CDT / 15:04 MDT`) |
| `deployment_locations` | none (PREV LOCS hidden) | Past deployment locations for **PREV LOCS** (live only): a CSV file (relative to the STORM folder) or an `http(s)` URL (asked with its `[[auth]]` key). Columns `lat` and `lon`, and optionally `rank_abi`, `rank_aoi` (whole-number ranks) and `rqi` (a number) for the drawer's filters; rows without a readable position are skipped. `profiles/nssl.toml` uses `providers/nssl/locs/deployment_locations.csv` |

## `[units]`: display units

```toml
[units]
temperature = "C"
distance = "km"
```

| Key | Default | Meaning |
|-----|---------|---------|
| `temperature` | `"F"` | `"F"` or `"C"`: station plots, the vehicle panel and timeseries, and the sounding viewer's surface labels and convective temperature |
| `distance` | `"mi"` | `"mi"` or `"km"`: the measuring tool (both shown, this one first), routing, damage-path lengths, pin and radar-site distances |

Wind is always in knots (the wind-barb convention) and pressure in hPa.
Data on the wire and in files stay in °C, m/s and hPa whatever this says
([wire-format.md](wire-format.md)).

## `[[sources]]`: your own data

```toml
[[sources]]
id = "farm"
kind = "station_obs"
name = "Farm Network"
url = "https://data.example.org/farm/current.json"
api_key_env = "FARM_API_KEY"
rows = "results"
fields = { station = "id", lat = "lat", lon = "lon", temperature_c = "t" }
units = { temperature_c = "F" }
```

One table per feed: station plots in SURFACE (`kind = "station_obs"`),
GeoJSON on the map (`kind = "overlay"`, also in ARCHIVE with
`archive = true`) or soundings in the Skew-T viewer (`kind = "sounding"`),
fetched every `refresh_min`. Every
key, the response layouts STORM reads and worked examples are in
[data-sources.md](data-sources.md). API keys go in `.env` under the name
`api_key_env` gives. A source on a server with an `[[auth]]` table (below)
can leave out `api_key_env` and `ca_cert`.

## `[[auth]]`: keys and certificates by server

```toml
[[auth]]
url = "https://api.example.org"
api_key_env = "EXAMPLE_API_KEY"
ca_cert = "certs/example-intermediate.pem"
```

How STORM asks one server (or one folder on it), wherever the URL comes
from: `[[sources]]` without their own key, `[archive] history` and
`vehicle_obs` URLs, `[mqtt] shared_items_url`, `[map] deployment_locations`,
a code provider's fetchers that look it up (`data/auth.py`), case-package
downloads and the source check script. Each table covers the URLs that
start with its `url` (that URL, or below it); the longest match wins. A key
is never sent to a URL no table covers, so a public station list next to a
keyed API gets no key.

| Key | Default | Meaning |
|-----|---------|---------|
| `url` | (required) | The server or folder it covers: an `http(s)` URL. `https://api.example.org` covers `https://api.example.org/data/x.json`, not `https://api.example.org.other.com` |
| `api_key_env` | none | Environment variable (or `.env` key) holding the key. A URL whose key isn't set is still asked (without it), except `[archive] history`, which skips it |
| `api_key_header` | `"X-API-Key"` | Header the key is sent in |
| `api_key_param` | none | Send the key as this query parameter instead of a header |
| `ca_cert` | none | Extra CA certificate to trust for this server, for a chain your system doesn't trust on its own (a server that leaves out an intermediate). Relative to the STORM folder |

A table needs `api_key_env`, `ca_cert` or both. `profiles/nssl.toml` has one
for the NSSL API (its key and CA) and one for NSSL's THREDDS server (the CA).

## `.env`: secrets

`KEY=VALUE` lines, one per line; `#` starts a comment. A real environment
variable of the same name takes precedence. See `.env.example`.

| Variable | Used for |
|----------|----------|
| `STORM_MQTT_PASSWORD` | `[mqtt]` password |
| `STORM_MQTT_VIEWER_PASSWORD` | `[mqtt.viewer]` password |
| `NSSL_API_KEY` | The NSSL API, through `profiles/nssl.toml`'s `[[auth]]`: its layers (with `[providers.nssl]` on), mesonets, history and shared items |
| any name `api_key_env` gives | That `[[auth]]` table's or `[[sources]]` feed's key (or a code provider's, by its own option) |
| `ORS_API_KEY` | openRouteService routing |
| `STORM_ADMIN_PASSPHRASE_HASH` | Admin mode passphrase hash; empty turns admin mode off |

## Headless publisher

The headless publisher (`headless/`) reads its own `storm-headless.toml`, but
its `[mqtt]` table has exactly the keys above (the GUI and the publisher parse
it with the same code), plus `client_id`, which defaults to
`<client_id_prefix><vehicle_id>-pi`. It reads the same `[obs_columns]` table
too, and `layout` picks one of its named layouts. Relative paths there resolve against
that file's folder. Its `install.sh` writes the table from `--host`,
`--port`, `--tls` and `--topic-root`, or from a profile: a shipped one with
`--profile nssl`, or your own settings file with `--profile-file PATH`
(whose `[obs_columns]` it copies too). See
[`headless/README.md`](../headless/README.md).

## Command-line overrides

`--disable-mqtt` runs without the broker whatever `storm.toml` says.
`--mqtt-no-tls` connects without TLS (`tls = "none"`), for debugging a local
broker only.
