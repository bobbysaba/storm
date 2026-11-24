# Setting up a group's data: a guide for AI agents

This page is for an AI coding agent helping a team connect its own data to
STORM. Work through the questionnaire with the person, then write
`[[sources]]` tables in their `storm.toml` or, when a table can't describe a
feed, a code provider. Reference: [data-sources.md](data-sources.md) (every
`[[sources]]` key), [providers.md](providers.md) (the code-provider
interface), [configuration.md](configuration.md) (the rest of
`storm.toml`), [archive-data.md](archive-data.md) (ARCHIVE data) and
[wire-format.md](wire-format.md) (the MQTT broker).

## Ground rules

- Edit the person's `storm.toml` (or the file `STORM_CONFIG` points to),
  never `profiles/`, which ships with STORM. Code providers go in
  `providers/<name>/` (git-ignored) or a folder outside the checkout. Don't
  commit either.
- Secrets go in `.env` (`NAME=value`), referenced by name
  (`api_key_env = "NAME"`). Never write a key into `storm.toml`, a provider's
  code, or the conversation. Ask the person to put it in `.env` themselves.
- Prefer a `[[sources]]` table. Write a code provider only when one of the
  questions below says a table can't do it.
- Before writing a table, fetch one real response (with the person's key,
  from their machine) and read its layout. Don't guess field names.
- After writing, load the settings to check them (below), and say what you
  couldn't check, such as a key you didn't have.

## The questionnaire

Ask these for each feed. The answers map onto keys.

### 1. What is it?

| Answer | Kind |
|--------|------|
| Current readings at fixed stations (temperature, wind, pressure) | `station_obs` |
| Shapes on the map: areas, routes, points of interest | `overlay` |
| Vertical profiles: radiosondes, drones, retrievals | `sounding` |
| Gridded fields drawn as contour lines, run by run (a mesoanalysis, a model), already cut into vector tiles | `contours` (one per settings file; the files must follow the layout in [data-sources.md](data-sources.md#contours-contour-lines-from-vector-tiles)) |
| Positions and obs of the team's own moving vehicles | not a source: the MQTT broker ([wire-format.md](wire-format.md)) live, and `[archive] vehicle_obs` with `[obs_columns]` in ARCHIVE ([archive-data.md](archive-data.md)) |
| Recorded broker traffic from past days | `[archive] history` ([archive-data.md](archive-data.md)) |
| Radar, satellite, model data, NWS products | STORM has these built in; check the user guide before adding anything |

### 2. Where does it live?

- One URL that returns everything current: `url`.
- A list of files, each a profile (soundings): `url` is the index, and
  `launches` and `[sources.launch_fields]` find each file.
- One "latest" profile file: `index = false`.
- Station positions in a separate list: `stations_url`.
- Paged results, several requests that have to be joined, or a URL that
  needs a value from an earlier response: **code provider**.
- A local file, a database, a socket: **code provider**.

### 3. How is it authenticated?

| Answer | Keys |
|--------|------|
| Public | none |
| A key in a header | `api_key_env`, plus `api_key_header` if it isn't `X-API-Key` |
| A key in the query string | `api_key_env`, `api_key_param` |
| A certificate chain the system doesn't trust | `ca_cert` (a PEM file in the STORM folder) |
| Several sources on one keyed server | one `[[auth]]` table for the server (`url`, `api_key_env`, `ca_cert`) instead of the keys on each source |
| A login that returns a token, OAuth, cookies, request signing | **code provider** |

### 4. What does a response look like?

Get a sample, then:

- **Format**: JSON or CSV (`format`). XML, NetCDF, binary: **code provider**.
  For soundings: SHARPpy/SPC text (`format = "sharppy"`), CSV or JSON.
- **Layout** (`station_obs`): a list of station objects (`shape = "rows"`),
  an object keyed by station (`"keyed"`) or an object per variable
  (`"columns"`), at the path `rows`.
- **Layout** (sounding JSON): a list of level objects (`shape = "rows"`) or
  an object of arrays (`"arrays"`), at the path `rows`.
- **Fields**: which key holds each of STORM's fields (`[sources.fields]`;
  dotted paths for nested objects, a list to try several).
- **Units**: anything not °C, m/s, hPa, m (`[sources.units]`).
- **Missing values**: the codes used for "no reading" (`missing`).
- **Times**: the format (`time_format` when it isn't ISO 8601 or Unix
  seconds) and zone (`timezone` when it has none and isn't UTC); one time
  for the whole response (`time`) or one per station.

### 5. How often does it change, and how old is too old?

- `refresh_min`: how often to fetch it (no faster than the feed updates, and
  within any rate limit).
- `fresh_min` (`station_obs`): minutes until a station's dot turns amber,
  then red.
- `lookback_hours` (`sounding`): how far back the time slider goes.
- Does an overlay stay the same from day to day? Then `archive = true`
  shows it in ARCHIVE as well.

### 6. Where should the button go?

The kind's drawer by default; `where = "toolbar"` with an `icon` for a
`station_obs` or `overlay` source the team uses all the time. Pick `name`
(tooltips, the layer list) and `short` (the button label, a few capitals).

## Writing a code provider

When a question above says "code provider", follow
[providers.md](providers.md):

1. Create `providers/<name>/__init__.py` (or a folder the person picks, with
   `path = ...` in the table).
2. Define `sources(options)` returning `code_source(kind, id, fetch, ...)`
   entries. Each `fetch()` does the requests and returns rows in STORM's
   field names and units. Do the unit conversions in the provider.
3. Read keys with `os.environ.get(options["api_key_env"])`; put the
   variable's name in the table, not the key.
4. Add `[providers.<name>] enabled = true` and any options to `storm.toml`.
5. Use the standard library and the packages already in `envs/storm.yml`.
   If you need another package, tell the person; it has to go in their
   environment.

## Checking your work

```bash
python -c "import settings; s = settings.SETTINGS; print(s.error or 'ok'); print([(x.id, x.kind) for k in ('station_obs', 'overlay', 'sounding', 'contours') for x in s.sources_of(k)])"
```

prints `ok` and every source, or every problem in `storm.toml`, one per
line. STORM still starts with problems (it skips what it can't use), so
fix them all: a skipped key or a left-out source is easy to miss in the app.

```bash
python scripts/check_data_sources.py <id or word>
```

asks each matching `[[sources]]` URL in `storm.toml` whether it still
answers (no `api_key_env` keys sent). Then have the person start STORM, turn the source on, and look at
the status line. A code provider that failed is in `storm_errors.log`.
