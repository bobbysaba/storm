# MQTT wire format

What STORM sends over the broker, for anyone writing their own publisher or
subscriber (a logger on a vehicle, a dashboard, a recorder). STORM's own
headless publisher (`headless/`) is a working example for vehicles.

Every message is an MQTT v5 publish with a UTF-8 JSON object as its payload.
STORM sets an MQTT v5 message expiry on everything it publishes, so a broker
that supports expiry drops stale messages on its own.

## Topics

Every topic sits under one root, `topic_root` in `storm.toml`'s `[mqtt]` table
(default `storm`; see [configuration.md](configuration.md)). Every STORM
client and publisher on a team must use the same root. Below, `<root>` stands
for it.

| Topic | Holds | QoS | Retained | Expiry |
|-------|-------|-----|----------|--------|
| `<root>/vehicles/<vehicle_id>` | A vehicle's latest position and observations | 1 | no | 3 hours |
| `<root>/annotations/<annotation_id>` | A point annotation (road closure, flooding, H/L and so on) | 1 | yes | until the daily reset |
| `<root>/drawings/<drawing_id>` | A front, dryline, polyline or polygon | 1 | yes | until the daily reset |
| `<root>/cones/<cone_id>` | A storm-motion cone | 1 | yes | 1 hour |
| `<root>/scan_sectors/<vehicle_id>` | Where a vehicle is collecting data right now | 1 | yes | until the daily reset |

The daily reset is the next `daily_reset_utc`:00 UTC, from `[mqtt]` (default
8, so 08:00 UTC; see [configuration.md](configuration.md)). Pick an hour
between your field days so the day's items clear overnight.

STORM subscribes to `<root>/<kind>/+` for each kind, so the last level is the
item's ID and there are no deeper levels. A message on any other topic is
ignored.

IDs in topics may only use letters, digits and `-` (no `/`, `+` or `#`).
STORM's own IDs are lowercase: vehicle IDs are `[a-z0-9-]`, and annotation,
drawing and cone IDs are 8 hex characters.

Who publishes:

| Mode | Publishes | Subscribes |
|------|-----------|------------|
| VEHICLE | its own vehicle (every 5 s), annotations, drawings, cones, its scan sector | everything |
| MONITOR | annotations, drawings, cones | everything |
| VIEWER | nothing | everything |
| ARCHIVE | nothing (no broker connection) | nothing |
| headless publisher | its own vehicle (every `publish_s`, default 5 s) | nothing |

## Retained messages

Everything except vehicles is published retained, so a client that connects
late gets the current annotations, drawings, cones and scan sectors from the
broker as soon as it subscribes. Deletes are retained too (see below): a
client that was offline when an item was deleted hears the delete when it
reconnects. Each retained message carries the item's expiry, so a broker that
supports MQTT v5 expiry drops it at the daily reset (an hour for cones).

An edit republishes the item on the same topic, replacing the retained copy.
An empty retained payload on an item's topic is also read as a delete (or, for
a scan sector, as `active: false`), so the standard MQTT way of clearing a
retained message works too.

STORM subscribes with wildcards (`<root>/<kind>/+`), and also to each
vehicle's own `<root>/scan_sectors/<vehicle_id>` the first time it hears from
that vehicle. Mosquitto, EMQX and HiveMQ send retained messages on wildcard
subscriptions, so everything arrives at once. **AWS IoT Core doesn't**: it
only sends them on an exact-topic subscription. There, scan sectors still
arrive (within one vehicle publish, about 5 s), but annotations, drawings and
cones published before you connected don't, since their IDs aren't known in
advance. `[mqtt] shared_items_url` fills that gap: a URL serving a snapshot
of the day's annotations, drawings and cones, fetched at launch and by the
annotation drawer's Refresh button. It's one JSON object of payloads as
published, keyed by item ID; items with `"deleted": true` are skipped. NSSL's
profile points it at the NSSL API's `current.json`. Publishing retained on
AWS IoT needs `iot:RetainPublish` in the certificate's policy.

`python scripts/check_broker.py` checks your broker for all of this (see
[configuration.md](configuration.md#checking-your-broker)).

## Timestamps

Annotations, drawings, cones and scan sectors use ISO 8601 with a UTC offset,
as Python's `datetime.isoformat()` writes it: `2026-05-06T18:00:00.123456+00:00`.
`Z` is accepted for scan sectors. Always include the offset: a timestamp
without one has no time zone and can sort or display wrong.

Vehicles may send either an ISO 8601 `timestamp` or two strings, `gps_date`
(`DDMMYY`) and `gps_time` (`HHMMSS`), both UTC. STORM itself sends the
second form.

## Vehicles

`<root>/vehicles/<vehicle_id>`

| Field | Also accepted | Type | Units | Required | Meaning |
|-------|---------------|------|-------|----------|---------|
| `vehicle_id` | | string | | yes | Must match the topic's last level. Shown on the map and used to tell vehicles apart. |
| `lat` | | number | degrees north | yes | Latitude |
| `lon` | | number | degrees east | yes | Longitude (negative in the western hemisphere) |
| `timestamp` | `gps_date` + `gps_time` | string | ISO 8601 (no offset means UTC); or `DDMMYY` + `HHMMSS` UTC | recommended | Time of the fix |
| `icon_type` | | string | | no | Map icon: `car`, `drone`, `mesonet`, `lidar`, `radar` or `hailcam`. Unknown or missing shows `car`. |
| `temperature_c` | `t_fast` | number | °C | no | Air temperature |
| `dewpoint_c` | `dewpoint` | number | °C | no | Dewpoint |
| `wind_speed_ms` | `wspd` | number | m/s | no | Wind speed |
| `wind_dir_deg` | `wdir` | number | degrees, direction the wind blows from | no | Wind direction |
| `pressure_mb` | `pressure` | number | hPa (mb) | no | Station pressure |

Use either name for a field; when a payload has both, the first column wins.
The second-column names come from NSSL's FOFS truck logger. STORM and the
headless publisher send those today, so past recordings and older tools keep
working, and every STORM client reads both.

Without a time (or when it doesn't parse), the receiver stamps the message
with its own clock, which makes trails and the timeseries wrong after any
delay, so send one. Leave out a met field rather than sending
`null` or a placeholder when the vehicle doesn't measure it; a GPS-only
vehicle sends just the position and time.

STORM publishes the vehicle's newest observation only; while the broker is
unreachable the headless publisher keeps the latest one and sends it on
reconnect instead of replaying a backlog.

```json
{
  "vehicle_id": "probe1",
  "lat": 35.2,
  "lon": -97.4,
  "gps_date": "060526",
  "gps_time": "180000",
  "icon_type": "mesonet",
  "wspd": 5.0,
  "wdir": 210.0,
  "t_fast": 25.5,
  "dewpoint": 18.0,
  "pressure": 965.2
}
```

A publisher using the generic names:

```json
{
  "vehicle_id": "probe2",
  "lat": 35.2,
  "lon": -97.4,
  "timestamp": "2026-05-06T18:00:00Z",
  "temperature_c": 25.5,
  "wind_speed_ms": 5.0,
  "wind_dir_deg": 210.0
}
```

## Annotations

`<root>/annotations/<annotation_id>`

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Must match the topic's last level |
| `type_key` | string | yes | `road_closure`, `construction`, `flooded`, `downed_lines`, `debris`, `high_pressure`, `low_pressure` or `fork` |
| `lat`, `lon` | number | yes | Position, degrees |
| `label` | string | no | Text shown with the marker; defaults to the type's name |
| `creator` | string | no | Who placed it; STORM sends its vehicle ID |
| `created_at` | string | no | ISO 8601 UTC; defaults to when it was received |
| `ttl_hours` | number or null | no | Carried along but not acted on yet |

```json
{
  "id": "3f9a1c2e",
  "type_key": "flooded",
  "label": "Flooded Road",
  "lat": 35.21,
  "lon": -97.44,
  "creator": "chase1",
  "created_at": "2026-05-06T18:00:00+00:00",
  "ttl_hours": null
}
```

An edit republishes the whole object on the same topic.

## Drawings

`<root>/drawings/<drawing_id>`

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `id` | string | yes | Must match the topic's last level |
| `drawing_type` | string | yes | `cold_front`, `warm_front`, `stationary_front`, `occluded_front`, `dryline`, `polyline` or `polygon` |
| `coordinates` | array of `[lon, lat]` | yes | The line's vertices, in GeoJSON order (longitude first). A polygon is not closed: the first point isn't repeated. |
| `title` | string | no | Defaults to the type's name |
| `creator` | string | no | Who drew it; STORM sends its vehicle ID |
| `created_at` | string | no | ISO 8601 UTC |
| `flipped` | boolean | no | Fronts only: draw the symbols on the other side of the line. Default `false`. |
| `color` | string | no | `#RRGGBB`; defaults to the type's color |
| `line_style` | string | no | `solid` (default), `dashed` or `dotted` |

```json
{
  "id": "b41d07aa",
  "drawing_type": "cold_front",
  "coordinates": [[-98.1, 36.0], [-97.6, 35.4], [-97.3, 34.9]],
  "title": "Cold Front",
  "creator": "chase1",
  "created_at": "2026-05-06T18:05:00+00:00",
  "flipped": false,
  "color": "#4A9EFF",
  "line_style": "solid"
}
```

## Storm-motion cones

`<root>/cones/<cone_id>`

| Field | Type | Units | Required | Meaning |
|-------|------|-------|----------|---------|
| `id` | string | | yes | Must match the topic's last level |
| `lat`, `lon` | number | degrees | yes | The storm's position at `valid_at` |
| `heading` | number | degrees | yes | Direction the storm moves **from** (meteorological convention) |
| `speed_kts` | number | knots | yes | Storm speed |
| `creator` | string | | no | Who made it; STORM sends its vehicle ID |
| `created_at` | string | ISO 8601 UTC | no | When it was made |
| `valid_at` | string | ISO 8601 UTC | no | The time the position applies to; the 15/30/45/60-minute labels count from here. Defaults to `created_at`. |

Receivers draw the cone themselves (a fixed ±20° spread out to one hour), so
only these fields travel.

```json
{
  "id": "c0ffee12",
  "lat": 35.5,
  "lon": -98.2,
  "heading": 240,
  "speed_kts": 30,
  "creator": "chase1",
  "created_at": "2026-05-06T18:10:00+00:00",
  "valid_at": "2026-05-06T18:08:00+00:00"
}
```

## Deleting an annotation, drawing or cone

Publish this on the item's own topic:

```json
{"id": "3f9a1c2e", "deleted": true, "deleted_at": "2026-05-06T19:00:00+00:00"}
```

Any payload with `"deleted": true` removes the item with that `id`; the other
fields are ignored. STORM publishes deletes retained, with the same expiry as
the item, so the delete replaces the item on the broker. An empty retained
payload on the item's topic also deletes it.

## Scan sectors

`<root>/scan_sectors/<vehicle_id>`, retained. One per vehicle: each publish
replaces that vehicle's sector. An empty payload hides it, like
`"active": false`.

| Field | Type | Units | Required | Meaning |
|-------|------|-------|----------|---------|
| `vehicle_id` | string | | yes | Must match the topic's last level |
| `active` | boolean | | no | `false` hides the sector (how a vehicle stops showing one). Default `true`. |
| `mode` | string | | no | `point` (default), `circle` or `sector` |
| `lat`, `lon` | number | degrees | no | Center. Default 0, so send them. |
| `timestamp` | string | ISO 8601 UTC | no | When it was set; defaults to when it was received |
| `follow_vehicle` | boolean | | no | Move the center with the vehicle's position. Defaults to `true` for `point`, else `false`. |
| `range_m` | number | m | `circle`, `sector` | Outer radius |
| `inner_range_m` | number | m | no | Inner radius (a ring or a cut-off wedge). Default 0. |
| `azimuth_deg` | number | degrees | `sector` | Direction the sector points (center of the beam) |
| `beam_width_deg` | number | degrees | `sector` | Full width of the sector |
| `color` | string | | no | `#RRGGBB`, default `#00CFFF` |
| `label` | string | | no | Defaults to the vehicle ID |

A `circle` without `range_m`, or a `sector` without `range_m`, `azimuth_deg`
and `beam_width_deg`, draws nothing.

```json
{
  "vehicle_id": "lidar1",
  "active": true,
  "mode": "sector",
  "lat": 35.3,
  "lon": -97.6,
  "timestamp": "2026-05-06T18:15:00+00:00",
  "follow_vehicle": false,
  "range_m": 8000,
  "azimuth_deg": 300,
  "beam_width_deg": 60,
  "color": "#00CFFF"
}
```

## Client IDs

STORM connects with `client_id_prefix` + its vehicle ID, and the headless
publisher with `<client_id_prefix><vehicle_id>-pi`, so a laptop and a Pi on
the same vehicle don't knock each other off the broker. Two clients with the
same ID do: most brokers disconnect the older one.

## Recorded history

ARCHIVE mode replays recorded broker traffic: one JSON Lines file per topic
kind per UTC day, named `<root>.<kind>.<YYYYMMDD>` (for example
`storm.vehicles.20260506`), each line one payload exactly as above. STORM's
recorder (`python -m archive.recorder`, or MONITOR's *File > Record Broker
Traffic*) writes them, and NSSL's recorder writes the same files. See
[archive-data.md](archive-data.md) for the layout, where STORM reads them
from, and how to record your own.
