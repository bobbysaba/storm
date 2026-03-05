# STORM
**Severe Thunderstorm Observation and Reconnaissance Monitor**

A standalone desktop application for storm chasing situational awareness. Runs on a laptop in the field and is designed for **low bandwidth environments** — offline map tiles, compressed radar data, and efficient MQTT messaging.

Built with Python + PyQt6. Not a web app — a native desktop application.

---

## Current Features

- **Offline vector map** — OpenStreetMap tiles served locally from MBTiles via a bundled Flask server; no internet required for the base map
- **NEXRAD radar overlay** — fetches Level 3 reflectivity and velocity from Unidata THREDDS (~50–300 KB per scan); reprojects polar data to lat/lon and renders as a transparent PNG overlay on the map
- **Radar playback** — automatically backfills the last 12 scans (~1 hour of history) on startup; full playback controls (⏮ ⏪ ▶/⏸ ⏩ ⏭) with a timeline slider
- **Storm motion cones** — click to place a 60-minute motion cone; configurable speed and heading; editable and deletable after placement; synced to all connected clients over MQTT
- **Road annotations** — place road closure, construction, flooding, downed lines, debris, and other markers on the map; editable after placement; synced over MQTT
- **Station plot markers** — MetPy-style station plot PNGs rendered at vehicle positions (temperature, dewpoint, pressure, wind barb)
- **Vehicle tracking** — polls NSSL vehicle location endpoint; publishes local obs to MQTT; remote vehicles appear as colored markers on the map
- **Obs file watcher** — monitors FOFS instrument logger files (Track A); parses each new record and feeds the obs pipeline
- **Obs history store** — 10-minute rolling buffer of surface obs per vehicle; feeds station plots and history charts
- **Dark theme** — full dark UI optimized for field use on a laptop screen
- **UTC clock** — live UTC time in the status bar
- **Coordinate display** — lat/lon and zoom level update on mouse move
- **Launch dialog** — prompts for vehicle ID, data directory, and monitor mode on every launch; persists settings across sessions
- **Monitor mode** — display remote vehicles and radar without publishing any local data

---

## Requirements

- Python 3.11 (tested with [Miniforge](https://github.com/conda-forge/miniforge))
- conda environment: `storm`
- macOS or Linux (Windows untested)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
```

### 2. Create the conda environment

**macOS:**
```bash
conda env create -f storm_mac.yml
conda activate storm
```

**Windows:**
```bash
conda env create -f storm_windows.yml
conda activate storm
```

### 3. Download the map tiles

The MBTiles file is too large for git and is hosted separately.

**[Download tiles/ folder (Google Drive)](https://drive.google.com/drive/folders/1q4DJ-mg94tpDWHLEkQ_50oQ3uauQ77it?usp=sharing)**

Download the entire `tiles/` folder and place it in the project root so the structure is:
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
.aws/storm.pem
.aws/storm.pem.crt
.aws/storm-private.pem.key
.aws/storm-public.pem.key
```
These are distributed out-of-band and are never committed to the repo.

### 6. macOS app bundle (optional)

To create a double-clickable `STORM.app`:
```bash
# First generate icons from a 1024×1024 PNG named storm.png:
python create_icon.py
# Then build the bundle:
./create_app.sh
```

---

## Project Structure

```
storm/
├── main.py                  # Entry point
├── config.py                # Constants — cert paths, MQTT settings, defaults
│
├── core/                    # Pure data types (no Qt, no I/O)
│   ├── annotation.py        # Annotation dataclass + type registry
│   ├── observation.py       # Meteorological obs record
│   ├── radar_scan.py        # RadarScan dataclass + product metadata
│   ├── storm_cone.py        # StormCone dataclass + GeoJSON builder
│   └── vehicle.py           # Vehicle dataclass
│
├── data/                    # Background I/O and decoding
│   ├── radar_fetcher.py     # Polls Unidata THREDDS; backfills 12 scans
│   ├── radar_decoder.py     # MetPy Level 3 decode → RadarScan
│   ├── obs_file_watcher.py  # Watches FOFS instrument logger file (Track A)
│   ├── gps_reader.py        # NMEA via pyserial — auto-detects GPS puck (Track B)
│   ├── obs_history_store.py # 10-min rolling obs buffer per vehicle
│   └── truck_replay.py      # Offline CSV replay for testing
│
├── network/
│   ├── mqtt_client.py       # Paho-MQTT wrapper (TLS, reconnect, signals)
│   ├── vehicle_sync.py      # Publishes local obs → storm/vehicles/{id}
│   ├── vehicle_fetcher.py   # Polls NSSL vehicle location endpoint
│   ├── annotation_sync.py   # Bidirectional annotation MQTT sync
│   └── storm_cone_sync.py   # Bidirectional storm cone MQTT sync
│
├── ui/                      # Qt widgets
│   ├── launch_dialog.py     # Pre-launch config dialog
│   ├── main_window.py       # Top-level QMainWindow
│   ├── map_widget.py        # MapLibre GL map + Flask tile server
│   ├── radar_controls.py    # Radar site/product/playback drawer
│   ├── radar_overlay.py     # RadarScan → PNG → MapLibre raster layer
│   ├── station_plot_layer.py # MetPy station plot PNG markers
│   ├── annotation_tools.py  # Annotation type selector drawer
│   ├── annotation_dialog.py # Place / edit annotation dialogs
│   ├── storm_cone_dialog.py # Storm motion cone input dialog
│   ├── history_widget.py    # Time series chart (obs history)
│   └── theme.py             # QSS dark theme + color constants
│
├── static/                  # Bundled offline assets (no CDN)
│   ├── maplibre-gl.js
│   ├── maplibre-gl.css
│   └── fonts/               # Noto Sans glyph PBFs (Latin ranges)
│
├── tiles/
│   └── storm.mbtiles        # NOT in git — download separately
│
└── .aws/                    # AWS IoT TLS credentials — NOT in git
    ├── storm.pem
    ├── storm.pem.crt
    └── storm-private.pem.key
```

---

## Architecture Notes

- **Tile server** — Flask runs on `http://localhost:8765` in a background daemon thread, serving the map HTML, MapLibre assets, fonts, and MBTiles vector tiles. MapLibre GL JS is bundled locally — no internet required.
- **Radar pipeline** — `RadarFetcher` polls Unidata THREDDS every 2 minutes for NEXRAD Level 3 files. On first fetch it backfills the last 12 scans. Data flows: `RadarFetcher` → `decode_nexrad_l3()` → `RadarScan` → `RadarOverlay` → base64 PNG → MapLibre raster source.
- **Map bridge** — `QWebChannel` connects Python and the MapLibre JS context. Mouse moves, clicks, and feature interactions emit Qt signals. Python calls JS functions (`stormAddVehicle`, `stormAddStormCone`, `stormAddAnnotation`, etc.) via `page().runJavaScript()`.
- **Data paths** — Track A: obs file watcher reads FOFS instrument logger CSV. Track B: GPS reader reads NMEA from serial port. Both feed the same `ObsHistoryStore` and publish via `VehicleSync`.
- **MQTT** — AWS IoT broker over TLS port 8883. Topic layout: `storm/vehicles/{id}`, `storm/annotations/{id}`, `storm/cones/{id}`.
- **Radar source** — NEXRAD Level 3 via Unidata THREDDS (public, no auth). N0Q (super-res reflectivity) with N0B fallback; N0U (velocity) with N0S fallback.

---

## Radar Site Coverage

The radar site selector covers the central and northern Great Plains:
Oklahoma, Kansas, Nebraska, South Dakota, North Dakota, Texas (panhandle and north), Colorado, Wyoming, Missouri, Iowa, and Arkansas.

The dropdown automatically sorts by distance from your configured home location and shows the 5 nearest sites. Any NEXRAD site can be entered manually via the **OTHER...** option.

---

## Roadmap

- [ ] PyInstaller packaging — standalone `.app` / `.exe` (no Python install required)

---

## Known Cosmetic Warnings

These appear in the console but are harmless:

| Warning | Cause |
|---|---|
| `font-variant-numeric` QSS warning | Qt ignores this CSS property |
| `SF Pro Display` font not found | Falls back to system font |
| Skia Graphite backend error | Qt/GPU initialization message |
