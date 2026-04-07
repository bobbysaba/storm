# STORM
**Severe Thunderstorm Observation and Reconnaissance Monitor**

A standalone desktop application for storm chasing situational awareness. Runs on a laptop in the field and is designed for **low bandwidth environments** — offline map tiles, compressed radar data, and efficient MQTT messaging.

---

## Current Features

- **Offline vector map** — OpenStreetMap tiles served entirely in-process via a custom `storm://` URL scheme; no internet required for the base map
- **NEXRAD radar overlay** — fetches Level 3 super‑res reflectivity (N0B fallback to N0Q/N0R) and velocity from Unidata THREDDS (~50–300 KB per scan); re-projects polar data to lat/lon and renders as a transparent PNG overlay on the map
- **Satellite overlay** — GOES‑East CONUS and MESO imagery with time‑step playback (backfills up to 10 recent frames on selection)
- **SPC/NWS hazards** — Day 1 outlook polygons, SPC watches/MDs, and NWS warnings with map tooltips + click‑through discussion text
- **Real-time annotations** — place road closure, construction, flooding, downed lines, debris, and storm motion cones on the map; editable after placement; synced over MQTT
- **Station plot markers** — MetPy-style station plot PNGs rendered at vehicle positions (temperature, dewpoint, pressure, wind barb); synced over MQTT
- **Surface obs (mesonet)** — live surface observation station models from OK Mesonet and West Texas Mesonet; toggled independently per network; polled every 5 minutes
- **Point soundings** — click any map location to fetch a live Skew-T log-P sounding; three sources: HRRR model (open-meteo, F0–F3 scrubber), observed radiosondes (IEM RAOB), and NSSL CLAMPS DL truck data; shows parcel parameters, kinematics table, and hodograph
- **Turn-by-turn routing** — on-map directions via OSRM with Nominatim geocoding; address, lat/lon, or map-click origin/destination; auto-re-routes when off-course
- **Multi-mode launch** — VEHICLE (full obs publish), MONITOR (view-only, no local publish), VIEWER (no MQTT, no obs), or ARCHIVE (replay a past session) — selected at the launch dialog with passphrase authentication
- **Archive mode** — replay any past session at a chosen UTC date/time; synchronized playback of NEXRAD radar, GOES satellite, SPC/NWS hazards, soundings, and MQTT vehicle positions; central time controller with play/pause, 1×–300× speed multipliers, ←/→ 30-second step buttons, and a timeline scrubber
- **Vehicle timeseries** — interactive time-series plots of meteorological observations (temperature, dewpoint, wind speed/direction, pressure) for any tracked vehicle; works in both live and archive modes; features scroll-wheel zoom, click-drag selection zoom with visual highlight, double-click to reset zoom, and inline cursor readouts below each subplot with 10-second grid snapping

---

## Requirements

- Python 3.11 via conda (Miniforge, Miniconda, or Anaconda)
- conda environment: `storm`
- macOS or Windows

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
```

> **Important:** You must clone the repo — do not download the ZIP from GitHub. STORM's in-app updater relies on `git pull` and will not work without a `.git` directory.

### 2. Run the setup script

The setup script creates the `storm` conda environment and places a launch shortcut on your Desktop — all in one step. Works on macOS, Linux, and Windows.

**Prerequisites:** Python 3.8+ and (optionally) conda. If you don't have conda, the script will offer to install Miniforge for you, or you can install it yourself from [Miniforge](https://github.com/conda-forge/miniforge#download).

```bash
python setup.py
```

> If you prefer to set up manually, see steps 2a–2b below.

<details>
<summary>Manual setup</summary>

**2a. Create the conda environment**

```bash
conda env create -f envs/storm.yml
conda activate storm
```

**2b. Create the Desktop shortcut (optional)**

macOS:
```bash
bash scripts/create_app.sh
```

Windows: double-click `scripts\create_app_windows.bat`

</details>

---

## Updating

> **Note:** In-app updates and `git pull` only work if you installed via `git clone`. If you downloaded a ZIP, re-install using the clone instructions above.

Pull the latest code and sync your conda environment using your normal git/conda workflow for this checkout. The repository does not currently include a dedicated update script.

Typical steps:
1. Run `git pull`
2. Run `conda env update --prune -f envs/storm.yml`
3. Rebuild the app bundle or refresh the shortcut only if you use those packaging flows

> If `git pull` fails, it usually means you have local uncommitted changes that conflict. Run `git status` to see what's changed, resolve any conflicts, and re-run the update steps.

---

### 3. Download the map tiles

The MBTiles file is too large for git and is hosted separately.

**[Download tiles/ folder (Google Drive)](https://drive.google.com/drive/folders/1q4DJ-mg94tpDWHLEkQ_50oQ3uauQ77it?usp=sharing)**

Download the file and place it in the `tiles/` folder so the structure is:
```
tiles/storm.mbtiles
```

### 4. Run

```bash
conda activate storm
python main.py
```

**Optional flags:**
```bash
python main.py --debug                          # enable debug logging
python main.py --monitor                        # monitor mode (no local publish)
python main.py --truck-replay-file path/to.txt  # replay a truck logger file locally
python main.py --render-grid-size 256           # lower radar resolution for slow hardware
```

### 5. Place your AWS IoT credentials

Obtain the four TLS cert files and place them at:
```
aws/storm.pem
aws/storm.pem.crt
aws/storm-private.pem.key
aws/storm-public.pem.key
```
These are distributed out-of-band and are never committed to the repo. Please contact [Bobby Saba](mailto:robert.saba@noaa.gov) for the files.

### 6. macOS app bundle (optional)

To create a double-clickable `STORM.app`:
```bash
bash scripts/create_app.sh
```

The app bundle records your project folder location at build time, so it can be moved or copied anywhere — the Dock, `/Applications`, a Desktop alias — and will always launch from the correct location. If you ever move the project folder itself, just re-run `bash scripts/create_app.sh` to update the path.

---

## Project Structure

```
storm/
├── main.py                  # Entry point
├── config.py                # Constants — cert paths, MQTT settings, defaults
├── storm.icns               # macOS app icon
├── storm.ico                # Windows app icon
├── setup.py                 # Cross-platform one-step setup (macOS/Linux/Windows)
├── roadmap.txt              # Implementation status and planned features
│
├── envs/                    # Conda environment specs
│   └── storm.yml            # Unified environment (all platforms)
│
├── scripts/                 # Build and utility scripts
│   ├── create_app.sh        # Builds STORM.app macOS bundle
│   ├── create_app_windows.bat  # Creates STORM desktop shortcut (Windows)
│   ├── launch_storm.bat     # Activates conda env and launches STORM (Windows)
│   └── test_mqtt_send.py    # CLI tool — sends test obs payloads to MQTT broker
│
├── archive/                 # Archive (replay) mode — session config, clock, fetchers
│   ├── session.py           # ArchiveSession dataclass — holds start time, radar station
│   ├── time_controller.py   # Central archive clock (play/pause, speed, scrubber, signals)
│   └── fetchers/            # Per-layer archive data fetchers (synchronized to clock)
│       ├── radar_archive_fetcher.py     # Fetches historical NEXRAD Level 3 frames
│       ├── satellite_archive_fetcher.py # Fetches historical GOES satellite frames
│       ├── hazard_archive_fetcher.py    # Fetches historical SPC/NWS hazard products
│       ├── sounding_archive_fetcher.py  # Fetches historical sounding data
│       └── mqtt_reader.py               # Replays historical MQTT vehicle positions
│
├── core/                    # Pure data types (no Qt, no I/O)
│   ├── annotation.py        # Annotation dataclass + type registry
│   ├── drawing.py           # Drawing (front/polyline/polygon) dataclass
│   ├── observation.py       # Meteorological obs record
│   ├── radar_scan.py        # RadarScan dataclass + product metadata
│   ├── sounding.py          # Sounding + SoundingSet dataclasses; pressure levels
│   ├── storm_cone.py        # StormCone dataclass + GeoJSON builder
│   └── vehicle.py           # Vehicle dataclass
│
├── data/                    # Background I/O and decoding
│   ├── radar_fetcher.py     # Polls Unidata THREDDS; backfills 12 scans
│   ├── radar_decoder.py     # MetPy Level 3 decode → RadarScan
│   ├── satellite_fetcher.py # WMS satellite imagery fetch + cache
│   ├── hazard_fetcher.py    # SPC/NWS hazard polygons
│   ├── sounding_fetcher.py  # On-demand HRRR point sounding via open-meteo
│   ├── obs_sounding_fetcher.py  # Observed radiosonde soundings via IEM RAOB
│   ├── clamps_sounding_fetcher.py  # NSSL CLAMPS DL truck soundings via THREDDS
│   ├── sounding_stations.py # Radiosonde station metadata (lat/lon lookup)
│   ├── surface_fetcher.py   # OK Mesonet / WTM surface obs (5 min poll)
│   ├── routing_fetcher.py   # OSRM turn-by-turn routing + Nominatim geocoding
│   ├── update_checker.py    # Git-based update check at launch
│   ├── obs_file_watcher.py  # Watches FOFS instrument logger file (Track A)
│   ├── gps_reader.py        # NMEA via pyserial — auto-detects GPS puck (Track B)
│   └── truck_replay.py      # Offline CSV replay for testing
│
├── network/
│   ├── mqtt_client.py       # Paho-MQTT wrapper (TLS, reconnect, signals)
│   ├── vehicle_sync.py      # Bidirectional vehicle obs sync via storm/vehicles/{id}
│   ├── annotation_sync.py   # Bidirectional annotation MQTT sync
│   └── storm_cone_sync.py   # Bidirectional storm cone MQTT sync
│
├── ui/                      # Qt widgets
│   ├── launch_dialog.py     # Pre-launch config dialog (VEHICLE / MONITOR / VIEWER / ARCHIVE modes)
│   ├── main_window.py       # Top-level QMainWindow
│   ├── map_widget.py        # MapLibre GL map + custom storm:// asset/tile scheme
│   ├── tile_scheme_handler.py  # QWebEngineUrlSchemeHandler for storm:// (tiles + assets)
│   ├── radar_controls.py    # Radar site/product/playback drawer
│   ├── radar_overlay.py     # RadarScan → PNG → MapLibre raster layer
│   ├── satellite_controls.py # Satellite mode/playback drawer
│   ├── hazard_controls.py   # SPC/NWS hazard toggle drawer
│   ├── sounding_controls.py # Sounding source selector (HRRR / OBS / NSSL) drawer
│   ├── surface_controls.py  # Mesonet surface obs toggle drawer (OK / WTM)
│   ├── surface_plot_layer.py # MetPy station model circles for surface obs
│   ├── routing_controls.py  # Turn-by-turn routing input / directions drawer
│   ├── deploy_locs_controls.py # Deployment location filter drawer (RANK/RQI slider)
│   ├── layer_order_pill.py  # Floating pill for reordering map layer draw order
│   ├── debug_pill.py        # Debug information display pill
│   ├── outlook_panel.py     # Right-side sliding panel for SPC/NWS discussion text
│   ├── station_plot_layer.py # MetPy station plot PNG markers at vehicle positions
│   ├── annotation_tools.py  # Annotation type selector drawer
│   ├── annotation_dialog.py # Place / edit annotation dialogs
│   ├── drawing_dialog.py    # Polyline/polygon drawing dialogs
│   ├── storm_cone_dialog.py # Storm motion cone input dialog
│   ├── sounding_dialog.py   # Skew-T log-P dialog (HRRR / OBS / NSSL sources)
│   ├── vehicle_timeseries_dialog.py  # Vehicle observation timeseries plots (temp/dewpoint, wind, pressure)
│   ├── archive_controls.py  # Archive playback controls bar (scrubber, speed, status indicators)
│   ├── archive_loading_dialog.py  # Progress dialog shown while archive data is prefetched
│   ├── nav_pill.py          # Compact navigation summary pill widget
│   └── theme.py             # QSS dark theme + color constants
│
├── static/                  # Bundled offline assets (no CDN)
│   ├── maplibre-gl.js
│   ├── maplibre-gl.css
│   ├── indicator_on.svg     # MQTT connection status indicators
│   ├── indicator_off.svg
│   └── fonts/               # Noto Sans glyph PBFs (Latin ranges)
│
├── tiles/
│   └── storm.mbtiles        # NOT in git — download separately
│
└── aws/                    # AWS IoT TLS credentials — NOT in git
    ├── storm.pem
    ├── storm.pem.crt
    └── storm-private.pem.key
```

---

## Architecture Notes

- **Tile/asset serving** — `StormSchemeHandler` (`ui/tile_scheme_handler.py`) registers a custom `storm://` URL scheme that serves the map HTML, MapLibre assets, fonts, and MBTiles vector tiles entirely in-process — no Flask server, no TCP port required.
- **Radar pipeline** — `RadarFetcher` polls Unidata THREDDS every 2 minutes for NEXRAD Level 3 files. On first fetch it backfills the last 6 scans per product (12 total — reflectivity and velocity). Data flows: `RadarFetcher` → `decode_nexrad_l3()` → `RadarScan` → `RadarOverlay` → base64 PNG → MapLibre raster source.
- **Map bridge** — `QWebChannel` connects Python and the MapLibre JS context. Mouse moves, clicks, and feature interactions emit Qt signals. Python calls JS functions (`stormAddVehicle`, `stormAddStormCone`, `stormAddAnnotation`, etc.) via `page().runJavaScript()`.
- **Data paths** — Track A: obs file watcher reads FOFS instrument logger CSV. Track B: GPS reader reads NMEA from serial port. Both update the live vehicle state and publish via `VehicleSync`.
- **MQTT** — AWS IoT broker over TLS port 8883. Topic layout: `storm/vehicles/{id}`, `storm/annotations/{id}`, `storm/cones/{id}`, `storm/drawings/{id}`.
- **Vehicle locations** — Live vehicle positions come from MQTT subscriptions on `storm/vehicles/{id}`. Local obs sources publish to that topic, and all connected clients subscribe to the same stream for fleet positions.
- **Radar source** — NEXRAD Level 3 via Unidata THREDDS (public, no auth). N0B (super-res reflectivity) with N0Q/N0R fallbacks; N0U (velocity) with N0S fallback.
- **Surface obs** — `SurfaceFetcher` polls OK Mesonet and West Texas Mesonet (WTM) every 5 minutes. Station model PNGs are rendered via MetPy/matplotlib and displayed as map markers.
- **Soundings** — Three independent sources: HRRR point soundings via open-meteo API (`SoundingFetcher`), observed radiosondes via IEM RAOB (`ObsSoundingFetcher`), and NSSL CLAMPS DL truck soundings via NSSL THREDDS (`ClampsSoundingFetcher`).
- **Routing** — `RoutingFetcher` geocodes addresses with Nominatim and fetches turn-by-turn directions from the public OSRM demo server. Auto-re-routing triggers when the vehicle drifts >100 m off-route for 3+ consecutive GPS fixes.

---

## Radar Site Coverage

The radar site selector covers the central and northern Great Plains:
Oklahoma, Kansas, Nebraska, South Dakota, North Dakota, Texas (panhandle and north), Colorado, Wyoming, Missouri, Iowa, and Arkansas.

The dropdown automatically sorts by distance from your configured home location and shows the 5 nearest sites. Any NEXRAD site can be entered manually via the **OTHER...** option.

---
