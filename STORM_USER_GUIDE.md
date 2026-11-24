# STORM User Guide
**Severe Thunderstorm Observation and Reconnaissance Monitor**

---

## Table of Contents

1. [Overview](#1-overview)
2. [Installation & Requirements](#2-installation--requirements)
3. [Launching STORM](#3-launching-storm)
4. [Main Interface Layout](#4-main-interface-layout)
5. [Map Navigation](#5-map-navigation)
6. [Floating Toolbar](#6-floating-toolbar)
   - 6.1 [Radar](#61-radar)
   - 6.2 [Hazards](#62-hazards)
   - 6.3 [Satellite](#63-satellite)
   - 6.4 [Soundings](#64-soundings)
   - 6.5 [Surface Obs](#65-surface-obs)
   - 6.6 [SFCOA Mesoanalysis](#66-sfcoa-mesoanalysis)
   - 6.7 [Map Tools & Base Layers](#67-map-tools--base-layers)
   - 6.8 [Routing](#68-routing)
   - 6.9 [Annotations](#69-annotations)
   - 6.10 [Drawings](#610-drawings)
   - 6.11 [Storm Motion Cone](#611-storm-motion-cone)
   - 6.12 [Measurement Tool](#612-measurement-tool)
   - 6.13 [Station Plots](#613-station-plots)
   - 6.14 [Deployment Locations](#614-deployment-locations)
   - 6.15 [Vehicle Panel](#615-vehicle-panel)
   - 6.16 [Vehicle Timeseries](#616-vehicle-timeseries)
7. [Status Bar](#7-status-bar)
8. [Archive Mode](#8-archive-mode)
9. [Outlook Text Panel](#9-outlook-text-panel)
10. [Point Soundings](#10-point-soundings)
11. [Vehicle Tracking & Observations](#11-vehicle-tracking--observations)
12. [Network & MQTT Sync](#12-network--mqtt-sync)
13. [Data Sources & Polling Intervals](#13-data-sources--polling-intervals)
14. [Command-Line Options](#14-command-line-options)
15. [Configuration & Certificates](#15-configuration--certificates)
16. [Keyboard Shortcuts](#16-keyboard-shortcuts)
17. [Performance Tuning](#17-performance-tuning)
18. [Diagnostics & Error Handling](#18-diagnostics--error-handling)
19. [Known Limitations & Quirks](#19-known-limitations--quirks)
20. [Feature Availability Matrix](#20-feature-availability-matrix)

---

## 1. Overview

STORM is a standalone desktop application for situational awareness in severe-weather field work, for any team with an MQTT broker. It integrates offline vector maps, real-time NEXRAD radar, GOES satellite imagery, SPC/NWS hazard products, collaborative field annotations, meteorological drawings, and live multi-vehicle tracking — all in a single interface designed to function reliably in the field. The built-in weather layers (NEXRAD, GOES, SPC/NWS, IEM, HRRR) cover the US; your team's own data sources can be added in `storm.toml`. NSSL's setup is a profile on top: see [`docs/nssl.md`](docs/nssl.md).

**Core Technology Stack:**

| Component | Technology |
|-----------|-----------|
| UI Framework | PyQt6 (QMainWindow + QWebEngineView) |
| Map Renderer | MapLibre GL JS (embedded in browser engine) |
| Vector Tiles | Offline MBTiles served via custom `storm://` URL scheme |
| Optional Base Layers | Offline NLCD and USGS satellite MBTiles |
| Radar Decoding | MetPy + matplotlib (Level 3, Unidata THREDDS) |
| Satellite Imagery | IEM WMS (GOES-East, GOES-West) |
| SFCOA Mesoanalysis | NSSL SFCOA vector tiles (a `contours` source in the NSSL profile) |
| Hazard Data | SPC GeoJSON MapServer + NWS API |
| Vehicle/Annotation Sync | MQTT v5 over your team's broker (`storm.toml`) |
| Python-to-JS Bridge | QWebChannel (`bridge` object) |

**Run Command:**
```bash
conda activate storm
python main.py
```

**Platform-Specific Launchers:**
- **macOS:** Double-click `STORM.app` (created via `bash scripts/create_app.sh`)
- **Linux:** Use desktop launcher (created via `bash scripts/create_desktop_entry.sh`)
- **Windows:** Double-click desktop shortcut (created via setup script)

---

## 2. Installation & Requirements

### Prerequisites
- Python 3.14 via conda (Miniforge, Miniconda, or Anaconda)
- conda environment: `storm` (created via `python setup.py` or `conda env create -f envs/storm.yml`)
- The offline MBTiles vector tile database (`storm.mbtiles`, about 3.7 GB, distributed separately). The shipped tiles cover the central US only (116°W–82°W, 28°N–49°N); `scripts/make_base_mbtiles.py` builds one for your own area (see the README)
- Optional offline base-layer MBTiles files: `storm_nlcd.mbtiles` for NLCD land cover and `satellite.mbtiles` for USGS imagery

**Quick Setup:**
```bash
git clone https://github.com/bobbysaba/storm.git
cd storm
python setup.py
```

The setup script creates the conda environment and desktop launcher automatically. See the README for detailed installation instructions.

### Broker Settings (`storm.toml`)
Vehicles, annotations, drawings, cones and scan sectors are shared through an MQTT broker your team runs. Tell STORM which one in `storm.toml` in the STORM folder:

```bash
cp storm.example.toml storm.toml      # then set [mqtt] host and the login
cp profiles/nssl.toml storm.toml      # or: NSSL's setup
```

The README's quickstart walks through checking the connection. NSSL's setup (profile, certificates, API key) is in [`docs/nssl.md`](docs/nssl.md).

Passwords go in `.env` (copy `.env.example`), not in `storm.toml`. [Section 15](#15-configuration--certificates) summarizes the settings; [`docs/configuration.md`](docs/configuration.md) describes every key.

Without a broker, STORM starts in a degraded state: map, radar, satellite, and SPC/NWS hazard features work, VIEWER and ARCHIVE launch, but nothing is shared live, and VEHICLE and MONITOR won't launch.

### Data Providers
Everything STORM reads from public sources (radar, satellite, SPC/NWS hazards, IEM ASOS and radiosondes, HRRR soundings, damage surveys) works for everyone. NSSL's own data is a *provider* that ships with STORM and is on for everyone unless `storm.toml` says `[providers.nssl] enabled = false` (the NSSL profile adds `team = true`):

- **NSSL THREDDS** (public, no key): ARCHIVE's **NSSL** button, for every team: NSSL's recorded vehicles and scan sectors, the FOFS mobile mesonet's one-second obs, the CLAMPS trailers, the lidar truck (radiosondes, winds, Doppler lidar scans and stares) and CLAMPS TROPoe ([NSSL's archive data](#nssls-archive-data-the-nssl-button)). NSSL's past deployment locations are a profile setting.
- **NSSL API** (also needs `NSSL_API_KEY` in `.env`, which the profile's `[[auth]]` table sends to the API only): live CLAMPS soundings, SFCOA, the regional mesonets, and the day's shared items at launch (`current.json`, the profile's `[mqtt] shared_items_url`). SFCOA and the mesonets are `[[sources]]` in the profile, not part of the provider.

Without the key, live SOUNDINGS has no NSSL source. Without the profile's SFCOA source there's no SFCOA button. PREV LOCS is a setting any team can use (`[map] deployment_locations`). NSSL's provider plugs in the way any team's code provider does (below): its ARCHIVE data is its own drawer, so a team with its own soundings can still fill the shared SOUNDING button. A team's own lidars and wind profiles sit beside NSSL's: LIDAR (next to RADAR) and NSSL > LIDAR open the same lidar drawer with every lidar on the map, and VAD and WINDS list every wind source. [Section 20](#20-feature-availability-matrix) lists what needs what; [`docs/nssl.md`](docs/nssl.md) is the NSSL setup checklist.

### Your Own Data Sources
Station networks, map overlays and soundings your team has can be added in `storm.toml` without changing STORM: one `[[sources]]` table per feed says where it is, how it's laid out and what units it's in. Station networks appear as buttons in SURFACE ([6.5](#65-surface-obs)), GeoJSON overlays as buttons in MAP ([6.7](#67-map-tools--base-layers)) (either can go on the toolbar instead), and soundings as buttons in SOUNDINGS ([6.4](#64-soundings)). [`docs/data-sources.md`](docs/data-sources.md) has every key and examples. The NSSL profile's regional mesonets (OK, WTM, KS, CO, NE, SD) are sources like these; they need `NSSL_API_KEY`.

A feed a table can't describe (a login handshake, paging, a binary format) can come from a **code provider**: a Python package of your team's own in `providers/<name>/` (git-ignored) or a folder you name, turned on with `[providers.<name>] enabled = true`. It hands STORM the same three kinds, can add ARCHIVE sources for recorded traffic and vehicle obs, and can feed STORM's own windows (a sounding button of its own, archive VAD, LIDAR, SFCOA); see [`docs/providers.md`](docs/providers.md). An AI coding agent can set up either with you from [`docs/agent-setup.md`](docs/agent-setup.md).

### Single-Instance Guard
Only one instance of STORM can run per machine (an internal port is used as a lock). If a second launch is attempted, a warning dialog appears and the second instance exits immediately.

---

## 3. Launching STORM

### Launch Dialog

Every time STORM starts, a configuration dialog appears before the main window opens.

#### Launch Mode

Choose one of four mutually exclusive roles for this session:

| Mode | Description |
|------|-------------|
| **VEHICLE** | Full participant — publishes local obs (file watcher / GPS) and syncs annotations, drawings, and cones over MQTT. Requires a broker login that can publish. |
| **MONITOR** | View-only participant — all map overlays and MQTT inbound work, but no local obs are published. Requires a broker login that can publish (annotations, drawings and cones are still shared). |
| **VIEWER** | Sees the team's vehicles, annotations, drawings, cones and scan sectors live, but can't publish. Uses the read-only `[mqtt.viewer]` login when one is set. Map, radar, satellite, and SPC/NWS hazards work as in the other modes. Needs no broker to launch. |
| **ARCHIVE** | Replay a past session — enter a UTC date/time and STORM fetches and plays back historical radar, satellite, hazards, soundings, and MQTT vehicle positions synchronized to a central time controller. Never connects to the broker. See [Section 8](#8-archive-mode). |

#### Archive Config (ARCHIVE mode only)

When ARCHIVE is selected, the vehicle-specific fields (ID, icon, data directory) are hidden and replaced with:

**Archive Start Time (UTC)**
A date/time field for the UTC start time of the replay session. Type the date and time, or open a calendar from the field's arrow or the calendar button beside it. All data products (radar, satellite, hazards, soundings, vehicle positions) replay from this time. Picking a day in a calendar keeps the time of day.

While ARCHIVE is selected, STORM looks for dates with data in the background (the ring beside the calendar button shows how many catalogs it has checked) and **shades those days** in both calendars. Click the calendar's year to jump to a year from a grid; years with data are outlined. The dates found are kept for a day (an hour for this year's), so the next launch shades the calendar at once. A day without shading isn't proof there's no data: a catalog that couldn't be reached stays unknown. The dates come from the archive sources in `storm.toml` ([Your Own Archive Data](#your-own-archive-data)): recorded broker traffic and vehicle-obs files in your folders (listed fresh each time), a web source's `dates.json`, and, with the NSSL provider, the FOFS mobile mesonet vehicles on NSSL's THREDDS server.

Under the field:

- **Browse available cases…** opens a window to find dates by campaign, years, months and vehicles. Campaigns are the `[[archive.campaigns]]` tables in `storm.toml` (the NSSL profile lists VORTEX2, RiVorS, TORUS, PERiLS and LIFT); without any, the campaign menu is off. The vehicle list is grouped by where the dates come from: RECORDED TRAFFIC, VEHICLES (your own vehicle-obs files) and, with the NSSL provider, MOBILE MESONET. Check vehicles to see the dates when *all of* them (or *any of* them) have data. A year-by-month grid counts the matching dates (click a month to list only its dates), and the list shows each date, newest first, with the vehicles that have data that day. Double-click a date, or select it and press *Use this date*, to set the archive date; the time is kept. The window fills in while the background search runs.
- **Open case…** chooses a case package (see [Menu Bar and Case Packages](#menu-bar-and-case-packages)). The date and time move to the case, a line under the links names it (station, time frame, tracks, packed files), and *Launch* opens it. Choosing another date, or *Clear*, drops it.

**Broker Login**
VEHICLE and MONITOR launch only when `storm.toml` names a broker with a login that may publish, and every certificate file that login needs is on disk; LAUNCH lists what's missing. VIEWER and ARCHIVE launch without one. In the NSSL profile, the team certificates go in `providers/nssl/certs/team/` and VIEWER uses a read-only certificate shipped with STORM ([`docs/nssl.md`](docs/nssl.md)). Only admin mode asks for a passphrase, and only when its box is checked.

The archive start time is not saved between sessions — each archive launch requires an explicit selection, except after *File > Change Day…*, which reopens the launch window on the last session's date.

---

#### Vehicle Config (VEHICLE mode only)

**Vehicle ID**
Your vehicle's identifier on the network (e.g., "Chase1", "Mesonet-TX"). Pre-filled from the previous session; edit freely. The ID is published with every observation, annotation, and drawing you send to other vehicles.

**Vehicle Icon**
Choose the icon that represents your vehicle on the map. Options: car, drone, mesonet, lidar, radar, hailcam.

**Data Directory (Track A)**
Optional folder path for a local instrument logger. If left blank, file-watcher input is disabled for the session. Use **Browse** to pick a folder. The app finds today's file there and polls it every second. Which file and which columns come from `[obs_columns]` in `storm.toml` (a `*YYYYMMDD.csv` with an ISO 8601 `time` column by default; see [Section 15](#15-configuration--certificates)).

**Logger File**
Shown when `storm.toml` names more than one logger layout (`[obs_columns.<name>]`): pick the one this vehicle's logger writes. The NSSL profile has *FOFS mesonet logger* and *GPS Ka (position only)*.

#### Data on Launch (collapsible)

A collapsible section lets you pre-select which data layers to enable automatically on startup:

- **SPC / NWS / RADAR** — multi-select toggle buttons to auto-enable those overlays
- **Satellite** — exclusive selector: OFF | CONUS | AUTO-MESO
- **Surface obs** — multi-select: the station networks in `storm.toml` ([Your Own Data Sources](#your-own-data-sources)); with the NSSL profile, OK | WTM | KS | CO | NE | SD. The row is hidden when there are none

#### Update Status

During startup STORM checks for available updates. The dialog shows:
- "Checking for updates…" while polling
- Current and latest version numbers if an update is available
- An "Update Available" button (STORM never auto-updates)

**Launch**
Click **LAUNCH** to proceed. Vehicle ID, icon, data directory, and mode are saved to persistent settings and pre-filled next time.

---

## 4. Main Interface Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  [Floating Toolbar: RADAR | HAZARDS | SATELLITE | ...]               │
│                                                                      │
│                                                                      │
│                        MAP (MapLibre GL)                             │
│                                                                      │
│                                                            ┌────────┐│
│                                                            │VEHICLE ││
│                                                            │PANEL   ││
│                                                            │(dock)  ││
│                                                            └────────┘│
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  35.2413 | -97.4521   Z 6.2  │  Ready  │           18:45:32 UTC  ●  │
└──────────────────────────────────────────────────────────────────────┘
```

| Region | Description |
|--------|-------------|
| **Floating Toolbar** | Top-left collapsible drawer strip — all overlay and tool controls |
| **Map** | Full-window interactive map; all overlays render here |
| **Vehicle Panel** | Right-side dock — vehicle list and selected-vehicle details |
| **Outlook Panel** | Right-side slide-out — SPC/NWS text discussion viewer |
| **Status Bar** | Bottom strip — mouse coordinates, zoom, status messages, clock, network indicator |

---

## 5. Map Navigation

### Mouse Controls

| Action | Result |
|--------|--------|
| **Scroll wheel** | Zoom in / zoom out |
| **Click + drag** | Pan the map |
| **Click a feature** | Open info popup (SPC outlook, watch, warning, annotation, etc.) |
| **Right-click** | Open the map menu (see [Right-Click Menu](#right-click-menu)) |
| **Double-click** | Finish a drawing in progress |
| **Hover over feature** | Show tooltip |
| **Hover over road** | Show road name/reference and surface class when available |

### Right-Click Menu

Right-clicking the map opens a menu at the cursor. On bare map it offers:

- **Sounding here ▸** — HRRR, nearest RAOB, or (NSSL provider) CLAMPS when a CLAMPS sounding has been fetched within 150 km
- **Route to here / Route from here** — opens ROUTE with the point filled in (in VEHICLE, *Route to here* routes from your GPS fix)
- **Measure from here** — turns on MEASURE with the first point placed
- **Place annotation ▸** and **Draw ▸** — place one annotation, or start a front, polyline or polygon at the point
- **Nearest radar site ▸** — the closest three sites; picking one switches the radar
- **Copy lat/lon** and **Add private pin**

On a feature, its own items come first: **Open text** for a warning, watch or MD; **Edit / Move / Delete** for an annotation, drawing or cone (Delete asks first, since it is published to the team); **Follow / Center / Timeseries** for a vehicle; **Sounding from nearest RAOB** for a surface station. Items hidden in the current mode are left out: VIEWER has no routing, and ARCHIVE treats map objects as read-only. While a map tool (drawing, measuring, sounding) is active, right-click does nothing.

### Command Palette

**Ctrl+K** (**⌘K** on macOS) opens a search box over the map. Type to filter, **↑ / ↓** to move, **Enter** to run, **Esc** to close. It finds:

- every action and drawer (with its key shown, if it has one), and every menu bar entry;
- radar sites (nearest first when this truck is on the map) and products;
- satellite sectors, SPC days and products, and hazard toggles;
- vehicles (center or follow) and the five ranked deployment locations nearest you;
- places (3 or more characters, when online), through the same geocoder as ROUTE.

The last ten commands run are listed first. Only commands available in the current mode are shown, and typing in the box never triggers map shortcuts.

### Map Layers (Render Order, bottom to top)

1. Offline vector base map (terrain, streets, water bodies, labels)
2. Optional USGS satellite basemap
3. Optional NLCD land-cover raster
4. SPC categorical outlook polygons (MRGL / SLGHT / ENH / MDT / HIGH)
5. SPC probabilistic outlook polygons (tornado / wind / hail probability shading + hatching)
6. NWS watches (county-polygon outlines)
7. SPC Mesoscale Discussions (polygon outline + numeric label)
8. NWS warnings (storm-polygon fills, phenom-specific colors)
9. GOES satellite imagery (optional, opacity-controlled)
10. NEXRAD radar overlay (semi-transparent PNG raster)
11. SFCOA mesoanalysis contours
12. Surface obs station models (your station networks, ASOS); `[[sources]]` overlays sit with the hazard layers, under the road labels
13. Station plot icons (MetPy-style, at vehicle positions)
14. Vehicle markers (colored dots + info popups)
15. Annotations (road closure / construction / flooding / downed-lines / debris markers)
16. Drawings (fronts, polylines, polygons)
17. Storm motion cones
18. Routing line (active route geometry)
19. Measurement tool geometry
20. Deployment location markers

---

## 6. Floating Toolbar

The floating toolbar lives in the top-left corner of the map window. Each button either toggles a layer directly or expands a collapsible drawer with additional controls. Drawers animate open/closed smoothly.

---

### 6.1 Radar

**Button:** `RADAR` (checkable toggle)

Clicking the button expands the radar drawer. Checking the checkbox inside enables radar fetching and display.

#### Drawer Controls

**Site Selector (dropdown)**
Choose from every WSR-88D site in the US and its territories (e.g., KTLX, KVNX, KDYX, KFWS) or type a custom 4-letter site code. On first launch the site is the one nearest your `[map]` home (in VEHICLE mode, the one nearest your GPS once it has a fix). Changing the site immediately clears cached frames and begins fetching for the new site.

**Product Selector (dropdown)**

| Option | NEXRAD Product | Description |
|--------|---------------|-------------|
| REFLECTIVITY (SR) | N0B (fallback: N0Q → N0R) | Base reflectivity (dBZ) |
| VELOCITY | N0U | Base velocity (kt) |

**Show Data Checkbox**
Enables or disables radar fetching. When unchecked, radar is hidden from the map but site/product selections are preserved.

**VAD Button**
Opens the VAD wind-profile window (hodograph and time-height) for the current radar site's recent NEXRAD VAD profiles. In ARCHIVE it shows the session's Doppler-lidar wind profiles instead (see *NSSL's archive data* in Archive Mode).

#### Playback Controls

| Button | Action |
|--------|--------|
| ⏮ | Jump to oldest cached frame |
| ⏪ | Step back one frame |
| ▶ / ⏸ | Start / pause auto-playback loop |
| ⏩ | Step forward one frame |
| ⏭ | Jump to most recent (live) frame |
| Timeline slider | Scrub to any cached frame by dragging |
| Time label | Shows current frame's UTC time (HH:MMZ) |
| Speed dropdown | Playback speed: 0.5× (1000 ms), 1× (500 ms), 2× (250 ms), 3× (167 ms) |
| SYNC | Satellite follows the radar loop (on by default) |
| VEH | Vehicle markers follow the loop too (needs SYNC) |

#### Synced Loop

While the radar shows a past frame (playing the loop, or stepped back from the live frame), the other layers follow its time:

- **Satellite** (SYNC on) shows the newest frame at or before the radar frame's time. The satellite drawer's own playback controls (slider, step, play and jump buttons) are greyed out while it follows the radar. If no satellite frame is close enough (10 min for CONUS, 3 min for MESO), the satellite dims and its time reads `no frame`. For CONUS, the loop waits up to 1.5 s for the new time's tiles before stepping.
- **Vehicles** (VEH on) move to where they were at that time, drawn faded with a small clock badge. A vehicle with no fix yet at that time is hidden. Follow mode and station plots pause while vehicles show past positions. STORM keeps the last 90 minutes of positions in memory, starting from launch.
- A **◷ LOOP HH:MMZ** chip appears next to the clock in the status bar. Click it to return to the live frame.

Stopping the loop, or stepping to the latest frame, returns everything to live. With radar data off, the satellite loop drives instead and vehicles (VEH on) follow it. SYNC and VEH are saved with the session. ARCHIVE mode is not affected: it already syncs every layer to the archive clock.

#### How Radar Data Works

- **Source:** Unidata THREDDS Level 3 catalog (public, no authentication required)
- **Polling:** Every 2 minutes
- **Frame cache:** Up to 12 frames per product
- **First fetch:** Backfills 6 reflectivity frames and 6 velocity frames
- **Render pipeline:** Level 3 polar data decoded by MetPy → reprojected to lat/lon grid → rendered as RGBA PNG via matplotlib → base64-encoded → pushed to MapLibre as a raster layer
- **Grid size:** Dynamic by default, starting at 768×768 (configurable via `--render-grid-size`)
- **Render time:** ~140–260 ms on modern hardware at 768×768

#### Disabling Radar

Pass `--disable-radar` at launch to hide the RADAR button entirely.

---

### 6.2 Hazards

**Button:** `HAZARDS` (checkable toggle)

Clicking the button expands the hazards drawer. The drawer contains two groups of controls.

#### SPC Products (mutually exclusive — only one active at a time)

| Button | Layer |
|--------|-------|
| **OUTLOOK** | Day 1 categorical outlook (MRGL, SLGHT, ENH, MDT, HIGH) |
| **TOR** | Tornado probability (2% / 5% / 10% / 15% / 30% / 45% / 60%) |
| **WIND** | Wind probability (5% / 15% / 30% / 45% / 60%) |
| **HAIL** | Hail probability (5% / 15% / 30% / 45% / 60%) |

Selecting one clears the others. Clicking the already-active button deactivates it (all SPC probability layers hidden).

#### Additive Overlays (independent, stack on top of each other)

| Button | Layer |
|--------|-------|
| **WATCHES** | SPC Tornado and Severe Thunderstorm Watch county polygons |
| **MDs** | SPC Mesoscale Discussion polygons with number labels |
| **NWS WARNINGS** | Active NWS warnings (all VTEC phenomenons) |
| **DAMAGE PATHS** | ARCHIVE only: NWS damage-survey tornado paths inside a box you draw (see *ASOS Replay and Damage Paths* in Archive Mode) |

These can be enabled in any combination, independent of the SPC product selection.

#### Legend

When any hazard product is active, a compact color legend row appears below the drawer controls showing color swatches and probability labels. It animates in and out with the drawer.

#### Clicking Hazard Features

**SPC Categorical / Tornado / Wind / Hail polygons:**
- Click a polygon → popup with risk level and probability label
- Click "Read Discussion" in the popup → fetches full text from IEM/SPC and displays it in the [Outlook Panel](#9-outlook-text-panel)

**Watches:**
- Click a county-polygon → popup with watch number, type (Tornado or Severe Thunderstorm), and issuance details
- Click "Read" → fetches full SEL bulletin text and displays it in the Outlook Panel

**Mesoscale Discussions:**
- Click an MD polygon → popup with MD number
- Click "Read" → fetches full MD text from SPC and displays it in the Outlook Panel

**NWS Warnings:**
- Click a warning polygon → popup with product type, event number, and NWS headline
- Full description and instructions fetched from the NWS API and shown in the popup

#### Significant Area Hatching

SPC tornado, wind, and hail probability layers include significant (CIG/SIGN) areas as separate overlapping features rendered with gray fill plus hatch patterns:

| Pattern | Meaning |
|---------|---------|
| Dashed diagonal lines | Conditional Intensity Group 1 (CIG1) |
| Solid diagonal lines | CIG2 or SIGN (significant tornado area) |
| Checkerboard | CIG3 |

#### Color Reference

**SPC Categorical:**

| Category | Color |
|----------|-------|
| MRGL (Marginal) | Green |
| SLGHT (Slight) | Yellow |
| ENH (Enhanced) | Orange |
| MDT (Moderate) | Red |
| HIGH | Purple |

**Watches:**

| Watch Type | Color |
|------------|-------|
| Tornado Watch | Red (#FF0000) |
| Severe Thunderstorm Watch | Royal Blue (#4169E1) |

**NWS Warnings by VTEC Phenom:**

| Phenom | Event | Color |
|--------|-------|-------|
| TO | Tornado Warning | Red (#FF0000) |
| SV | Severe Thunderstorm Warning | Gold (#FFD700) |
| FF | Flash Flood Warning | Green (#00FF00) |
| FL | Flood Warning | Cyan-green (#00FF7F) |
| WS | Winter Storm Warning | Pink (#FF69B4) |
| BZ | Blizzard Warning | Orange-red (#FF4500) |

**Mesoscale Discussions:** Magenta (#FF66CC) outline

#### Data Polling

| Product | Interval | Cache TTL |
|---------|----------|-----------|
| SPC Categorical + Probabilities | 15 min | 15 min |
| SPC Watches | 2 min | None (always fresh) |
| SPC MDs | 2 min | None |
| NWS Warnings | 2 min | None |

When you toggle a product on within the cache window, data displays instantly from cache. After TTL expiry, a fresh fetch is triggered automatically.

---

### 6.3 Satellite

**Button:** `SATELLITE` (checkable toggle)

Clicking the button expands the satellite drawer.

This controls live GOES weather satellite imagery. The offline USGS satellite basemap is separate and lives in the MAP drawer.

#### Coverage Mode Selection

| Button | Description |
|--------|-------------|
| **CONUS** | Full continental US view (1600×800 px, updates every 5 min) |
| **MESO-1** | Mesoscale sector 1 (1024×1024 px, updates every 1 min) |
| **MESO-2** | Mesoscale sector 2 (1024×1024 px, updates every 1 min) |

MESO-1 and MESO-2 buttons are disabled until the satellite system confirms those sectors are available. Hover over MESO-1 or MESO-2 buttons to preview the sector boundary on the map as a semi-transparent overlay.

#### VIS / IR (ARCHIVE only)

In ARCHIVE mode, **VIS** and **IR** buttons follow the sector buttons:

| Button | Shows |
|--------|-------|
| **VIS** | Band 2 visible reflectance, in grey (the default) |
| **IR** | Band 13 infrared cloud-top temperature: grey from warm (dark) to -30 °C (light), then blue, green, yellow, red and purple for colder tops, and white below -90 °C. A color bar under the row shows the scale. |

IR works at night and picks out deep convection; VIS shows more detail by day. Both are also in the command palette (Ctrl+K: "Satellite infrared", "Satellite visible"). The choice is saved with the session.

#### Opacity Slider

Controls satellite image transparency from 0% (invisible) to 100% (fully opaque). Default is 70%. Useful for seeing underlying map features through the imagery.

#### Playback Controls

Identical layout to the radar playback row:

| Button | Action |
|--------|--------|
| ⏮ | Jump to oldest frame |
| ⏪ | Step back one frame |
| ▶ / ⏸ | Start / pause auto-playback |
| ⏩ | Step forward one frame |
| ⏭ | Jump to newest frame |
| Timeline slider | Scrub to any cached frame |
| Time label | Current frame UTC time (--:--Z if no data yet) |
| Speed dropdown | Playback speed: 0.5× (1200 ms), 1× (600 ms), 2× (300 ms), 3× (200 ms) |

While radar data is on and SYNC is on in the radar drawer, these controls are off and the radar loop picks the satellite frame. See [Synced Loop](#synced-loop).

#### How Satellite Data Works

- **Source:** CONUS: nowCOAST WMS (`nowcoast.noaa.gov`, `goes_visible_imagery`, GOES-East and GOES-West) as map tiles. MESO: IEM GOES-East WMS (`mesonet.agron.iastate.edu`, GOES-19 as of 2025)
- **Request format:** WMS GetMap PNG
- **Frame cache:** Up to 10 frames per mode. MESO also keeps older frames at one per 5 minutes, up to 40 minutes back, so a synced radar loop finds a frame (lite mode keeps only the 10). IEM serves only the current MESO frame, so these build up while the sector is selected.
- **CONUS polling:** Every 5 minutes; 10 frames backfilled on mode selection
- **MESO polling:** Every 1 minute once sectors are confirmed available
- **Memory usage:** CONUS ~10–20 MB; each MESO sector ~20–30 MB

---

### 6.4 Soundings

**Button:** `SOUNDINGS` (checkable toggle, expands drawer)

Select which data source to use for Skew-T log-P soundings. Only one source is active at a time. Deactivating the button resets to HRRR mode.

| Button | Source | How to Trigger |
|--------|--------|----------------|
| **HRRR** | NCEP HRRR model via open-meteo (no API key) | Click anywhere on the map |
| **OBS** | Observed radiosondes via IEM RAOB API | Radiosonde station markers appear; click one |
| **NSSL** | Live only: the lidar truck's soundings via the NSSL API (needs `NSSL_API_KEY`). Another team's code provider can fill this button with its own soundings and name ([`docs/providers.md`](docs/providers.md)) | CLAMPS markers appear when data is available; click one |
| *(your soundings)* | Live only: one button per sounding source in `storm.toml` or a code provider ([Your Own Data Sources](#your-own-data-sources)) | Pick it: the launches in its lookback open in the sounding dialog, newest first on the slider, and refresh every `refresh_min` while shown. Greyed out when its API key isn't set |

ARCHIVE has no HRRR source, and NSSL's radiosondes and TROPoe are under the NSSL button (see *NSSL's archive data* in Archive Mode). In HRRR mode, a click on the map immediately starts fetching. In OBS and NSSL modes, station/truck markers are rendered on the map first; clicking a marker fetches and opens the sounding dialog. Once one of your sounding sources has been fetched, the dialog offers it as a comparison pill on any sounding within 150 km, the way it offers NSSL.

See [Section 10](#10-point-soundings) for full dialog documentation.

---

### 6.5 Surface Obs

**Button:** `SURFACE` (checkable toggle, expands drawer)

Displays live surface observation station models from ASOS/AWOS stations and from the station networks in `storm.toml` ([Your Own Data Sources](#your-own-data-sources)). With none set up, the drawer shows ASOS only.

#### Network Toggles (multi-select)

One button per station network in `storm.toml` (hover for its full name), then **ASOS**: IEM ASOS/AWOS current observations inside a box you draw. A network whose API key isn't in `.env` is greyed out and its tooltip names the key. A network set up with `where = "toolbar"` has its own toolbar button instead.

With the NSSL profile and `NSSL_API_KEY`:

| Button | Network | Coverage |
|--------|---------|----------|
| **OK** | Oklahoma Mesonet (OU) | ~120 stations across Oklahoma |
| **WTM** | West Texas Mesonet (TTU) | ~50 stations across West Texas |
| **KS** | Kansas Mesonet (K-State) | ~80 stations across Kansas |
| **CO** | Colorado Mesonet (CoAgMET) | ~90 stations across Colorado |
| **NE** | Nebraska Mesonet | Stations across Nebraska |
| **SD** | South Dakota Mesonet | Stations across South Dakota |

Any combination can be active at once. Each network refreshes on its own `refresh_min` (5 minutes unless set); ASOS fetches stations inside the selected bounding box and refreshes that saved domain every 5 minutes.

#### ASOS Bounding Box

When **ASOS** is enabled for the first time, the map enters bounding-box mode:

1. Click and drag on the map to draw the ASOS domain.
2. Release the mouse to accept the box.
3. STORM fetches current ASOS/AWOS observations from IEM for stations inside that box.
4. Station plots render on the map. Large boxes may briefly pause the UI while plots are prepared.

Toggling ASOS off and back on reuses the last selected bounding box. To replace it, click the **new box** link in the surface status text, then draw a new domain.

In ARCHIVE the drawer shows ASOS only, replayed on the archive clock (see *ASOS Replay and Damage Paths* in Archive Mode).

#### Station Model Display

Each station renders a compact model circle on the map showing:

| Position | Data |
|----------|------|
| Upper-left | Temperature (°F) |
| Lower-left | Dewpoint (°F) |
| Center | Wind barb |
| Upper-right | Pressure in mb/hPa |

Toggle station models on or off with the **Show plots** checkbox.

#### Freshness Coloring

The station-plot center dot is colored by observation age, not fetch time. Each network has its own thresholds (`fresh_min`, 5 and 10 minutes unless set), and the drawer's legend lists them. ASOS uses wider thresholds because routine reports are typically hourly:

| Source | Green | Yellow | Red |
|--------|-------|--------|-----|
| Station networks (default; the NSSL profile's mesonets) | ≤5 min | ≤10 min | >10 min |
| ASOS | ≤70 min | ≤90 min | >90 min |

The ASOS timestamp comes from IEM's `valid` observation time.

#### Data Sources

Each network's address and layout are its `[[sources]]` table. For the NSSL profile's:

| Source | Endpoint |
|--------|----------|
| OK Mesonet | NSSL API OK Mesonet JSON + Oklahoma Mesonet metadata |
| West Texas Mesonet | NSSL API WTM JSON + TTU Mesonet site metadata |
| Kansas Mesonet | NSSL API KS Mesonet JSON with embedded station metadata |
| Colorado Mesonet | NSSL API CO Mesonet JSON + CO station metadata |
| Nebraska Mesonet | NSSL API NE Mesonet JSON with embedded station metadata |
| South Dakota Mesonet | NSSL API SD Mesonet JSON with embedded station metadata |
| ASOS/AWOS | IEM `currents.json` for selected station IDs; station metadata from IEM METAR GeoJSON |

---

### 6.6 SFCOA Mesoanalysis

**Button:** `SFCOA` (checkable toggle, expands drawer; under the ⋯ overflow menu when the toolbar is narrow)

Displays NSSL SFCOA mesoanalysis contour products as labeled vector-tile overlays. SFCOA is a `contours` source in the NSSL profile (`[[sources]]`, [data-sources.md](docs/data-sources.md#contours-contour-lines-from-vector-tiles)); any team can add its own the same way, and its button and drawer then carry that source's name. SFCOA comes from the NSSL API, so its button is greyed out without `NSSL_API_KEY`.

#### Product Groups

SFCOA variables are grouped into compact columns so related fields are easy to scan (each run's `metadata.json` names a variable's group and lists the variables in the order the buttons go; variables in other groups share a last column):

| Group | Examples |
|-------|----------|
| **SURFACE** | Near-surface thermodynamic fields |
| **PARCEL** | Parcel diagnostics |
| **LAPSE RATES** | Low- and mid-level lapse-rate fields |
| **SHEAR** | Bulk shear fields |
| **SRH** | Storm-relative helicity fields |
| **COMPOSITE** | Composite severe-weather parameters |
| **UPPER AIR** | Upper-air diagnostics |
| **WINDS** | Kinematic wind products |

Click one or more product buttons to add those contours to the map. Clicking a selected product removes it. Active products remain selected when stepping between valid times if that variable is available at the new time.

#### Valid-Time Controls

| Button | Action |
|--------|--------|
| **REFRESH** | Reload the SFCOA API run index and product list |
| ⏮ | Jump to the oldest available valid time |
| ⏪ | Step back one valid time |
| ⏩ | Step forward one valid time |
| ⏭ | Jump to the latest available valid time |
| Status label | Shows the selected valid time, product count, or current loading state |

#### How SFCOA Data Works

- **Source:** NSSL API SFCOA run index and per-run metadata (`https://api.nssl.noaa.gov/data/sfcoa`, the source's `url` in the profile); button labels, tooltips and groups come from each run's `metadata.json`
- **Render format:** MapLibre vector tiles with contour lines and labels; each variable's tile file (`.pmtiles`, or `.mbtiles` while the profile says `tiles = "mbtiles"`) is downloaded whole and kept for a day
- **Layer behavior:** SFCOA is mutually exclusive with other large overlay drawers so the toolbar stays readable
- **Caching:** Selected product/time combinations are cached during the session for faster stepping back to recently viewed fields

---

### 6.7 Map Tools & Base Layers

**Button:** `MAP` (checkable toggle, expands drawer)

The MAP drawer groups map-context tools and optional offline base layers:

| Button | Description |
|--------|-------------|
| **ROUTE** | Opens turn-by-turn routing controls. Hidden in VIEWER mode. |
| **MEASURE** | Starts the distance measurement tool. |
| **LANDCOVER** | Toggles the offline NLCD land-cover raster when `tiles/storm_nlcd.mbtiles` is installed. |
| **SAT** | Toggles the offline USGS satellite basemap when `tiles/satellite.mbtiles` is installed. |
| *(your overlays)* | One button per GeoJSON overlay in `storm.toml` ([Your Own Data Sources](#your-own-data-sources)): target areas, road closures, field boundaries. Each refreshes every `refresh_min` while on. Live modes only, except an overlay with `archive = true` (fixed layers such as field boundaries), which ARCHIVE shows too. |

LANDCOVER and SAT are available to normal users. They appear only when the matching MBTiles file exists in the `tiles/` folder.

#### Land-Cover Controls

When LANDCOVER is enabled, a small drawer opens with an opacity slider and a compact NLCD class legend. Hovering over the NLCD overlay reports the land-cover class under the cursor when tile data is available.

NLCD is also available in the layer-order pill, so it can be reordered relative to radar, satellite, hazards, annotations, drawings, and other map overlays.

#### Satellite Basemap

The SAT button switches the underlying map into a cached USGS imagery basemap. It is an offline reference layer and does not replace the live GOES satellite overlay in [Section 6.3](#63-satellite).

---

### 6.8 Routing

**Button:** `MAP` → `ROUTE` (checkable toggle, expands drawer)

Provides turn-by-turn driving directions rendered on the map.

#### Setting Origin and Destination

Each of the **FROM** and **TO** fields takes an address, city or `lat,lon` pair, and has three small buttons beside it:

- **⊕** (pick on map): click it, then click a point on the map to fill the field.
- **Current GPS location**: fills the field with your vehicle's GPS fix. Greyed out until there is a fix.
- **Vehicle picker**: a menu of the vehicles STORM is tracking; picking one fills the field with its position. Greyed out until a vehicle is known.

Click **Get Directions** to fetch the route. The route geometry is drawn on the map as a colored line, and the directions list appears below with each maneuver's distance and turn arrow (↑, ←, →, ↖, ↗, ↩, etc.).

#### Auto-Re-Routing

If the vehicle drifts more than 100 m off the calculated route for 3 or more consecutive GPS fixes, STORM automatically re-fetches directions from the current position.

#### Arrival

When the vehicle is within 30 m of the destination, STORM shows an arrival notification and clears the route from the map.

#### Data Sources

| Function | Service |
|----------|---------|
| Turn-by-turn routing | openrouteservice (`api.openrouteservice.org`), with `ORS_API_KEY` in `.env` (free at openrouteservice.org) |
| Address geocoding | Nominatim (`nominatim.openstreetmap.org`) |

---

### 6.9 Annotations

**Button:** `ANNOTATE` (checkable toggle, expands drawer)

Annotations are field-condition markers placed on the map and synced to all vehicles over MQTT.

#### Annotation Types

| Icon | Type | Color | Use For |
|------|------|-------|---------|
| ✕ | Road Closure | Red (#E53935) | Blocked road — no through traffic |
| ▲ | Construction | Yellow (#FFD166) | Active road work |
| ~ | Flooded Road | Blue (#4A9EFF) | Standing water over road |
| ⚡ | Downed Power Lines | Yellow (#FFD166) | Electrical hazard on road |
| ! | Road Debris | Orange (#FF6B35) | Debris field on road |
| H | High Pressure | Blue (#4A9EFF) | Mark a high-pressure center |
| L | Low Pressure | Red (#E53935) | Mark a low-pressure center |

The pressure button opens a High/Low selector before placement, then creates the corresponding H or L marker.

#### Placing an Annotation

1. Click the desired annotation type button in the drawer — it highlights in that type's color.
2. Click any location on the map.
3. A dialog opens showing the clicked coordinates and an optional "Note" text field.
4. Add any relevant note (e.g., "Road completely blocked at intersection with CR-1234").
5. Click **Confirm**.
6. The annotation marker appears on the map immediately and is published to all connected vehicles via MQTT.

#### Editing or Deleting an Annotation

1. Click an existing annotation marker on the map.
2. The edit dialog opens, pre-filled with the existing note.
3. Edit the note or click **Delete** to remove it.
4. Click **Save** to confirm.

#### Moving an Annotation

1. Click an existing annotation marker on the map.
2. In the edit dialog, click **Move**.
3. The dialog closes and the marker becomes draggable — drag it to the new location.
4. On drop, a confirmation dialog shows the old and new coordinates.
5. Click **Confirm** to save the new position, or **Cancel** to revert.

Changes, moves, and deletions are published to all connected vehicles.

> **Note:** When the routing or measure tool is active, clicking on an annotation will forward the click to the active tool instead of opening the edit dialog.

---

### 6.10 Drawings

**Button:** `DRAWINGS` (collapsible drawer)

Drawings are meteorological features sketched on the map — synced to all vehicles over MQTT.

#### Drawing Types

**Fronts:**

| Button | Front | Symbol | Color |
|--------|-------|--------|-------|
| Cold Front | Cold Front | Triangles pointing in direction of motion | Blue (#4A9EFF) |
| Warm Front | Warm Front | Semicircles pointing in direction of motion | Red (#E53935) |
| Stationary Front | Stationary Front | Alternating triangles + semicircles on one line | Blue + Red |
| Occluded Front | Occluded Front | Alternating triangles + semicircles, same side | Purple (#9C27B0) |
| Dryline | Dryline | Scalloped arcs above the line | Brown (#D4872E) |

#### Custom Shapes

| Button | Shape | Styling |
|--------|-------|---------|
| — | Polyline | Custom title, color, and line style |
| □ | Polygon | Custom title, color, and line style; fill follows line color |

#### Drawing a Front or Shape

1. Click the front type or shape button — it highlights and the map enters drawing mode (crosshair cursor).
2. Click the map to place the first point, then continue clicking to trace the line.
3. **Finish:** Double-click the last point.
4. A dialog prompts for a title. Polylines and polygons also provide color and line-style controls.
5. The feature appears on the map with the appropriate symbol and a title label at its center.

#### Editing or Deleting a Drawing

1. Click an existing drawing on the map.
2. The dialog opens with the current title.
3. For fronts: an option to flip direction (reverses the symbol orientation) is available.
4. For polylines and polygons: edit the title, color, or line style.
5. Click **Delete** to remove the drawing if needed.
6. Click **Save** to confirm.

All changes are synced via MQTT.

#### Disabled If

`--disable-annotations` is passed at launch — this flag disables annotations, drawings, station plots, and the storm motion cone together.

---

### 6.11 Storm Motion Cone

**Button:** `CONE`

Places a storm motion cone on the map — a forward-propagating sector showing the expected storm track with a 20° angular spread and time rings.

#### Creating a Cone

1. Click the **CONE** button.
2. Click any location on the map (typically the current storm location).
3. A dialog opens with two fields:
   - **Speed (knots):** Expected storm forward speed. Default 35 kts. Range: 0–200.
   - **Heading (degrees):** Direction the storm is coming *from*, as the dialog says. Default 240° (from the WSW, moving toward the ENE). Range: 0–359.
4. Click **Add to Map**.

The rendered cone includes:
- A base point at the clicked location
- Two radial bounding lines extending 1 hour of travel at ±20° spread
- Concentric time rings at 0.25, 0.5, 0.75, and 1.0 hour
- A center spine line showing the primary motion direction

**Color:** Cyan (#00CFFF)

#### Editing or Deleting a Cone

1. Click the cone on the map.
2. The dialog shows the current speed and heading.
3. Update the values or click **Delete**.
4. Click **Save**.

Cones are synced via MQTT (`<topic_root>/cones/{id}` topic).

#### Disabled If

`--disable-annotations` is passed at launch.

---

### 6.12 Measurement Tool

**Button:** `MAP` → `MEASURE` (checkable toggle)

Measures great-circle distances on the map.

#### Usage

1. Click the **MEASURE** button — the map cursor changes to a crosshair.
2. Click a start point on the map — a dot appears.
3. Move the mouse — a preview line and live distance label follow the cursor.
4. Click an end point — the segment is fixed.
5. Continue clicking to measure additional segments (multi-segment path).
6. **Finish:** Press **Escape**.
7. Distance is shown in miles and kilometres (e.g., "47.3 mi / 76.1 km"), the `[units]` distance first.

**Clear:** A **Clear** button removes all measurement geometry from the map.

**Color:** Cyan lines and labels

---

### 6.13 Station Plots

**Button:** `STATION PLOTS` (checkable toggle)

Displays MetPy-style meteorological station plot icons at each tracked vehicle's current position.

#### Station Plot Layout

```
  [Temp °F]   [Pressure mb]
       ○──wind barb
  [Dewpt °F]
```

| Position | Data | Color |
|----------|------|-------|
| Upper-left (NW) | Temperature (°F) | Red text |
| Lower-left (SW) | Dewpoint (°F) | Green text |
| Upper-right (NE) | Pressure in mb/hPa, rounded to nearest whole value | White text |
| Center | Wind barb (meteorological convention, barbs point toward station) | White |
| Center dot | Anchor | White circle |

**Wind Calm:** If wind speed is ≤2 kt, only a small circle is rendered (no barbs).

#### How Station Plots Are Rendered

Station plots are generated as PNG images (135×135 px, transparent background) by MetPy and matplotlib, then pushed to the map as image markers. Each vehicle's plot is cached and only re-rendered when new observation data arrives.

#### Disabled If

`--disable-annotations` is passed at launch.

---

### 6.14 Deployment Locations

**Button:** `PREV LOCS` (checkable toggle, expands drawer)

Displays NSSL's historical deployment locations as markers on the map, filtered by research quality metrics. Live modes with the NSSL provider only.

#### Quality Metric Selector

| Button | Metric | Description |
|--------|--------|-------------|
| **RANK_ABI** | ABI Rank | Rank by ABI-derived quality index |
| **RANK_AOI** | AOI Rank | Rank by area-of-interest quality |
| **RQI** | Road Quality Index | Continuous road surface quality score (0–1) |

#### Threshold Slider

A slider controls the minimum quality threshold. Only locations meeting or exceeding the threshold are shown. The legend below the slider shows a green → red color ramp corresponding to the quality range, updated live as you drag.

#### Marker Radius

An additional slider controls the displayed circle radius for each location marker.

#### Data Source

- **File:** `[map] deployment_locations` in `storm.toml`: a CSV file or URL with `lat`, `lon` and optional `rank_abi`, `rank_aoi`, `rqi` columns. The NSSL profile uses `providers/nssl/locs/deployment_locations.csv`
- Markers are pre-loaded at startup; toggling visibility or threshold is instant.

#### Disabled If

`--disable-deploy-locs` is passed at launch, `[map] deployment_locations` isn't set, or the locations file is missing.

---

### 6.15 Vehicle Panel

**Button:** `VEHICLES` (toggles the right-side dock panel)

The vehicle panel provides a live roster of all tracked vehicles and time-series observation graphs.

#### Vehicle List (upper section)

Each vehicle row shows:

| Field | Description |
|-------|-------------|
| **ID** | Vehicle identifier (e.g., "Chase1") |
| **Position** | Compact lat/lon (e.g., "35.24 | -97.45") |
| **Last Update** | Relative time (e.g., "just now", "2 min ago") |
| **Color dot** | Matches the vehicle's marker color on the map |

Click a vehicle row to **zoom the map to that vehicle's location**.

Vehicles are sorted by most-recently-updated first.

#### Vehicle Detail Section (lower section)

When a vehicle is selected, the detail section shows:

| Field | Description |
|-------|-------------|
| **Temperature** | Current temperature in °F (red text) |
| **Dewpoint** | Current dewpoint in °F (green text) |
| **Wind** | Wind speed (kt) and direction (deg) with arrow icon |
| **Pressure** | Atmospheric pressure in mb (purple text) |
| **TIMESERIES Button** | Opens the vehicle timeseries dialog (only shown for vehicles with observation history) |

The TIMESERIES button appears only for non-local vehicles that have published meteorological observations. See [Section 6.16](#616-vehicle-timeseries) for full timeseries documentation.

---

### 6.16 Vehicle Timeseries

**Access:** Click the **TIMESERIES** button in the vehicle detail section of the Vehicle Panel.

Displays interactive time-series plots of meteorological observations for the selected vehicle. Works in both live MQTT mode and archive mode.

#### Dialog Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Vehicle Timeseries — {vehicle_id}                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [Temperature / Dewpoint subplot]                               │
│  Red line: Temperature (°F)                                      │
│  Green line: Dewpoint (°F)                                       │
│  ━ T --°F    ━ Td --°F          ← inline legend / cursor readout │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [Wind Speed / Direction subplot]                               │
│  Cyan line: Wind speed (kt) — left y-axis                       │
│  Yellow scatter: Wind direction (deg) — right y-axis            │
│  ━ Speed -- kt    ● Dir --°     ← inline legend / cursor readout │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [Pressure subplot]                                             │
│  Purple line: Pressure (mb)                                     │
│  ━ Pressure -- mb               ← inline legend / cursor readout │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### Three Subplots (shared x-axis)

**1. Temperature / Dewpoint**
- Red line: Temperature (°F)
- Green line: Dewpoint (°F)
- Y-axis label: °F

**2. Wind Speed / Direction (dual y-axes)**
- Cyan line: Wind speed (kt) — left y-axis
- Yellow scatter points: Wind direction (deg) — right y-axis
- Left y-axis label: kt
- Right y-axis label: deg

**3. Pressure**
- Purple line: Pressure (mb)
- Y-axis label: mb

#### Data Grid & Gap Handling

Observations are snapped to a 10-second time grid. Missing data becomes NaN, creating visual gaps in the plots without connecting across missing points. This prevents misleading interpolation across data outages.

#### Interactive Features

**Inline Legend / Cursor Readout**
Each subplot has an inline legend rendered directly below its axis (similar to uPlot's built-in legend). At rest, the legend shows series names with `--` placeholder values. Hover over any subplot to update all three legends with the timestamp and current values at that point:
- Temperature and dewpoint (below panel 1)
- Wind speed and direction (below panel 2)
- Pressure (below panel 3)

The cursor snaps to the nearest 10-second grid point and only displays values where actual observations exist (no readouts in data gaps). A vertical crosshair line appears across all three subplots at the snapped time.

**Scroll Wheel Zoom**
Scroll the mouse wheel while hovering over any subplot to zoom in/out on the x-axis, centered on the cursor position. Zoom applies to all three subplots simultaneously (shared x-axis).

**Click-Drag Selection Zoom**
Click and drag horizontally across any subplot to select a time range. A translucent highlight rectangle appears across all three panels during the drag. Release to zoom into that range. The selection must span at least 1% of the current x-axis range to trigger zoom. Zoom applies to all three subplots.

**Double-Click Reset**
Double-click any subplot to reset the zoom to the full data extent.

#### Live vs. Archive Mode

**Live Mode:**
The dialog shows all observations received via MQTT since the vehicle first appeared in the current session. The plot updates automatically as new observations arrive.

**Archive Mode:**
The dialog shows all observations from the archive MQTT log up to the current archive time. As you scrub the archive timeline or play forward, the plot updates dynamically to reflect the data available at that moment in the replay.

#### Color Palette

| Parameter | Color | Hex |
|-----------|-------|-----|
| Temperature | Red | #FF6B6B |
| Dewpoint | Green | #3DDC84 |
| Wind Speed | Cyan | #4FC3F7 |
| Wind Direction | Gold | #FFD700 |
| Pressure | Purple | #B39DDB |

#### Unit Conversions

Wind speed is converted from m/s (stored in Observation records) to knots using the factor 1.94384.

#### Dialog Behavior

The dialog is reusable — if you close it and click TIMESERIES again for the same vehicle, the same dialog instance is raised and focused rather than creating a duplicate. Each vehicle has its own independent dialog instance.

---

## 7. Status Bar

The status bar runs along the bottom of the main window.

| Section | Content |
|---------|---------|
| **Left** | Mouse coordinates (lat/lon) and zoom level, updated live as you move the cursor over the map. Example: `35.2413 | -97.4521   Z 6.2` |
| **Center** | Status messages: "Ready" (default), "MQTT connected", error messages (e.g., "Radar fetch error: timeout") |
| **Right** | Current UTC clock (updates every second). Example: `18:45:32 UTC`. While a loop shows a past frame, a **◷ LOOP HH:MMZ** chip sits before the date; click it to return to live. |
| **Broker** | **● MQTT OK** while connected to the broker, **● MQTT OFFLINE** while not (the center then gives the reason), **● NO BROKER** when `storm.toml` names none. Not shown in ARCHIVE. |
| **Far right** | Network status indicator pill: **Green** = Online, **Yellow** = Slow (>1 s response), **Red** = Offline. Checked every 30 seconds via Cloudflare DNS. |

---

## 8. Archive Mode

Archive mode lets you replay any past session at a chosen UTC date and time. All data layers — radar, satellite, hazards, soundings, and vehicle positions — are fetched from historical archives and driven by a central time controller.

### Entering Archive Mode

Select **ARCHIVE** in the launch dialog and choose a UTC start date/time using the calendar picker. Click **LAUNCH**. The map opens straight away; a small indicator at the top left lists what is still loading (hazards, satellite, vehicle tracks, radar) and disappears when everything has arrived. The map is usable meanwhile.

The session runs from 00Z on the chosen date to 06Z the next morning, trimmed to 30 minutes after the day's last activity (driving, or an annotation, cone, drawing or sector change from 12Z on) once vehicles and MQTT history have loaded. Those defaults suit a field day in the Americas (local afternoon and evening fall between about 18Z and 06Z). A team elsewhere moves the window with `session_start_utc`, `session_hours` and `session_activity_after_hours` under `[archive]` in `storm.toml` ([`docs/configuration.md`](docs/configuration.md#archive-recorded-history-and-vehicle-obs)); the times below are the defaults.

The window title and status pill display the current archive timestamp while in this mode: `[ARCHIVE YYYY-MM-DD HH:MMZ]`.

---

### Archive Controls Bar

A controls bar sits at the bottom of the map window in archive mode (it is not visible in live modes). Beside the UTC clock it shows the time in your local zones: `[map] time_zones` in `storm.toml` (up to three), or this computer's own zone; the NSSL profile shows Central and Mountain.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  21:04:30Z │ 16:04 CDT / 15:04 MDT      Radar: … │ Sat: … │ OBS: …         │
│  ──────────────────────── time slider ───────────────────────────────    │
│  00Z    02Z    04Z   …   (radar scan ticks, coverage, hour labels)        │
│  -1m  -10  ▶  +10  +1m │ ⏮  ⏭ │ SPEED 1×▾ │ JUMP                         │
└──────────────────────────────────────────────────────────────────────────┘
```

#### Playback Buttons

| Button | Action |
|--------|--------|
| -1m / +1m | Step back / forward 1 minute |
| -10 / +10 | Step back / forward 10 seconds (**Left** / **A**, **Right** / **D**) |
| ▶ / ⏸ | Start / pause automatic playback (**Space**) |
| ⏮ / ⏭ | Previous / next radar scan (enabled once the station's scan list has loaded) |
| JUMP | Type a time to jump to |

**Home** / **End** jump to the start / end of the session. Playback pauses at the end of the session.

#### Time Slider

The slider spans the whole session (00Z to as late as 06Z the next morning). Drag to preview a time; the data loads when you let go. Under it, short ticks mark each radar scan, a strip shows where instruments such as lidar have data, and labels mark the hours.

#### Speed Selector

The SPEED box sets the playback multiplier: 1× (real time), 5×, 10×, 30×, 60×, 120× or 300× (5 minutes of archive time per second).

#### Layer Status Indicators

| Indicator | What It Shows |
|-----------|---------------|
| **Radar:** | Product, tilt and scan time of the frame on screen; for velocity, `· dealiased` or `· not dealiased` |
| **Sat:** | Satellite status: `off` until SATELLITE is turned on, then the sector, band and frame time; `none` on a day with no GOES imagery (hover for why) |
| **OBS:** | Vehicle data source: `probing`, `1-second (n)`, `catalog (n)`, `partial n/m`, `MQTT fallback`, or `no data for this date`. `MQTT fallback` also when no vehicle-obs source is set up: vehicles move on their recorded positions |

These update as each fetcher resolves data for the current archive timestamp.

---

### How Archive Data Works

| Layer | Source | Behavior |
|-------|--------|----------|
| NEXRAD Radar | Unidata THREDDS archive | Fetches Level 2 or Level 3 scans nearest to the current archive time |
| GOES Satellite | NOAA GOES-16 / GOES-19 archive on AWS | Listed and fetched only once SATELLITE is turned on; shows the newest frame at or before the archive time, VIS or IR. Visible CONUS frames download only the part of the file covering the map's CONUS view (about 30 of 66 MB). Before 28 Feb 2017 (no GOES-16) the drawer is disabled and **Sat:** reads `none`. |
| SPC/NWS Hazards | Archived GeoJSON products | Fetched once at session start for the session date |
| Soundings | IEM RAOB (OBS); under NSSL, the lidar truck's radiosondes and CLAMPS TROPoe on NSSL THREDDS | Fetched on demand, up to the archive clock on its UTC day. No model (HRRR) soundings in ARCHIVE |
| Lidar wind profiles | CLAMPS / lidar truck VAD winds on NSSL THREDDS | Loaded for the whole session the first time NSSL > LIDAR > WINDS is pressed, then kept |
| CLAMPS trailers | Trailer met tower or MWR surface fields on NSSL THREDDS | Loaded at session start for the session span, replayed on the clock as map markers |
| Doppler lidar scans | Lidar truck and CLAMPS trailer PPI / CSM files on NSSL THREDDS | Found at session start and loaded in the background; a picked lidar's scans open in the scan window, and on the map with its "draw on map" |
| Doppler lidar stares | The same lidars' fixed-point (vertical) files on NSSL THREDDS | Found at session start; a lidar's load when STARES picks it, into a time-height window |
| Vehicles, annotations, drawings, cones, scan sectors | Recorded broker traffic (see [Recorded Team Traffic](#recorded-team-traffic)); one-second vehicle obs from your own logger files or, with the NSSL provider, FOFS on NSSL THREDDS (see [Your Own Archive Data](#your-own-archive-data)) | Replayed in time order on the clock |
| ASOS | IEM ASOS history | Stations inside a drawn box, fetched once for the session, replayed on the clock |
| Damage Paths | NWS Damage Assessment Toolkit, else NCEI Storm Events | Tornado paths inside a drawn box, within a day of the session date |

Each layer updates automatically as the archive clock advances. Data fetches are triggered when the clock crosses boundaries (e.g., every radar scan interval).

### Recorded Team Traffic

The team's own vehicles, annotations, drawings, cones and scan sectors replay from recordings of the broker: one file per kind per UTC day (`storm.vehicles.20260506` and so on, named after your `topic_root`), read for the chosen day and the next morning. STORM looks for them in the sources listed under `[archive] history` in `storm.toml`, in order, and uses the first that has each file: folders on this machine, web addresses, or `nssl` for NSSL's own recordings (with the NSSL provider on). Without that setting it reads NSSL's recordings when the provider is on, then the `recordings/` folder.

To record your team's days, either run the recorder on a machine that stays connected through the field day:

```bash
python -m archive.recorder
```

or, in MONITOR mode, check **File > Record Broker Traffic** (remembered for the next MONITOR launch). Both write to `recordings/` (`[archive] recordings` changes it) and only need a read-only login. A day with no recording replays without the team's traffic. File layout, sources and what the recorder writes are in [`docs/archive-data.md`](docs/archive-data.md).

### Your Own Archive Data

Vehicles can replay on their loggers' one-second files as well as their recorded positions. List where the files are under `[archive] vehicle_obs` in `storm.toml`: folders on this machine, web addresses, or `nssl` for NSSL's FOFS mesonet files (the default with the NSSL provider on). Below each folder or web address a vehicle's file for a UTC day sits at `vehicle_obs_path`, `{vehicle}/{date}.csv` by default (`wx1/20260506.csv`):

```toml
[archive]
vehicle_obs = ["vehicle-obs"]
vehicle_obs_path = "{vehicle}/{date}.csv"
```

The files are CSV, read with the same `[obs_columns]` as live obs files. For each vehicle STORM reads the files dated the day before, the day and the day after (for loggers that name files by the local day) and keeps the rows in the session. The first source with rows for a vehicle wins; a vehicle no source has moves on its recorded positions, and one that only has files is added to the map anyway. Every source's rows get the same checks: missing-value codes (−999, a 999 wind) and impossible readings are dropped, as are fixes at 0,0, short jumps away and back faster than a vehicle can drive, and frozen positions beside them; one row is kept per second.

The calendar and **Browse available cases…** list each vehicle in a folder by its file names. A web source can't be listed, so give it a `dates.json` (`{"dates": [...], "vehicles": {"wx1": ["20260506"]}}`) to shade its days; without one its days aren't shaded but still load. Case packages carry these files like the rest, and a package made on another machine serves a folder's files by their path inside the folder. Details are in [`docs/archive-data.md`](docs/archive-data.md#vehicle-obs-files).

---

### Storm Tracks

**TRACK** (**T**, archive only) marks a storm's position at each radar frame. Opening the drawer enters track mode:

- **Click** the map to place the point for the radar frame on screen (clicking again at the same frame moves it).
- **Drag** a point to move it to the current frame's time; hold **Alt** to keep its own time.
- **Right-click** to add, move, rename or remove the reference marker, or to select or delete the point under the cursor. **P** places a point at the marker.
- **Delete** / **Backspace** removes the selected point; **Ctrl+Z** / **Ctrl+Shift+Z** undo and redo (also UNDO / REDO in the drawer). CLEAR removes every point and can be undone.

The drawer shows the point count and the storm motion: **Mean** over the whole track (2 points needed) and **Now**, at the clock. **TABLE** lists every point's exact time and position; it follows the map, and double-clicking a row pauses and moves the clock there. **Line**, **Points** and **Keep on map** choose what stays drawn when the drawer is closed.

Every edit saves the track to `storm_tracks/storm_YYYYMMDD_track.csv` next to `main.py` (one file per case date; git-ignored). The next archive session on that date opens it automatically. **IMPORT** replaces the track with a CSV (STORM's or MESO-VIEW's columns; undoable), **EXPORT** saves a copy elsewhere, and **FOLDER** shows the folder.

When a track has motion, the sounding viewer plots it as an **OBS** marker on the hodograph and adds an **SRH obs** column (0–500 m, 0–1 km, 0–3 km).

### Observation Trails

At the bottom of the VEHICLES panel in archive mode, **show trails** draws each platform's recent path (one-second vehicle obs where there are some, from your own files or FOFS, else the recorded MQTT positions), colored by a quantity:

- the quantity: measured fields or derived ones (θ, θv, θe, θw, mixing ratio, u / v, radial and tangential wind);
- the window: the last 5, 10, 15, 30 or 60 minutes before the clock;
- **wind barbs** along the trail;
- **time-to-space** and **storm-relative** barbs, which need a storm track's motion (the *Storm-relative needs a track ▸* link opens TRACK).

A color bar with units sits under the options. Hover a trail for its readout. Trail settings aren't saved with the session.

### Velocity Dealiasing and Sharp Radar

With velocity on screen in archive mode, a **DEALIAS** button appears after the tilt picker in the RADAR drawer. It unfolds aliased velocities with a region-based method (the same approach as Py-ART's). The archive bar's Radar status says whether the frame is `dealiased` or `not dealiased` — the latter when the scan records no Nyquist velocity or dealiasing failed; a raw field is never labeled dealiased. The choice is saved with the session's radar settings.

When the clock has been still for half a second, the frame on screen is re-rendered sharper (up to 4096 px, stepping down on slower machines). The first pause per station can take several seconds; later ones are quicker, and the 12 most recent sharp frames are kept, so scrubbing back shows them at once.

### ASOS Replay and Damage Paths

Both are picked by drawing a box on the map, like live ASOS: turn the button on, then drag a box. **Esc** or a right-click backs out, and with nothing loaded the button turns off again. Neither needs the NSSL servers.

**SURFACE > ASOS** fetches the ASOS/AWOS history of every station inside the box (up to 400) from IEM for the whole session — its UTC day and the next morning to 06Z — and shows each station's latest report at the archive clock as a station plot. The center dot is colored by the report's age at the clock (green ≤ 70 min, yellow ≤ 90 min, red older), so a station that stopped reporting turns red as playback moves on. Stepping back before a station's first report removes it. The drawer's status line says how many stations are loading and loaded. **new ASOS bbox** draws another box; the stations shown stay until it is drawn. **show plots** hides the plots without turning ASOS off. With a storm track, trails in *time-to-space* mode include the ASOS stations as well (their pressure is sea-level, so pressure-based quantities stay blank for them). The station networks from `storm.toml` (the NSSL profile's regional mesonets among them) are live only, so the SURFACE drawer shows ASOS only.

**HAZARDS > DAMAGE PATHS** finds NWS damage-survey tornado paths inside the box, surveyed within a day of the session date. They come from the Damage Assessment Toolkit (DAT); where it has none (common before about 2015), from NCEI Storm Events, whose paths are straight lines from each tornado's start to its end point. Paths are colored by EF rating (EF0 teal, EF1 green, EF2 yellow, EF3 orange, EF4 red, EF5 purple, unrated light blue), with a white dot at the start and a colored dot at the end. Hovering a path shows its rating and source, start and end times (UTC), length, width and peak wind. The status line says how many were found and from which source. To search another box, turn DAMAGE PATHS off and on again. The first NCEI search of a year downloads that year's Storm Events file (tens of MB); later searches reuse it.

Both sources are recorded for case packages (*ASOS observations*, *Damage paths*), so a package can carry them; opening one doesn't redraw the boxes.

---

### NSSL's archive data (the NSSL button)

NSSL's field data on its public THREDDS server is in every ARCHIVE session, for every team, with no key: the **NSSL** button after RADAR opens a drawer with **VEHICLES · LIDAR · SOUNDING · RETRIEVALS**. Nothing in it asks the NSSL API. An item with nothing for the date is greyed out ("none for this date" on hover); NSSL's instruments were out on only some days. A team can leave NSSL out with `[providers.nssl] enabled = false` in `storm.toml`.

**VEHICLES.** NSSL's recorded vehicles and their scan sectors, their FOFS one-second obs and the CLAMPS trailers' surface obs. With the NSSL profile these are the team's own vehicles: VEHICLES starts on, and turning it off hides them. For every other team it starts off; turning it on loads NSSL's recordings for the session, and NSSL's vehicles appear tagged **NSSL** ("NSSL probe1", "NSSL dltruck") beside your own. NSSL's annotations, drawings and cones stay out of other teams' maps. NSSL's vehicles publish under their FOFS folder names (`dltruck`, `probe1`…); recordings from before 3.0 say `lid1` and `p1`–`p7`, which read as those names.

**LIDAR** opens the lidar drawer (below): the lidar truck's and the CLAMPS trailers' Doppler lidar scans, **STARES** for their vertical stares, and **WINDS** for their wind profiles.

**Lidar wind profiles (NSSL > LIDAR > WINDS).** The first press loads every lidar wind source for the session (its UTC day and the next morning to 06Z): VAD winds from the lidar truck's two lidars and from the CLAMPS1 and CLAMPS2 trailers, retrieved from their high-elevation VAD rings. When more than one has data, pick the instrument from a list (with its number of profiles). The VAD window opens on the latest profile at or before the archive clock; the scrubber and the time-height view cover the whole session. The first load can take a while; later presses reuse it. Cells with a failed fit or fill values are left out. Above the aerosol layer a VAD fits noise, so the window's **min intensity** hides levels whose intensity (the lidar's SNR + 1) is below it: it starts at 1.002, which clears most of the noise on the CLAMPS and truck lidars; all the way left ("off") shows every level. The time shown stays put as it moves, and a profile with no levels left drops out of the scrubber. NEXRAD VADs have no intensity, so the control isn't there.

**SOUNDING: the lidar truck's radiosondes** (on THREDDS from April 2022) on the clock's day up to the clock. A launch is placed on the lidar truck's recorded track (one-second data, else MQTT) when it has a fix within a minute of launch; otherwise the header says the launch position is unknown.

**RETRIEVALS: CLAMPS TROPoe**, temperature and humidity retrieved from a trailer's AERI and/or microwave radiometer every few minutes, on the clock's day up to the clock. They have no wind (the hodograph reads "no wind") and come from one trailer only. Radiosondes and retrievals are separate products; each says when it has none, or why it couldn't be read.

Both open the sounding viewer, which still offers OBS (and the others) as comparison pills.

**CLAMPS trailers on the map.** Each trailer's surface observations (temperature, dewpoint from humidity, pressure, wind) replay on the archive clock as a lidar marker labeled CLAMPS1 / CLAMPS2, with time series and trails like the mobile mesonet. A trailer leaves the map once its latest report is five minutes old. CLAMPS2's met tower is used when it has the day (2016–2021, without wind direction: its files say the trailer's heading isn't applied); otherwise the radiometer's surface sensors (to 2023). Days whose sensors recorded nothing show no trailer.

**Doppler lidar scans (NSSL > LIDAR).** The lidar truck's and the CLAMPS trailers' low-angle sweeps, scanning as a radar does (PPI scans, and the sector sweeps in their continuous-scan files, at 5° elevation or less). LIDAR is enabled once the date is known to have lidar files. They load in the background once the first radar image and the vehicle tracks are in ("Loading lidar scans…" in the indicator). Without the NSSL profile, nothing downloads until LIDAR is first opened: that loads NSSL's vehicles too, and the scans follow once the truck's track is in, so the purple dots take a moment the first time. While LIDAR is open, every place a lidar scanned from this session is marked with a purple dot: a trailer's site, or each stop of the truck (labeled *Truck 1*, *Truck 2*…). Hovering a dot tells when it scanned and how its scan directions were set; clicking it pauses the clock at its first scan and opens its **scan window**; its scanning periods show as a purple strip under the time slider, and the drawer's button (hover for its position and distance from the radar; click to center the map on it) names it. **SCANS** opens the window again.

The scan window shows one scan at a time in km east and north of the lidar, north up, with range rings:
- **field** (velocity, intensity, backscatter, as recorded), **colours** (the scale's low and high ends, and **RESET COLOURS**);
- **min intensity**: gates whose intensity (the files' SNR + 1) is below it are hidden, which clears the noise beyond the aerosol layer (about 1.005–1.02 usually does it; all the way left shows every gate);
- ◀ ▶ and the scan list pick a scan; **follow the clock** shows the scan at the archive clock, each held until the next (up to 10 minutes);
- under the cursor: range, azimuth, height, position, the field's value and the gate's intensity; drag a box (the toolbar's zoom) or scroll to zoom, pan, double-click to go back; the toolbar also saves a PNG;
- **draw on map** (off by default) draws the scan shown, with this field and threshold, on the map.

Only sweeps at 5° or less count: higher sweeps are other scan strategies (the 60–70° rings many files hold are the VAD scans behind WINDS), so on many days LIDAR reads "No lidar PPI scans this session". The truck's files carry no position, so its scans are placed from its one-second track (else its MQTT track), fix by fix within a minute. Its scan directions are relative to the truck and turned to north by the heading in its files; when a file has none, the heading comes from the truck's compass in its mesonet data and an amber ⚠ says so (hover for the estimate). With neither, the scans can't be oriented: a red ⚠, and the stop is listed but its scans aren't drawn. Velocity is shown as recorded, positive away from the lidar, with no correction for the truck's motion. Files over 150 MB load only once their lidar is picked; files over 768 MB (some trailer continuous-scan days run to 1–2 GB) aren't downloaded, and the status line says so. Downloads are kept in `~/.cache/storm/raw_lidar` (up to 3 GB, least recently used removed first).

**Lidar stares (NSSL > LIDAR > STARES).** The same lidars' fixed-point files: rays straight up, one every second or two. STARES is on once a lidar has a stare file for the date (LIDAR opens for stares alone, too), and opens a window of vertical velocity against time and height for the session (12Z to the next morning), on the picked lidar if it has stares, else the first; the list at the top switches lidars (each loads when picked; a day's stares can be a few hundred MB, and the window shows how far the download has got):
- **field**: vertical velocity (positive up), intensity, or backscatter (log10);
- **min intensity** (starts at 1.010: above the boundary layer a stare is mostly noise) and **colours**, as in the scan window (velocity starts at ±3 m/s);
- **cloud base**: the cloud-base height the files record, as green dots;
- the archive clock as a dashed yellow line;
- under the cursor: the time, height, value, intensity and cloud base of the nearest ray; box or scroll zoom, pan, double-click to go back.

Rays are averaged into columns as wide as the view needs, so a whole day draws at once and zooming in brings back single rays; time with no rays is blank. On days the lidar ran continuous scans, it looks up for one ray between conical scans, which show as thin columns. The files say positive velocity is toward the lidar, but as with the scans that's not how they're written: the stares agree in sign with the conical scans taken between them, so velocity is shown as recorded, positive up. Every lidar product (stares, scans, and the VAD and CSM wind profiles) leaves out its first four gates, which are inside the lidar's near-range blind zone (noise, or a fixed velocity such as +35 m/s, which a VAD fits as calm air); NSSL's own processing hides them too. Some files on THREDDS were cut short when they were copied there (CLAMPS1 on 2023-06-09 is 32 KB of 52 MB); the window says "file incomplete on THREDDS" for those.

All of these are recorded for case packages (*Instrument wind profiles, sondes and retrievals*, *Instrument surface obs*, *Raw lidar scans*).

---

### Limitations in Archive Mode

- Vehicle positions come from recorded broker traffic and vehicle-obs files ([Recorded Team Traffic](#recorded-team-traffic), [Your Own Archive Data](#your-own-archive-data)) — a vehicle that is in neither won't appear.
- Real-time MQTT sync (annotations, drawings) is inactive in archive mode.
- Local file watcher (Track A) and GPS reader (Track B) are inactive.
- Network connectivity is still required — archive data is fetched from remote servers at playback time — unless the case was opened from a case package that holds the data (below).

---

### Menu Bar and Case Packages

The menu bar has **File**, **Edit**, **View** and **Help**. On macOS it sits in the system menu bar at the top of the screen; on Windows and Linux it is a thin row above the toolbar. Every entry is also in the command palette (**Ctrl+K** / **⌘K**).

| Menu | Entries |
|------|---------|
| File | Open Case Package… (**Ctrl+O**), Export Case Package… (**Ctrl+Shift+E**), Show Case Packages Folder, Video Studio (**Ctrl+Shift+V**), Change Day… (archive mode only); Save Map Screenshot… (**Ctrl+S**) |
| Edit | Undo / Redo Track Edit (**Ctrl+Z** / **Ctrl+Shift+Z**, archive mode only; Undo / Redo Studio Edit while the Video Studio is open) |
| View | Error Log (**Ctrl+E**), Debug Panel (**Ctrl+D**) |
| Help | STORM User Guide, About STORM |

A **case package** is a ZIP holding one archive case: the date's storm track, the case settings (date, session window, clock, radar station, product, tilt and dealiasing), the address and SHA-256 checksum of every source file the session loaded and, optionally, the data files themselves.

**Export** (*File > Export Case Package…*) first asks what to include: the storm track and each type of data the session loaded (radar volumes, one-second mesonet, recorded vehicles and annotations, soundings, satellite, hazards…), each with its file count and size, all ticked by default. The **time frame** (UTC) limits radar and satellite scans to that span; daily files and everything else are packed whole. It starts at the storm track's span with 30 minutes either side, or the hour either side of the clock when the date has no track; *Whole session* packs everything. *Include scans not yet loaded* adds the scans the catalogs list for the frame that you hadn't played through, so the case opens offline with nothing missing. Packing runs in the background with a progress bar and Cancel; the package goes to `data/case_packages/<date>/` by default. A file that changed upstream since you viewed it is packed with both checksums recorded.

**Open** (*File > Open Case Package…*) imports the package's track (asking first if the date already has a different one; Edit > Undo Track Edit brings yours back), switches to its radar station, product and tilt, moves the clock to its time, and loads the packed files from the package instead of downloading them. Anything the package doesn't hold is downloaded from its source as usual. A package for another date offers to close this session and open STORM on that date, without going back to the launch window. The launch window's *Open case…* opens one too. To open a case with no network at all, start STORM with it:

```bash
python main.py --case data/case_packages/20240427/STORM_case_20240427_KFDR_2030Z.zip
```

This skips the launch window and serves the packed files before anything is downloaded.

**Change Day** (*File > Change Day…*) closes the archive session and brings back the launch window, set to ARCHIVE on the session's date, without quitting STORM. The admin passphrase isn't asked again. Sounding, VAD and other windows from the session close with it. Closing the launch window then quits.

### Video Studio

*File > Video Studio* (**Ctrl+Shift+V**) makes a movie of the case. The studio takes the archive bar's place at the bottom of the window; **Esc** or **Close Studio** brings the bar back. The movie stays in the studio while STORM is open; **Save** keeps it as a project file (`data/studio/<date>/<name>.json` by default) that **Open…** reopens.

A movie is a row of **keyframes**. Each records the case clock, the map view (center, zoom, rotation, tilt) and the radar on screen (station, product, tilt). Between two keyframes the clock runs at a steady speed and the camera pans and zooms to the next view.

- **Case clock** strip (top): the whole session, with the radar scans as ticks and the keyframes as diamonds. Click or drag to set the clock; scroll for the next or previous radar scan (Shift: a minute). The box beside it takes a typed UTC time.
- **Add Keyframe** (**K**) records the clock and the map at the playhead: after the last keyframe at the end of the movie, in place of a keyframe when the playhead is on one, or splitting the move it's in.
- **Timeline**: one clip per move (case span, speed and camera easing), dashed boxes for pauses, the keyframes and the red playhead. Drag a keyframe to retime it (later keyframes follow; **Alt** moves it alone); click or drag elsewhere to scrub, and the map shows that moment; right-click for go to, update, duplicate, easing and delete. **Ctrl** + scroll zooms the timeline.
- **Inspector** (the selected keyframe): its case time, the radar it switches to (*Use current* takes the radar now on screen), *Retake* (the clock and map as they are now), and the move to the next keyframe: speed (60× = a minute of case per second of video; *Use for all* gives every move this speed), camera easing, and a pause before it. ½× / 2× and the movie length beside **Movie** stretch or squeeze the whole movie.
- **Play** previews the movie on the map (the map draws as fast as data arrives); *loop* repeats it.
- **Output**: MP4 (H.264), GIF (at most 960 px wide) or numbered PNG frames; the map's size, 720p, 1080p or 4K (rendered natively at that size, showing the same area); 24, 30 or 60 fps; the clock step (*smooth*, every minute, every 5 minutes or each radar scan: stepped movies hold the data between steps like a radar loop and export much faster); and captions (case time, the radar line, the legend).
- **Save Frame…** saves a PNG of the map once it has finished drawing, with the chosen captions. **Export Movie…** renders every frame complete: each frame waits for its radar scan (in its sharp render), satellite and trails before it is captured. A progress window shows the time left and can cancel.

Keys while the studio is open: **Space** play / pause, **K** add keyframe, **[** / **]** previous / next keyframe, **←** / **→** a frame (**Shift**: a second), **Home** / **End**, **Delete** the selected keyframe, **,** / **.** previous / next radar scan, **Ctrl+Z** / **Ctrl+Shift+Z** undo / redo. They replace the archive playback keys until the studio closes. A field changes on scroll only after it's clicked into.

---

## 9. Outlook Text Panel

The Outlook Panel is a right-side panel that expands horizontally to display the full text of SPC and NWS products.

**Opening the Panel:**
Click any SPC feature (outlook area, watch polygon, or MD polygon) on the map, then click "Read" or "Read Discussion" in the popup.

**What Appears:**
- **Title bar:** Product name (e.g., "DAY 1 CONVECTIVE OUTLOOK", "MD 0179", "TORNADO WATCH 0029")
- **Close button (×):** Collapses the panel
- **Text area:** Full monospaced text of the product (read-only, scrollable)

**Text Sources:**

| Product | Source |
|---------|--------|
| Day 1 Outlook | IEM AFOS (`pil=SWODY1`) |
| Mesoscale Discussions | SPC direct (`spc.noaa.gov/products/md/md{NNNN}.txt`) |
| Tornado Watches | IEM AFOS (`pil=SEL{n}` where n = watch_number % 10) |
| SVR Tstm Watches | IEM AFOS (same PIL logic as tornado) |
| NWS Warnings | NWS API JSON (`headline` + `description` + `instruction` fields) |

**Animation:** Expands in from the right edge (200 ms), collapses on close (200 ms). Panel is 340 px wide.

---

## 10. Point Soundings

STORM has two built-in sounding sources, HRRR and observed radiosondes, plus NSSL's with the NSSL provider and any your team adds in `storm.toml` ([Your Own Data Sources](#your-own-data-sources)). Pick one in the **SOUNDINGS** drawer (see [Section 6.4](#64-soundings)); they all display in the same Skew-T log-P dialog.

### HRRR Model Soundings

Click any map location within the HRRR CONUS domain. Data fetched from open-meteo (free tier, no API key required). The dialog opens automatically when data arrives (typically 1–3 seconds). Clicking a new location updates the same dialog in place.

### Observed Radiosonde Soundings

Switch to OBS mode in the SOUNDINGS drawer. Radiosonde station markers appear on the map at the US upper-air sites in IEM's RAOB network (80, including Alaska, Hawaii and Puerto Rico; Denver isn't in IEM's archive, and military test ranges are left out). Click a marker to fetch the most recent balloon launch profile from IEM RAOB. STORM automatically selects the 00Z or 12Z launch closest to the current UTC time.

### NSSL CLAMPS DL Truck Soundings

Switch to NSSL mode in the SOUNDINGS drawer (there with the NSSL provider and `NSSL_API_KEY`). CLAMPS truck markers appear when data is available from the NSSL API. Click a marker to fetch and display the most recent truck sounding. Up to 12 hours of soundings per truck are available.

### Dialog Layout

```
┌─────────────────────────────────────────────────────────────┐
│  HRRR  Init 18Z 19 Mar 2026  ·  Valid 18Z 19 Mar 2026 (F0) │ ─── [scrubber] ─
│  35.220°N  97.440°W  ·  308 m MSL                           │   F0  F+1  F+2  F+3
├─────────────────────────────────────────────────────────────┤
│                                                             │
│              Skew-T log-P (with hodograph inset)            │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  [Parcel table: SB / ML / MU × CAPE / CIN / LCL / LFC / EL]│
│  [Kinematics table: 0-500m / 0-1km / 0-3km / 0-6km × Shear / SRH / SRW]  │
├─────────────────────────────────────────────────────────────┤
│  LR 700-500  LR 0-3km  SFC θe  PW  Conv Temp  STP  SCP  EHI  0-3km CAPE│
└─────────────────────────────────────────────────────────────┘
```

### Header & Scrubber

The header shows the HRRR init time and the currently-displayed valid time. The scrubber on the right side steps through four time slots: **F0** (analysis), **F+1h**, **F+2h**, and **F+3h** from the most recent HRRR run. Drag the slider or click a tick label to switch hours. The active slot is highlighted in cyan.

### Skew-T diagram

| Element | Description |
|---------|-------------|
| Red curve | Temperature |
| Green curve | Dewpoint |
| Dashed warm-tint curve | Virtual temperature |
| White dashed curve | Surface-based parcel profile |
| Surface T/Td labels | Bold numeric labels at the base of the temperature (red) and dewpoint (green) traces showing surface values in the `[units]` temperature unit (°F by default) |
| Red shading | CAPE area |
| Blue shading | CIN area |
| Cyan circle | LCL marker |
| Blue shading (light) | Dendritic growth zone (−10 to −20°C) |
| Red dotted horizontal lines | AGL height reference lines (0.5, 1, 2, 3, 4, 6, 9 km) |
| Green bracket (left spine) | Effective inflow layer (EIL) |
| Wind barbs | Meteorological wind at each pressure level |

### Hodograph Inset

Located in the upper-right corner of the Skew-T. The trace is color-coded by height AGL:

| Color | Layer |
|-------|-------|
| Red | 0–3 km |
| Gold | 3–6 km |
| Blue | 6–9 km |
| Gray | Above 9 km |

A wide semi-transparent green overlay highlights the EIL segment. Bunkers right-mover (RM) and left-mover (LM) storm motion points are plotted as colored dots (red and blue respectively) with direction/speed readouts in the corner of the inset.

### Parcel Table

| Column | Description |
|--------|-------------|
| CAPE | Convective available potential energy (J/kg) |
| CIN | Convective inhibition (J/kg, negative values) |
| LCL | Lifted condensation level height (m AGL) |
| LFC | Level of free convection (m AGL) |
| EL | Equilibrium level (m AGL) |

Rows: **SB** (surface-based), **ML** (100 hPa mixed-layer), **MU** (most-unstable). Values are threshold-colored: yellow → orange → red as severity increases.

### Kinematics Table

| Row | Description |
|-----|-------------|
| 0–500m | Bulk shear, SRH, and mean SRW in the lowest 500 m |
| 0–1km | Bulk shear, SRH, and mean SRW in the lowest 1 km |
| 0–3km | Bulk shear, SRH, and mean SRW in the lowest 3 km |
| 0–6km | Bulk shear and mean SRW (SRH not shown for this layer) |

**SRW** (storm-relative wind) is the mean wind speed relative to the Bunkers right-mover in each layer. All shear values in knots, SRH in m²/s².

Storm motion is displayed as `RM  dir°/spd kt` and `LM  dir°/spd kt` in the hodograph corner.

### Bottom Scalar Row

| Parameter | Description |
|-----------|-------------|
| LR 700–500 | 700–500 hPa lapse rate (°C/km) |
| LR 0–3 km | 0–3 km AGL lapse rate (°C/km) |
| SFC θe | Surface equivalent potential temperature (K) |
| PW | Precipitable water (mm) |
| Conv Temp | Convective temperature — surface temperature required for convection initiation (°F, or °C with `[units]`) |
| STP | Significant Tornado Parameter |
| SCP | Supercell Composite Parameter |
| EHI | Energy-Helicity Index |
| 0-3km CAPE | CAPE integrated over the lowest 3 km AGL (J/kg) |

### Cursor Readout

Hovering over the Skew-T axes displays a live readout below the plot:
`pressure (hPa)  ·  T  ·  Td  ·  Wind dir°@spd kt  ·  height m MSL`

### Data Sources

**HRRR mode:**

| Item | Detail |
|------|--------|
| Model | NCEP HRRR CONUS (3 km, hourly updates) |
| API | open-meteo `/v1/forecast` — no API key required |
| Variables | Temperature, dewpoint, U/V wind components, geopotential height at 25 pressure levels (1000–100 hPa) |
| Request cost | 1 API call per map click |
| Rate limits (free tier) | 600/min · 10,000/day · 300,000/month |
| Fetch time | Typically 1–3 seconds |
| Domain | CONUS only — clicks outside HRRR coverage will return an error |

**OBS mode:**

| Item | Detail |
|------|--------|
| Source | IEM RAOB radiosonde archive |
| API | `mesonet.agron.iastate.edu/json/raob.py` |
| Launch times | 00Z and 12Z; STORM selects the most recent launch |
| Fetch time | Typically 2–5 seconds |

**NSSL mode:**

| Item | Detail |
|------|--------|
| Source | NSSL CLAMPS DL truck soundings via authenticated NSSL API |
| Index | `{NSSL_API_ROOT}/data/sonde/index.json` |
| Availability | Only when NSSL DL truck is actively collecting data |
| History | Up to 12 hours of soundings per truck available |

---

## 11. Vehicle Tracking & Observations

STORM tracks vehicles through several parallel data input channels. All inputs feed into the live vehicle state used by the vehicle panel, station plots, and map markers.

### Track A — Local File Watcher

Polls the logger's CSV file every second. Enabled only if a data directory was selected at launch.

**Which file:** the layout's `file` pattern in `[obs_columns]`: by default `*YYYYMMDD.csv` for today's UTC date (the shortest matching name when there are several). A pattern without `{date}` follows the most recently changed matching file instead.

**Which columns:** the layout's column names. By default:

| Column | Format | Description |
|--------|--------|-------------|
| `time` | ISO 8601 | Time (UTC when it has no zone) |
| `lat` | decimal degrees | Latitude |
| `lon` | decimal degrees | Longitude |
| `temperature_c` | °C | Temperature |
| `dewpoint_c` | °C | Dewpoint temperature |
| `wind_speed_ms` | m/s | Wind speed |
| `wind_dir_deg` | degrees | Wind direction |
| `pressure_mb` | hPa | Pressure |
| `heading_deg` | degrees | Which way the vehicle faces |

The NSSL profile's `fofs` layout reads the FOFS truck logger's `YYYYMMDD.txt` (`gps_date`, `gps_time`, `t_fast`, `dewpoint`, `sfc_wspd`, `sfc_wdir`, `pressure`, `compass_dir`).

### Track B — GPS Reader

Automatically detects and reads NMEA sentences from a connected serial GPS device. Provides position-only data (no met obs). Active only if `--disable-data-inputs` is not passed.

### MQTT — Inbound Vehicle Observations

Receives observations from other vehicles via the `<topic_root>/vehicles/{id}` topic (`storm/vehicles/{id}` by default). Active whenever MQTT is connected and not disabled. Any logger or script can appear as a vehicle by publishing the payload in [`docs/wire-format.md`](docs/wire-format.md); the headless publisher (`headless/`) does this from a logger file on a Raspberry Pi.

## 12. Network & MQTT Sync

STORM uses an MQTT v5 broker, set in `storm.toml` (see [Section 15](#15-configuration--certificates)), to synchronize vehicle observations, annotations, drawings, cones and scan sectors across all connected vehicles in the network. Setting up a broker (Mosquitto or a hosted service), who can see what, and fixing connection problems are in [`docs/broker.md`](docs/broker.md).

### Topics

Every topic starts with `topic_root` from `storm.toml`'s `[mqtt]` table, `storm` unless your team changed it. Everyone on the team must use the same root; a client with a different one sees none of the others.

| Topic | Content |
|-------|---------|
| `<topic_root>/vehicles/{id}` | Vehicle observations (lat, lon, temp, dewpoint, wind, pressure) |
| `<topic_root>/annotations/{id}` | Road condition annotations (create / edit / delete) |
| `<topic_root>/drawings/{id}` | Meteorological drawings (fronts, polylines, polygons) |
| `<topic_root>/cones/{id}` | Storm motion cones |
| `<topic_root>/scan_sectors/{id}` | Each vehicle's current scan sector |

Every payload, with its fields, units, QoS, retain flag and expiry, is in [`docs/wire-format.md`](docs/wire-format.md).

### Behavior

- Annotations, drawings, cones and scan sectors are retained on the broker, deletes included, so on connect (and on every reconnect) the current ones arrive at once and anything deleted while you were away disappears. This needs a broker that sends retained messages on wildcard subscriptions, as Mosquitto, EMQX and HiveMQ do. AWS IoT Core doesn't: on NSSL's broker scan sectors still arrive (each one within a few seconds, once its vehicle publishes), but annotations, drawings and cones from before you connected don't. A setup with `[mqtt] shared_items_url` loads those from that URL at launch instead (NSSL's profile: the NSSL API's `current.json`), and the **↻ Refresh** button in the annotation drawer re-fetches it (the button only shows with it set). See [`docs/wire-format.md`](docs/wire-format.md#retained-messages).
- Annotations, drawings and scan sectors expire from the broker at the team's daily reset (`daily_reset_utc` in `storm.toml`, 08:00 UTC unless changed), cones after an hour, vehicle positions after three hours.
- STORM was tested end to end against a HiveMQ broker only. Other brokers that meet the requirements in [`docs/broker.md`](docs/broker.md) should work with the right settings, but haven't been tried.
- Disconnect/reconnect is handled automatically — STORM continuously attempts to reconnect with exponential backoff.
- If `--disable-mqtt` is passed, all sync features are disabled and the vehicle panel shows only locally-observed vehicles.
- In MONITOR mode, **File > Record Broker Traffic** writes everything the broker sends to `recordings/` for ARCHIVE to replay; `python -m archive.recorder` does the same without the GUI. See [Recorded Team Traffic](#recorded-team-traffic).
- The status pill shows **● MQTT OK**, **● MQTT OFFLINE** or **● NO BROKER**; while offline the status bar gives the reason (for example "not authorized").

---

## 13. Data Sources & Polling Intervals

| Data Source | Polling Interval | Notes |
|-------------|-----------------|-------|
| NEXRAD Radar (Unidata THREDDS) | Every 2 minutes | Level 3 products; no auth required; Level 2 in archive mode |
| SPC Categorical Outlook | Every 15 minutes | Day 1 only |
| SPC Tornado / Wind / Hail Probabilities | Every 15 minutes | All fetched together with categorical |
| SPC Watches | Every 2 minutes | County polygons, tornado + SVR tstm |
| SPC Mesoscale Discussions | Every 2 minutes | MD polygon geometry + number |
| NWS Warnings | Every 2 minutes | All active VTEC phenomenons |
| GOES Satellite (CONUS) | Every 5 minutes | Full CONUS, up to 10 frames |
| GOES Satellite (MESO-1/2) | Every 1 minute | When active, per-sector |
| Offline NLCD Land Cover | Local MBTiles | Optional `tiles/storm_nlcd.mbtiles`; no network polling |
| Offline USGS Satellite Basemap | Local MBTiles | Optional `tiles/satellite.mbtiles`; no network polling |
| SFCOA Mesoanalysis | On demand | NSSL API index refresh plus selected vector-tile products |
| HRRR Point Sounding (open-meteo) | On demand | 1 API call per map click |
| Observed Radiosonde (IEM RAOB) | On demand | Nearest 00Z / 12Z launch |
| NSSL CLAMPS DL Truck | On demand | Fetched from the NSSL API on click |
| Your station networks (`[[sources]]`; the NSSL profile's OK / WTM / KS / CO / NE / SD) | Every `refresh_min` (5 minutes unless set) | Per-network, independent toggles |
| ASOS/AWOS Surface Obs (IEM) | Every 5 minutes after bbox selection | User-drawn bbox; capped for performance |
| Turn-by-turn Routing (openrouteservice) | On demand | Re-fetched automatically if off-route |
| Local Obs File (Track A) | Every second | Today's logger file (`[obs_columns]` `file`) |
| GPS (Track B) | Real-time / continuous | NMEA sentences |
| MQTT Inbound | Real-time | Vehicle obs, annotations, drawings, cones |
| Network Health Check | Every 30 seconds | Cloudflare 1.1.1.1 ping |

---

## 14. Command-Line Options

Run `python main.py --help` for the full list. Key options:

### General

```
--debug                        Enable debug logging and the debug panel (Ctrl+D)
--log-level LEVEL              Logging verbosity: DEBUG, INFO, WARNING, ERROR (default: WARNING)
--case PACKAGE.zip             Open a case package in ARCHIVE, skipping the launch window
--perf-log                     Write timings, memory and responsiveness to perf_logs/ (also STORM_PERF_LOG=1)
```

> **Note:** Mode selection (VEHICLE, MONITOR, VIEWER, ARCHIVE) is handled exclusively through the launch dialog, which checks for a broker login (and the admin passphrase). There are no command-line flags for mode selection.

### Disable Features

```
--disable-radar                Hide the RADAR control; disable all radar fetching
--disable-mqtt                 Disable all MQTT features (sync, vehicle obs)
--disable-annotations          Disable annotations, drawings, station plots, and cones
--disable-deploy-locs          Disable deployment location markers
--disable-data-inputs          Disable local file watcher and GPS reader
```

### Replay Mode

```
--truck-replay-file PATH        Replay a local obs CSV/TXT file instead of live data
--truck-replay-interval-ms MS   Interval between replayed samples (default: 1000 ms)
--truck-replay-restamp          Shift timestamps so the last obs lands at "now"
```

### Performance

```
--render-grid-size N            Radar rendering grid: 128, 256, 512, 768, 1024, or 1536
--lite                          Lite mode: lower radar resolution, fewer surface plots, slower loops
--gpu-radar                     Experimental: draw live reflectivity with WebGL (off in safe/lite mode)
--webengine-flags FLAGS         Extra Chromium flags for the map, e.g. "--disable-gpu-compositing"
```

### Debug Run Profiles

```
--debug-run N                  Quick diagnostic profile (0–6):
                                0 = full features
                                1 = disable MQTT only
                                2 = safe mode (minimal features, low grid size)
                                3–6 = various feature subsets for testing
--enable-startup-toggles        Allow CLI disable flags to override the debug-run profile
```

### Network

```
--mqtt-no-tls                  Connect as if [mqtt] tls = "none" (diagnostics only, not for field use)
```

### Recorder

`python -m archive.recorder` records broker traffic for ARCHIVE without opening STORM (see [Recorded Team Traffic](#recorded-team-traffic)):

```
--folder FOLDER                Write here instead of [archive] recordings (default recordings/)
-v, --verbose                  Debug logging
```

---

## 15. Configuration & Certificates

### storm.toml

Per-install settings live in `storm.toml` in the STORM folder (or the file `STORM_CONFIG` names). Copy `storm.example.toml`, or a profile such as `profiles/nssl.toml`. Relative paths are resolved against the STORM folder. A missing file means no broker. Problems are listed together in one window at startup, and STORM uses the rest of the file: an unknown table or key is skipped, with a suggestion for a near miss ("did you mean [units]?"); a table with an invalid value keeps its defaults (a bad `[mqtt]` means no broker, nothing more); a bad `[[sources]]`, `[[auth]]` or `[providers.<name>]` entry is left out. A file that isn't valid TOML is ignored as a whole. Details in [`docs/configuration.md`](docs/configuration.md#when-something-in-the-file-is-wrong). Every key is in [`docs/configuration.md`](docs/configuration.md).

**`[mqtt]`** — the broker:

| Key | Description |
|-----|-------------|
| `host`, `port` | Broker address (port defaults to 8883, or 1883 without TLS) |
| `tls` | `"none"`, `"server"` (default: TLS, broker verified) or `"mutual"` (TLS plus a client certificate, as AWS IoT Core needs) |
| `ca_cert` | CA bundle for a privately signed broker; system trust store if left out |
| `cert_file`, `key_file` | Client certificate and key, for `tls = "mutual"` |
| `username` | Broker login; the password goes in `.env` as `STORM_MQTT_PASSWORD` |
| `client_id_prefix` | Put in front of the client ID (the vehicle ID) |
| `read_only` | Never publish; VEHICLE and MONITOR won't launch with it |
| `topic_root` | First level of every topic (default `"storm"`); the same for everyone on the team. See [Section 12](#12-network--mqtt-sync) |
| `daily_reset_utc` | Hour (UTC) when shared annotations, drawings and scan sectors expire (default `8`) |

**`[mqtt.viewer]`** — optional read-only login for VIEWER mode. Keys left out come from `[mqtt]`; its password is `STORM_MQTT_VIEWER_PASSWORD`. Without it, VIEWER uses the `[mqtt]` login and never publishes. It always uses `[mqtt]`'s `topic_root` and `daily_reset_utc`.

**`[providers.nssl]`** — NSSL's provider is built in: without the table ARCHIVE's NSSL button is there for everyone. `enabled = false` leaves it out; `team = true` (the NSSL profile) makes NSSL the setup's own team ([Data Providers](#data-providers)). Its live API layers also need `NSSL_API_KEY` in `.env`.

**`[[sources]]`** — your own station networks, map overlays and soundings, one table each ([Your Own Data Sources](#your-own-data-sources); every key in [`docs/data-sources.md`](docs/data-sources.md)).

**`[providers.<name>]`** — a code provider of your team's own (NSSL's is one too): `enabled = true`, an optional `path`, and its own options ([`docs/providers.md`](docs/providers.md)).

**`[archive]`** — where ARCHIVE reads recorded broker traffic (`recordings`, `history`, `history_prefix`) and vehicle obs (`vehicle_obs`, `vehicle_obs_path`), and the case finder's `[[archive.campaigns]]` (`name`, `years`). See [Recorded Team Traffic](#recorded-team-traffic) and [Your Own Archive Data](#your-own-archive-data).

**`[obs_columns]`** — which file in a vehicle logger's folder is today's, and which of its CSV columns hold what, for live obs files and ARCHIVE's vehicle-obs files. Defaults read a plain CSV named after the UTC day; `""` means the file has no such column.

| Key | Default | Description |
|-----|---------|-------------|
| `file` | `*{date}.csv` | Today's file: a file-name pattern, `{date}` the UTC day as YYYYMMDD; without `{date}`, the newest matching file |
| `lat`, `lon` | `lat`, `lon` | Position (decimal degrees) |
| `timestamp` | `time` | One ISO 8601 column (UTC when it has no zone) |
| `date`, `time` | `""` | Or UTC date and time columns, read with `date_format` / `time_format` |
| `date_format`, `time_format` | `%Y%m%d`, `%H%M%S` | Python `strptime` formats |
| `temperature_c`, `dewpoint_c` | `temperature_c`, `dewpoint_c` | °C |
| `wind_speed_ms`, `wind_dir_deg` | `wind_speed_ms`, `wind_dir_deg` | m/s; degrees, from |
| `pressure_mb` | `pressure_mb` | hPa |
| `heading_deg` | `heading_deg` | Which way the vehicle faces |
| `label` | the layout's name | Its name in the launch window |

Columns in other units are converted with `[obs_columns.units]` (for example `temperature_c = "F"`, `wind_speed_ms = "kt"`). Teams with more than one kind of logger name a layout for each (`[obs_columns.<name>]`) and pick one in the launch window's **Logger File** list; ARCHIVE reads vehicle-obs files with the first. The NSSL profile has `fofs` and `gps_ka`. The headless publisher reads the same table and picks a layout with `layout`. Every key: [`docs/configuration.md`](docs/configuration.md#obs_columns-vehicle-logger-columns).

**`[map]`** — `home_lat`, `home_lon` and `zoom`: where the map opens, and the position STORM falls back to while your vehicle has none yet (ARCHIVE's first radar site, a new scan sector). Default: the middle of the contiguous US at zoom 4; the NSSL profile sets Norman, OK at zoom 6. `time_zones`: the local clocks in ARCHIVE's bar (IANA names such as `"Europe/Berlin"`, up to three); default, this computer's zone.

**`[units]`** — `temperature` (`"F"` or `"C"`) and `distance` (`"mi"` or `"km"`): how STORM shows temperatures (station plots, vehicle panel and timeseries, the sounding viewer's surface labels and convective temperature) and distances (measuring tool, routing, damage paths, pin and radar-site distances). Defaults °F and miles. Wind is always in knots. The °F and miles throughout this guide are the defaults.

**`[archive]` session window** — `session_start_utc`, `session_hours`, `session_activity_after_hours`: where ARCHIVE's session starts and ends ([Archive Mode](#8-archive-mode)).

### .env

Secrets, one `KEY=VALUE` per line (copy `.env.example`): `STORM_MQTT_PASSWORD`, `STORM_MQTT_VIEWER_PASSWORD`, `NSSL_API_KEY`, `ORS_API_KEY`, `STORM_ADMIN_PASSPHRASE_HASH`, and the keys your `[[sources]]` and `[[auth]]` tables name with `api_key_env`. A real environment variable of the same name wins.

### config.py

`config.py` holds the few constants that aren't per-team settings (those are in `storm.toml`, `docs/configuration.md`):

| Constant | Description |
|----------|-------------|
| `ACCENT_COLOR` | UI accent color (default: `"#00CFFF"`, cyan) |
| `VEHICLE_ID`, `VEHICLE_ICON` | Defaults for the launch window's vehicle ID and icon (overridden there) |
| `OBS_FILE_DIR`, `OBS_FILE_LAYOUT` | Defaults for the logger folder and its `[obs_columns]` layout (overridden by the launch window); the file's columns are `[obs_columns]` |
| `OBS_FILE_POLL_S` | How often the logger file is read for the local display (default 1 s) |
| `OBS_MQTT_PUBLISH_S` | How often your vehicle's obs are published for the others (default 5 s) |
| `GPS_BAUD` | Serial baud rate for a GPS receiver (default 4800) |
| `LAYER_AGE_THRESHOLDS_MIN` | Per layer, the data age (minutes) at which its time label turns amber and its toolbar button gets an amber dot |
| `ADMIN_PASSPHRASE_HASH`, `ORS_API_KEY` | Read from `STORM_ADMIN_PASSPHRASE_HASH` and `ORS_API_KEY` in the environment or `.env`; never stored in the file |
| `HOME_LAT`, `HOME_LON` | The map home from `[map]` in `storm.toml` |

### Persistent Session State (QSettings)

The following are saved automatically and restored at next launch:
- Window geometry and dock layout
- Last-used vehicle ID
- Last-used data directory

Settings are stored under the `"STORM" / "STORM"` namespace in your OS's native settings storage. Settings saved under the old `"NSSL" / "STORM"` namespace are copied over once, the first time this version starts.

### NSSL Certificate Files

With `profiles/nssl.toml`, VEHICLE and MONITOR need the team certificates (handed out by the STORM team) in `providers/nssl/certs/team/`; VIEWER uses the read-only certificate shipped in `providers/nssl/certs/viewer/`. A missing file stops VEHICLE and MONITOR at the launch window; everything else works. The file list, and moving certificates from an older install's `aws/` folder, are in [`docs/nssl.md`](docs/nssl.md#3-certificates). Your own broker's files can go in a top-level `certs/`, which is git-ignored.

---

## 16. Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Escape** | Cancel active drawing / annotation placement / measurement mode |
| **Ctrl+D** | Toggle debug panel |
| **Ctrl+E** | Toggle error log panel |
| **Ctrl+K** | Command palette |
| **Ctrl+S** | Save map screenshot |
| **Ctrl+O** / **Ctrl+Shift+E** | Open / export a case package (archive mode) |
| **Ctrl+Z** / **Ctrl+Shift+Z** | Undo / redo a storm track edit (archive mode; studio edits while the Video Studio is open) |
| **Ctrl+Shift+V** | Open / close the Video Studio (archive mode); its own keys are listed under [Video Studio](#video-studio) |
| **T** | Storm track drawer (archive mode); **P** point at the marker, **Delete** remove the selected point |
| **Space** | Play / pause the radar loop (live) or archive playback |
| **[** / **]** | Previous / next radar frame (live); step back / forward (archive, also **←** / **→**, **A** / **D**) |
| **Home** / **End** | Jump to the start / end of the archive session |
| **?** | Show / hide the shortcut list |
| **Right-click** | Map menu |
| **Double-click** | Finish a drawing in progress |

---

## 17. Performance Tuning

### Radar Grid Size

The radar rendering grid controls the sharpness and rendering time of the NEXRAD overlay.

| Grid Size | Render Time (approx.) | Recommended Use |
|-----------|----------------------|-----------------|
| 128 | ~5–10 ms | Very slow hardware, emergency fallback |
| 256 | ~20–40 ms | Older laptops, safe-mode deployments |
| 512 | ~80–150 ms | Balanced quality/speed |
| **768** | **~200–400 ms** | **Default starting point in dynamic mode** |
| 1024 | ~400–800 ms | Workstation-class machines only |
| 1536 | ~900–1800 ms | Maximum sharpness, very fast machines only |

Pass `--render-grid-size N` to override. Lower grid sizes make the radar appear blockier but keep the CPU free for other tasks.

### Memory Usage

| Component | Approximate Memory |
|-----------|--------------------|
| Radar frame cache (12 frames) | 20–50 MB |
| CONUS satellite cache (10 frames) | 10–20 MB |
| MESO satellite cache (10 frames/sector) | 20–30 MB per sector |
| Station plot image cache | ~1–5 MB (per vehicle) |
| Observation history buffer (10 min/vehicle) | Negligible |

### Safe Mode

If the application is unstable on older hardware (WebGL context loss, slow renders), launch with:
```bash
python main.py --debug-run 2
```
Safe mode reduces grid size to 128, disables some overlays, and minimizes background polling.

---

## 18. Diagnostics & Error Handling

### Debug Mode

```bash
python main.py --debug
```

Enables:
- Verbose logging to console (DEBUG level)
- The debug panel (also toggled with **Ctrl+D** at any time)
- Fault handler writing stack traces to `storm_fault.log` on crash

### Error Log Panel

Press **Ctrl+E** at any time to open the error log panel. It shows all WARNING-level and above log messages captured since launch. This log is also written to `storm_errors.log` and survives across restarts.

### Network Status Indicator

The colored pill in the bottom-right of the status bar updates every 30 seconds:
- **Green (Online):** Connectivity to Cloudflare 1.1.1.1 is fast
- **Yellow (Slow):** Response time exceeded 1 second — data fetches may be delayed
- **Red (Offline):** No connectivity — no real-time data will update

### Single-Instance Guard

STORM uses an internal TCP port (19876) as a process lock. If a second instance is launched while one is already running, a dialog warns the user and the second instance exits.

### Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|-----------|
| Radar not updating | THREDDS server unavailable or slow | Check network indicator; try a different site |
| **● NO BROKER** pill | No `storm.toml`, or no `[mqtt] host` in it | Copy `storm.example.toml` or a profile to `storm.toml` and set the broker |
| **● MQTT OFFLINE** pill | Broker unreachable, or the login was refused (the status bar gives the reason) | Check `[mqtt]` in `storm.toml`, the certificate files it names, and `STORM_MQTT_PASSWORD` in `.env` |
| Station plots missing | `--disable-annotations` passed | Relaunch without that flag |
| MESO-1/2 buttons grayed out | Sectors not currently available | Wait — buttons enable automatically when sectors are active |
| Map tiles blank/gray | `storm.mbtiles` missing or corrupted | Verify the MBTiles file is present and not zero-byte |
| Slow/choppy radar rendering | Grid size too large for hardware | Use `--render-grid-size 256` |
| Other broker connection problems (`not authorized`, two laptops dropping each other, late joiners missing items) | Login, access list, `topic_root` or a duplicate vehicle ID | See [`docs/broker.md`](docs/broker.md#when-it-doesnt-connect) |
| `make_base_mbtiles.py` says Java not found | No Java 21 | `conda env create -f envs/tiles.yml`, then run the script after `conda activate tiles` |
| Hatch patterns not visible | WebGL limitation | Known quirk; patterns are built as pixel data, not canvas — should render in all supported modes |

---

## 19. Known Limitations & Quirks

**Canvas Readback**
MapLibre GL running inside QWebEngineView cannot reliably read back canvas pixel data. Hatch patterns (CIG/SIGN significant areas) are therefore built entirely as raw `Uint8Array` pixel data and added to MapLibre via `addImage()`, rather than drawn on a canvas. This is a known architectural constraint.

**QWebChannel Async Initialization**
The `bridge` object that connects Python and JavaScript is initialized asynchronously after the map loads. All JavaScript API functions are stubbed as no-ops (`_stormNoop`) until the bridge is ready. In practice this is transparent to the user, but very rapid actions in the first second after launch may be silently dropped.

**MBTiles Files Not Included in Repository**
The offline vector tile database (`storm.mbtiles`) is large (about 3.7 GB) and is not included in the git repository. The shipped files cover the central US only (116°W–82°W, 28°N–49°N); `scripts/make_base_mbtiles.py` (vector base map, anywhere) and `scripts/make_satellite_mbtiles.py --bbox` (USGS imagery, US only) build your own. Optional NLCD and satellite-basemap caches are also distributed outside git. Place MBTiles files in the `tiles/` folder.

**SPC Text Product Latency**
IEM AFOS sometimes responds slowly (up to 20 seconds per request). STORM waits patiently rather than timing out aggressively. This is normal behavior during high-traffic SPC issuance periods.

**MESO Sectors Availability**
GOES MESO-1 and MESO-2 sectors are not always active or available over your area of interest. STORM automatically enables/disables the MESO buttons based on sector availability. This is a function of NOAA's operational GOES scanning schedule.

**GOES Satellite Band**
The IEM WMS endpoint serves the default operational visible or IR band for the current GOES satellite. Band selection is not user-configurable from within STORM.

**SPC Data Lag**
SPC GeoJSON products (tor, wind, hail) are typically updated once or twice daily. They may not reflect very recent issuances until the next polling cycle completes (up to 15 minutes after the fact). SPC Watches and MDs poll every 2 minutes and are effectively near-real-time.

---

## 20. Feature Availability Matrix

"Needs" is what the feature requires beyond STORM itself: **NSSL** is the NSSL provider (built in; `[providers.nssl] enabled = false` removes it), **NSSL + key** adds `NSSL_API_KEY`, **NSSL profile + key** is the `[[sources]]` in `profiles/nssl.toml` with `NSSL_API_KEY`. See [Data Providers](#data-providers).

| Feature | Default | Needs | Disabled By |
|---------|---------|-------|-------------|
| Offline vector base map | ✅ Always | — | n/a (requires storm.mbtiles) |
| Offline NLCD land cover | ✅ Enabled when installed | — | Requires `tiles/storm_nlcd.mbtiles` |
| Offline USGS satellite basemap | ✅ Enabled when installed | — | Requires `tiles/satellite.mbtiles` |
| NEXRAD Radar overlay | ✅ Enabled | — | `--disable-radar` |
| GOES Satellite overlay | ✅ Enabled | — | n/a |
| SPC Hazards (outlook, tor, wind, hail) | ✅ Enabled | — | n/a |
| SPC Watches | ✅ Enabled | — | n/a |
| SPC Mesoscale Discussions | ✅ Enabled | — | n/a |
| NWS Warnings | ✅ Enabled | — | n/a |
| Outlook Text Panel | ✅ Enabled | — | n/a |
| HRRR Point Soundings | ✅ Enabled | — | n/a |
| Observed Radiosonde Soundings | ✅ Enabled | — | n/a |
| NSSL CLAMPS Truck Soundings | ✅ Enabled (when data available) | NSSL + key | n/a |
| Surface Obs: ASOS | ✅ Enabled (when toggled on) | — | n/a |
| Surface Obs: OK / WTM / KS / CO / NE / SD mesonets | ✅ Enabled (when toggled on) | NSSL profile + key | n/a |
| Surface Obs: your station networks | ✅ Enabled (when toggled on, live modes) | `[[sources]]`, and its key if it has one | n/a |
| Map overlays (GeoJSON) | ✅ Enabled (when toggled on, live modes; ARCHIVE too with `archive = true`) | `[[sources]]`, and its key if it has one | n/a |
| Your sounding sources | ✅ Enabled (when picked, live modes) | `[[sources]]`, and its key if it has one | n/a |
| Code provider data | ✅ Enabled (as its sources say) | `[providers.<name>]` and its package | n/a |
| SFCOA Mesoanalysis | ✅ Enabled | NSSL profile + key | n/a |
| Your contours source | ✅ Enabled (live modes) | `[[sources]]` with `kind = "contours"`, and its key if it has one | n/a |
| Day's shared items at launch (`[mqtt] shared_items_url`; NSSL: `current.json`) | ✅ Enabled | `shared_items_url` (NSSL: + key) | `--disable-annotations` |
| Turn-by-turn Routing | ✅ Enabled | `ORS_API_KEY` | n/a |
| Annotations (road conditions) | ✅ Enabled | a broker | `--disable-annotations` |
| Drawings (fronts, polylines, polygons) | ✅ Enabled | a broker | `--disable-annotations` |
| Storm Motion Cone | ✅ Enabled | a broker | `--disable-annotations` |
| Station Plots (vehicle obs) | ✅ Enabled | — | `--disable-annotations` |
| Measurement Tool | ✅ Enabled | — | n/a |
| Recording broker traffic (MONITOR, `python -m archive.recorder`) | Off until checked | a broker | n/a |
| Deployment Locations | ✅ Enabled (live modes) | NSSL | `--disable-deploy-locs` or missing data file |
| Radar Playback (loop mode) | ✅ Enabled | — | `--disable-radar` |
| Satellite Playback (loop mode) | ✅ Enabled | — | n/a |
| Archive Mode (session replay) | ✅ Enabled | — | Selected at launch; needs no broker |
| Archive Radar Playback (Level 2/3) | ✅ Enabled in archive mode | — | n/a |
| Archive Satellite Playback | ✅ Enabled in archive mode | — | n/a |
| Archive Hazard Products, ASOS, Damage Paths | ✅ Enabled in archive mode | — | n/a |
| Archive team traffic (recorded vehicles, annotations, drawings, cones, scan sectors) | ✅ Enabled in archive mode | a recording, or NSSL | n/a |
| Archive one-second vehicle obs: your logger files | ✅ Enabled in archive mode | `[archive] vehicle_obs` | n/a |
| Archive one-second vehicle obs: FOFS | ✅ Enabled in archive mode (NSSL > VEHICLES) | — (built in) | n/a |
| Archive Soundings: OBS | ✅ On demand in archive mode | — | n/a |
| Archive NSSL radiosondes, TROPoe | ✅ On demand in archive mode (NSSL button) | — (built in) | n/a |
| Archive NSSL vehicles, CLAMPS trailers, lidar winds, lidar scans and stares | ✅ Enabled in archive mode (NSSL button) | — (built in) | n/a |
| Vehicle Timeseries Plots | ✅ Enabled (for vehicles with obs history) | — | n/a |

---

*STORM — Severe Thunderstorm Observation and Reconnaissance Monitor*
*Built for NSSL field operations; usable by any team with an MQTT broker*
