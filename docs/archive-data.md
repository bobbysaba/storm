# Archive data

ARCHIVE mode replays a past day. Radar, satellite, hazards, observed
soundings and ASOS come from public national archives and need no setup.
The team's own traffic (vehicle positions and observations, annotations,
drawings, storm-motion cones and scan sectors) comes from **recorded broker
traffic**, which you record yourself or read from somewhere that already
has it. Vehicles can also replay on their loggers' own one-second files
(**vehicle obs**), which are denser than what they published.

This page covers how both are laid out, how STORM finds them, how the
launch calendar learns which days have data, and how to record your own.
The payloads themselves are described in [wire-format.md](wire-format.md);
the settings in
[configuration.md](configuration.md#archive-recorded-history-and-vehicle-obs).

## Files

One file per topic kind per UTC day, in JSON Lines (one JSON object per
line):

```
<prefix>.<kind>.<YYYYMMDD>
```

- `<prefix>` is the topic root (`topic_root` in `[mqtt]`, default `storm`),
  with any `/` replaced by `.`: a root of `team/storm` gives
  `team.storm.vehicles.20260506`. `[archive] history_prefix` reads files made
  under another root.
- `<kind>` is one of `vehicles`, `annotations`, `drawings`, `cones`,
  `scan_sectors`.
- `<YYYYMMDD>` is the UTC day the messages arrived.
- The name has no extension. A name ending in `.jsonl` is read too, for
  files you make by hand.

For example:

```
recordings/
├── storm.vehicles.20260506
├── storm.annotations.20260506
├── storm.cones.20260506
├── storm.drawings.20260506
└── storm.scan_sectors.20260506
```

A kind with nothing that day can be left out. Each line is one payload as it
was published (see [wire-format.md](wire-format.md)), for example:

```json
{"vehicle_id": "probe1", "lat": 35.18, "lon": -97.44, "gps_date": "060526", "gps_time": "213004", "t_fast": 27.1, "dewpoint": 18.4}
```

Lines don't need to be in order. STORM places each one by the time in the
payload, not by its place in the file, so a line without a time it can read
is skipped:

| Kind | Time field |
|------|------------|
| vehicles | `timestamp` (ISO 8601), else `gps_date` + `gps_time` |
| scan sectors | `timestamp` |
| annotations, drawings, cones | `created_at`, else `deleted_at` |

Lines that aren't JSON are skipped as well.

### How a day replays

A session runs from 00Z on the chosen date until as late as 06Z the next
morning (`[archive] session_start_utc` and `session_hours` move that window;
[configuration.md](configuration.md#archive-recorded-history-and-vehicle-obs)),
so STORM reads the chosen day's files and the next day's, keeping the next
day's records up to the session's end.

- **Vehicles** and **scan sectors**: the latest record for each vehicle at
  or before the playback time.
- **Annotations, drawings, cones**: an item appears at its `created_at`, takes
  each later record with the same `id` as an edit, and goes away at a
  record with `"deleted": true` or once its `expires_at` passes (cones last an
  hour).

## Where STORM looks

`[archive] history` in `storm.toml` lists the sources, tried in order for
each file; the first that has it wins:

```toml
[archive]
history = ["recordings", "https://data.example.org/storm/history"]
```

| Entry | Source |
|-------|--------|
| a folder | A folder on this machine, relative to the STORM folder unless absolute |
| `http://…` or `https://…` | A URL root: STORM fetches `<root>/<file name>`, with the key and CA of the `[[auth]]` table covering it ([configuration.md](configuration.md#auth-keys-and-certificates-by-server)); a root whose key isn't set is skipped. A 404 means the source doesn't have that file; any other error makes it unknown |
| a provider's name | A code provider's `history_source` ([providers.md](providers.md)); skipped with a warning when it's off |

A folder that's named like a provider needs a path: `"./farm"`.

Without `history`, STORM reads the providers that are on (those with a
`history_source`) and then the recorder's folder (`[archive] recordings`,
default `recordings/`). List a shared server before your own folder, as the
NSSL profile does, so a partial local recording of a day doesn't hide the
full one.

Since the first source with a file wins, sources aren't merged: if a folder
has only part of a day, the rest of that day isn't read from the next source.

If no source has a file and at least one couldn't be reached, STORM reports
the kind as failed rather than empty, since the file may exist. A failure on
the next morning's file is only logged.

### Case packages

Recorded files are packed into case packages (*Recorded vehicles, annotations
and scan sectors*) like any other download, by their URL; a folder's files
by their `file://` path. A package opened on another machine serves them
too: a folder file is matched by its full path, else by its name inside the
folder (`storm.vehicles.20260506`). If the package holds that name from more
than one folder, STORM reads this machine's own folder instead.

### Serving your own

Any web server that serves a folder of these files works as a URL source,
for example `python -m http.server` in the folder, or a bucket with public
reads. STORM sends no credentials to URL sources, so a URL source should
only serve data you're happy to make public, or be reachable only on your
network.

## Vehicle obs files

A vehicle's logger file for a day is a CSV with a header row, read with
the `[obs_columns]` map in `storm.toml` (the same one live obs files use;
see [configuration.md](configuration.md#obs_columns-vehicle-logger-columns);
with named layouts, the first). The defaults read a plain CSV with STORM's
field names:

```
time,lat,lon,temperature_c,dewpoint_c,wind_speed_ms,wind_dir_deg,pressure_mb,heading_deg
2026-05-06T21:30:04Z,35.18,-97.44,22.82,19.5,2.3,215.2,977.29,184.0
```

NSSL's FOFS logger files are the profile's `fofs` layout (`gps_date`,
`gps_time`, `t_fast` and so on). The layout's `file` key is for live
logger folders only; here `vehicle_obs_path` says where each file is.

and, for example, `timestamp = "time"` with the met columns renamed reads:

```
time,latitude,longitude,temp_c
2026-05-06T21:30:04Z,35.18,-97.44,27.1
```

Units are °C, m/s, hPa and degrees; STORM doesn't convert them.

### Layout

One file per vehicle per day, below a folder or URL root at
`[archive] vehicle_obs_path` (default `{vehicle}/{date}.csv`):

```
vehicle-obs/
├── wx1/
│   ├── 20260505.csv
│   └── 20260506.csv
└── wx2/
    └── 20260506.csv
```

- `{vehicle}` is the vehicle ID, as it appears in the recorded traffic.
  STORM uses the recorded vehicle's icon; a vehicle that only has files gets
  the default icon.
- `{date}` is `YYYYMMDD`. STORM reads the files dated the day before the
  session, the session's day and the next day, and keeps every row whose
  time falls in the session (00Z to 06Z the next morning by default), so a logger that
  names its files by the crew's local day still works.
- A flat folder works too: `vehicle_obs_path = "{vehicle}_{date}.csv"`.

### Where STORM looks

`[archive] vehicle_obs` lists the sources, tried in order **for each
vehicle**; the first one with rows for that vehicle in the session wins:

```toml
[archive]
vehicle_obs = ["vehicle-obs", "https://data.example.org/storm/obs", "nssl"]
```

| Entry | Source |
|-------|--------|
| a folder | A folder on this machine, relative to the STORM folder unless absolute. Its vehicles are the names it finds for the session's dates |
| `http://…` or `https://…` | A URL root: STORM fetches `<root>/<vehicle_obs_path>`, with its `[[auth]]` key and CA; a root whose key isn't set is skipped. Its vehicles come from `dates.json` (below); without one, only vehicles in the recorded traffic are asked for |
| `nssl` | NSSL's FOFS mobile mesonet files on THREDDS, with the repairs those files need; needs `[providers.nssl]` on |

Without `vehicle_obs`, STORM reads the providers that are on (`nssl` in the
NSSL profile), so other teams start with none.

A logger whose files use another name than the vehicle's broker ID (`probe1`
for `p1`) is matched by `[archive] vehicle_aliases = { p1 = "probe1" }`:
`{vehicle}` becomes `probe1` for `p1`, and the files found under `probe1`
count as `p1`'s. With no source, or for a
vehicle none of them has, vehicles move on their recorded positions and the
archive bar reads **OBS: MQTT fallback**.

A source that can't be read is skipped for the next one; a vehicle that no
source had and at least one couldn't be read is reported as failed.

### What STORM checks

Every source's rows get the same checks before they're shown:

- rows without a readable time or position are skipped, as are fixes at or
  near 0,0;
- readings at or below −900 (missing-value codes), a wind speed or
  direction of 999 or more, values no sensor could read (for example a
  temperature outside −60 to 60 °C or a pressure outside 500–1100 hPa), and
  a dewpoint more than 1 °C above the temperature are left empty;
- where two rows share a second, the one that continues the track is kept;
- short stretches the position jumps away to and straight back from faster
  than a vehicle can drive are dropped, as are frozen positions right beside
  such a jump.

### Case packages

These files go into case packages as *Vehicle obs*, with the same rules as
recorded traffic above: a folder file from another machine is matched by its
path inside the folder (`wx1/20260506.csv`).

## The launch calendar

While ARCHIVE is selected, the launch window shades the days with data, and
*Browse available cases…* lists them by vehicle. The days come from the
sources above:

| Source | Days |
|--------|------|
| a `history` folder | The dates in its `<prefix>.<kind>.<YYYYMMDD>` file names, under RECORDED TRAFFIC |
| a `vehicle_obs` folder | Each vehicle's dates in its file names, one entry per vehicle under VEHICLES |
| a URL root (either list) | Its `dates.json`, if it has one |
| `nssl` in `vehicle_obs` | The FOFS vehicles, under MOBILE MESONET |

Folders are listed every time the window opens; URL and NSSL dates are kept
for a day (an hour for a source with dates this year).

A URL root can't be listed, so give it a `dates.json` to put its days on the
calendar:

```json
{
  "dates": ["20260505", "20260506"],
  "vehicles": {"wx1": ["20260506"], "wx2": ["20260505", "20260506"]}
}
```

Both keys are optional. `dates` is for recorded-traffic roots; `vehicles`
also tells ARCHIVE which vehicles a vehicle-obs root has. Without the file,
the root's days aren't shaded, but picking one still loads it.

Campaigns in the case finder are the `[[archive.campaigns]]` tables (`name`,
`years`) in `storm.toml`.

## Recording your own

The recorder subscribes to `<root>/<kind>/+` for every kind and writes each
message to the day's file in `[archive] recordings` (default `recordings/`,
git-ignored). Files are appended to, so stopping and restarting on the same
day keeps what's there. A recording holds every vehicle's track for the day,
so keep it as private as the broker itself
([broker.md](broker.md#who-can-see-what)).

### As a command

Run it on any machine that stays connected through the field day:

```bash
python -m archive.recorder
```

`--folder` writes somewhere other than `[archive] recordings`; `-v` turns on
debug logging. It stops on Ctrl-C or SIGTERM, so a service manager can run it.
If it can't connect, its log says why (a refused login, an untrusted
certificate, nothing listening) and it keeps retrying.

It uses the VIEWER login (`[mqtt.viewer]` on top of `[mqtt]`, see
[configuration.md](configuration.md)) and only subscribes, so a read-only
login is enough. Its client ID is `<client_id_prefix>recorder-<hostname>`,
so two recorders on different machines don't knock each other off the
broker.

### From MONITOR

In MONITOR mode, **File > Record Broker Traffic** records with the same code
while the window is open. The choice is remembered for the next MONITOR
launch.

### What gets written

- Each message is written as published, on one line (a payload that spans
  lines is rewritten as one).
- Retained copies the broker hands a new subscription are skipped: they were
  recorded when they were first published, if the recorder was running.
- An empty payload, the standard way to clear a retained message, is written
  as the delete it means: `{"id": …, "deleted": true, "deleted_at": …}`, or
  `{"vehicle_id": …, "active": false, "timestamp": …}` for a scan sector.
  An empty vehicle payload is skipped.
- Messages on other topics, payloads that aren't a JSON object, and topics
  with extra levels are skipped.

Two recorders on the same broker write the same messages twice if they share
a folder. Duplicate lines are harmless (an item's record is applied twice),
but one recorder per folder is simpler.

## NSSL

NSSL's archive data is public on its THREDDS server, so it's in every
team's ARCHIVE under the **NSSL** button (`providers/nssl/archive/drawer.py`),
and none of it comes from the NSSL API. NSSL's recorder writes
`storm.<kind>.<YYYYMMDD>` files, mirrored on THREDDS. The NSSL profile's
`history` lists that root, then your own `recordings/`; for other teams NSSL
> VEHICLES reads it itself (vehicles and scan sectors only).

Files are found through indexes NSSL publishes on THREDDS
(`providers/nssl/indexes.py`): either a table,
`{"files": {"<platform>": {"YYYYMMDD": ["path", ...]}}}`, or a list of
`{"path": ...}` entries like the mesonet's `manifest.json` below (the
platform is the path's first folder, the day the 8-digit date in the file
name, unless an entry gives `"platform"` and `"date"`), with paths relative
to the index's folder. The lidar truck's radiosondes (under
`dltruck`) and CLAMPS TROPoe (under `CLAMPS1` / `CLAMPS2`) each have one;
until an index is published its folders are listed instead.

The `nssl` vehicle-obs source reads the FOFS mobile mesonet's
`raw/<YYYYMMDD>.txt` files from THREDDS, wherever they sit in that tree:
NSSL rebuilds `FOFS/Mobile-Mesonet/data/manifest.json` every hour, a list
of every file under `data/` that dates the launch calendar; STORM re-reads
it after an hour. Without it STORM
reads each vehicle's `data/<vehicle>/raw/` folder and leaves FOFS days off
the calendar. It repairs what the published files get wrong:
garbled and day/month-swapped `gps_date` values, files filed under a
swapped date, relative humidity filed as dewpoint, and stretches listed in
`providers/nssl/archive/fofs_known_corrections.py`. NSSL's vehicles publish
under their FOFS folder names (`dltruck`, `probe1`…); recordings from before
3.0 used `lid1` and `p1`–`p7`, which the provider reads as those names
(`providers/nssl/archive/vehicle_aliases.py`), so no `[archive]
vehicle_aliases` is needed. Its
NSSL recorded history isn't on the calendar.
