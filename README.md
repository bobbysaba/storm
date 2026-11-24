# STORM

**Severe Thunderstorm Observation and Reconnaissance Monitor**

A field operations tool for teams that work around severe storms: real-time situational awareness while you're out, and replay afterwards. It runs on a laptop in the field and is built for low bandwidth: offline map tiles, compressed radar data and efficient MQTT messaging. It shows live radar, satellite, hazards, surface observations, soundings and your team's vehicles on one map, and can replay a past day on a single synchronized clock.

Bring your own MQTT broker and your team shares vehicles, annotations, drawings and storm-motion cones. Your own station networks, map overlays, soundings and archive can be added in a settings file. The built-in weather layers (NEXRAD radar, GOES satellite, SPC/NWS hazards, IEM surface obs and radiosondes, HRRR soundings) cover the US.

STORM was built for NSSL field operations, and NSSL's setup ships as a profile: see [Setting up for NSSL](docs/nssl.md).

The full user guide is [`STORM_USER_GUIDE.md`](STORM_USER_GUIDE.md) (also *Help > STORM User Guide* in the app). Setup references are in [`docs/`](docs/).

## Modes

STORM asks for a mode each time it starts.

| Mode | Use it to | Needs |
|------|-----------|-------|
| **VIEWER** | See the team live, read-only, plus all weather layers | Nothing (a broker in `storm.toml` to see the team) |
| **MONITOR** | Watch the team from base or from inside a vehicle | A broker login in `storm.toml` |
| **VEHICLE** | Share your vehicle's observations and annotations | A broker login in `storm.toml` |
| **ARCHIVE** | Replay a past session | Nothing |

VIEWER is the easiest way to try STORM.

## Install

STORM runs on macOS, Windows and Linux. You need Python 3.8+ to run the setup script; STORM itself runs in a conda environment on Python 3.14 that the script creates.

**1. Clone the repo.** Do not download the ZIP: the in-app updater uses `git pull` and needs the `.git` folder.

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
```

**2. Run the setup script.** It finds (or offers to install) conda, builds the `storm` environment and puts a launch shortcut on your Desktop.

```bash
python setup.py
```

**3. Download the map tiles.** The offline base map is too large for git. Download the files from [Google Drive](https://drive.google.com/drive/folders/1q4DJ-mg94tpDWHLEkQ_50oQ3uauQ77it?usp=sharing) into `tiles/`:

```
tiles/storm.mbtiles          offline vector map (3.7 GB)
tiles/storm_nlcd.mbtiles     optional: NLCD land-cover overlay (1.0 GB)
tiles/satellite.mbtiles      optional: USGS satellite basemap (2.8 GB)
```

These cover the central US (116°W–82°W, 28°N–49°N). Without `storm.mbtiles`, STORM loads the base map over the internet instead. That is fine for trying it out, but the map is blank offline.

**Working somewhere else?** Build tiles for your own area instead of downloading these:

```bash
python scripts/make_base_mbtiles.py --area oklahoma
```

builds `tiles/storm.mbtiles` from OpenStreetMap for any [Geofabrik](https://download.geofabrik.de) extract (`germany`, `australia`, `us` with `--bbox W,S,E,N` to clip). It needs Java 21 or newer: `conda env create -f envs/tiles.yml` makes a small `tiles` environment with it, then run the script with `conda activate tiles`. `python scripts/make_satellite_mbtiles.py --bbox W,S,E,N` builds the satellite basemap from USGS imagery, which covers the US only. NLCD land cover is a US product and stays optional.

**4. Launch.** Use the desktop shortcut, or:

```bash
conda activate storm
python main.py
```

Pick **VIEWER** in the launch window to confirm everything works. 

## Quickstart: point it at your broker

STORM shares vehicles, annotations, drawings and cones through an MQTT broker your team runs (Mosquitto, EMQX, HiveMQ, AWS IoT Core…). 3.0 was tested end to end against HiveMQ; the others aren't tested yet (see [what has been tested](docs/broker.md#what-has-been-tested)). No broker yet? [`docs/broker.md`](docs/broker.md) sets one up: on your laptop to try STORM, on a small server, or as a hosted service. Tell STORM which one in `storm.toml`; passwords and API keys go in `.env`:

```bash
cp storm.example.toml storm.toml
cp .env.example .env
```

In `storm.toml`, set `host` under `[mqtt]`, and `tls`, `username` or the certificate files if your broker needs them. Put the password in `.env` as `STORM_MQTT_PASSWORD`. Everyone on the team uses the same broker and `topic_root`. Then:

1. Launch and pick **VIEWER**: the status pill should read **● MQTT OK**.
2. Pick **VEHICLE** on one laptop and **VIEWER** or **MONITOR** on another: the vehicle should appear on both, and an annotation placed on one should show up on the other.
3. `python scripts/check_broker.py` checks that your login can publish and that the broker handles retained messages and their expiry the way STORM needs (about a minute).

Next, as you need them:

| To | See |
|----|-----|
| Run your own broker, see who can see what, fix connection problems | [`docs/broker.md`](docs/broker.md) |
| Learn every `storm.toml` key (broker, map home and time zones, logger columns, archive) | [`docs/configuration.md`](docs/configuration.md) |
| Add your station networks, map overlays and soundings | [`docs/data-sources.md`](docs/data-sources.md), or [`docs/providers.md`](docs/providers.md) for feeds a table can't describe |
| Publish to STORM from your own logger or script | [`docs/wire-format.md`](docs/wire-format.md) |
| Record your days and replay them in ARCHIVE, with your own logger files | [`docs/archive-data.md`](docs/archive-data.md) |
| Have an AI coding agent set up your data with you | [`docs/agent-setup.md`](docs/agent-setup.md) |
| Use NSSL's setup | [`docs/nssl.md`](docs/nssl.md) |

## Setting up your team's laptops

Once one laptop works, every other vehicle gets the same setup. Install STORM on each (above), then copy over:

1. **`storm.toml`**: the team's shared settings (broker, topic root, data sources, units). Nothing in it is secret. Put it in the STORM folder, or anywhere `STORM_CONFIG` points.
2. **`.env`**: the passwords and keys (`STORM_MQTT_PASSWORD`, `ORS_API_KEY`, your sources' keys). Put it in the STORM folder. Hand it out privately, never in the repo or a public channel.
3. **Broker certificates**, only if your broker uses client certificates (`tls = "mutual"`, as AWS IoT Core does): into `certs/`, at the paths `storm.toml` names. A broker with a username and password needs none.
4. **Map tiles** in `tiles/`, for an offline map (the downloads above, or your own from `scripts/make_base_mbtiles.py`).
5. **Your code provider's folder**, only if your team wrote one: `providers/<name>/`, or wherever `[providers.<name>] path` points.

Vehicle ID, icon and logger folder are picked in each laptop's launch window, so the files stay the same everywhere. A vehicle with a different logger picks its layout there too, from the layouts `storm.toml` names. Check each laptop with **VIEWER** (the pill should read **● MQTT OK**).

A headless Pi takes its broker settings from the same `storm.toml`: pass it to `install.sh` with `--profile-file`; its certificates go in its own `certs/`, and its password in `storm-headless.toml` or `STORM_MQTT_PASSWORD` (Pis don't read `.env`). See [`headless/README.md`](headless/README.md).

## Outside the US

The broker, vehicles, annotations, drawings, cones, your own `[[sources]]` and ARCHIVE replays of your own recordings work anywhere. The built-in weather layers don't: NEXRAD radar, GOES satellite, SPC/NWS hazards, IEM surface obs and radiosondes, and HRRR soundings are US products, and the downloadable map tiles cover the central US. For a team elsewhere:

- **Map:** build tiles for your area with `scripts/make_base_mbtiles.py` (see *Working somewhere else?* above).
- **Units:** `[units]` `temperature = "C"`, `distance = "km"`. Wind stays in knots.
- **Map home and clocks:** `[map]` `home_lat`, `home_lon`, `zoom` and `time_zones`.
- **ARCHIVE's day:** `[archive]` `session_start_utc`, `session_hours` and `session_activity_after_hours`, so a session covers your field day instead of the Americas' afternoon and evening.
- **Your own weather data:** station networks, overlays and soundings from national services or your own instruments as `[[sources]]` ([`docs/data-sources.md`](docs/data-sources.md)).

Every key is in [`docs/configuration.md`](docs/configuration.md).

## API keys

Some data comes from authenticated services. Keys go in `.env` (a real environment variable of the same name takes precedence):

- `ORS_API_KEY`: openRouteService, for turn-by-turn routing. Create one for free at openrouteservice.org.
- Keys your own `[[sources]]` or `[[auth]]` tables name with `api_key_env`.
- `NSSL_API_KEY`: the NSSL API, used only with the NSSL profile ([docs/nssl.md](docs/nssl.md)).

## Data sources and their terms

STORM fetches from public services while it runs; each laptop makes its own requests. Most are free US government data with no terms beyond reasonable use. The ones with conditions:

| Service | Used for | Terms |
|---------|----------|-------|
| [open-meteo](https://open-meteo.com) | HRRR point soundings | Free API is for non-commercial use, with daily limits; commercial use needs a paid plan |
| [openrouteservice](https://openrouteservice.org) | Turn-by-turn routing | Free key with daily and per-minute quotas |
| [Nominatim](https://nominatim.org) (OpenStreetMap) | Address search in ROUTE and the command palette | Light use only (about one request a second) |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) / OpenMapTiles | The base map, downloaded or built | Free under the ODbL; the map must show the credit, which STORM does |
| [IEM](https://mesonet.agron.iastate.edu) (Iowa State) | Live satellite MESO frames, ASOS, radiosondes, product text, archive warnings, watches and MDs | Free; asks for reasonable use |
| [Geofabrik](https://download.geofabrik.de) | OpenStreetMap extracts for `make_base_mbtiles.py` | Free; one download per build |

NEXRAD, GOES, SPC, NWS, NCEI, nowCOAST and the USGS imagery and NLCD tiles are public-domain US government data. Your own `[[sources]]` follow their providers' terms. Guide §13 lists every source and how often STORM polls it.

## Updating

If you cloned the repo, the launch window shows an update button when a new version is available. By hand:

```bash
git pull
conda env update -f envs/storm.yml --prune
```

If `git pull` fails, you probably have local changes in the way; run `git status`.

## Files STORM keeps on disk

Besides the repository itself (and `tiles/`, if you downloaded or built map tiles), STORM writes:

| Where | What | Size and cleanup |
|-------|------|------------------|
| `recordings/` | Broker traffic the recorder writes for ARCHIVE | A few MB per vehicle per field day; keep the days you want to replay |
| `storm_tracks/` | ARCHIVE storm tracks, one CSV per case date | Small |
| `data/case_packages/`, `data/studio/` | Exported case packages and Video Studio projects | Case packages can be GBs with data packed in |
| `perf_logs/` | Performance logs, only with `--perf-log` | Small; safe to delete |
| `storm_errors.log`, `storm_fault.log` | The error log (rotated, at most 8 MB) and crash traces | Safe to delete |
| `~/.cache/storm/` | Archive calendar dates, the ASOS station list, NSSL's FOFS file index (a copy of its `manifest.json`), downloaded raw lidar files (`raw_lidar/`, at most 3 GB) and `make_base_mbtiles.py`'s downloads (`planetiler/`, about 1.5 GB plus your extracts) | Safe to delete; rebuilt or re-downloaded when needed |
| The OS app-data folder, `STORM/STORM/vehicle_history/` | Live vehicle observations, for timeseries after a restart | The last 2 days are kept |

All of these are git-ignored. Settings (launch choices, window layout) are in the OS's own settings store under `STORM`.

## Command-line options

Most people run `python main.py` with no flags. The options (open a case package, lite mode for slow hardware, performance logs, diagnostic profiles) are listed by `python main.py --help`.

## Headless publisher

A Raspberry Pi can publish a vehicle's data without a display, using the code in `headless/`. See [`headless/README.md`](headless/README.md).

## Repository layout

| Folder | Contents |
|--------|----------|
| `main.py`, `config.py`, `runtime_flags.py` | Entry point, constants, runtime flags |
| `settings/`, `storm.example.toml`, `profiles/` | Per-install settings (`storm.toml`) and ready-made profiles |
| `docs/` | Setup references: [`configuration.md`](docs/configuration.md), [`data-sources.md`](docs/data-sources.md), [`providers.md`](docs/providers.md), [`wire-format.md`](docs/wire-format.md), [`archive-data.md`](docs/archive-data.md), [`broker.md`](docs/broker.md), [`agent-setup.md`](docs/agent-setup.md), [`nssl.md`](docs/nssl.md) |
| `ui/` | The interface: map, toolbar drawers, dialogs, sounding viewer, Video Studio |
| `data/` | Fetchers and decoders for radar, satellite, hazards, surface obs, soundings and contours, and the `[[sources]]` reader |
| `archive/` | ARCHIVE mode: session, clock and per-layer archive fetchers |
| `providers/nssl/` | NSSL's code provider (FOFS, CLAMPS, lidar), its certificates and deployment locations; on with `[providers.nssl]`, through the same hooks as any team's (`docs/providers.md`). The regional mesonets and SFCOA are `[[sources]]` in `profiles/nssl.toml` |
| `core/` | Shared models: radar scans, annotations, storm tracks, case packages |
| `network/` | MQTT client and sync for vehicles, annotations, cones and drawings |
| `headless/` | The Raspberry Pi publisher |
| `envs/`, `scripts/`, `setup.py` | Conda environment, launchers and the setup script |
| `tests/` | Test suite |

The map is MapLibre GL inside a Qt web view, served in-process through a custom `storm://` URL scheme, with a `QWebChannel` bridge between Python and JavaScript. 

## Contributing

Bug reports and pull requests are welcome; [`CONTRIBUTING.md`](CONTRIBUTING.md) covers setup, tests and what a change should include. Report security problems privately, as [`SECURITY.md`](SECURITY.md) describes.

## Citing STORM

See [`CITATION.cff`](CITATION.cff), or use GitHub's "Cite this repository".

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
