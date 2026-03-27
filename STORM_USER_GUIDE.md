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
   - 6.6 [Routing](#66-routing)
   - 6.7 [Annotations](#67-annotations)
   - 6.8 [Drawings](#68-drawings)
   - 6.9 [Storm Motion Cone](#69-storm-motion-cone)
   - 6.10 [Measurement Tool](#610-measurement-tool)
   - 6.11 [Station Plots](#611-station-plots)
   - 6.12 [Deployment Locations](#612-deployment-locations)
   - 6.13 [Vehicle Panel](#613-vehicle-panel)
7. [Status Bar](#7-status-bar)
8. [Outlook Text Panel](#8-outlook-text-panel)
9. [Point Soundings](#9-point-soundings)
10. [Vehicle Tracking & Observations](#10-vehicle-tracking--observations)
11. [Network & MQTT Sync](#11-network--mqtt-sync)
12. [Data Sources & Polling Intervals](#12-data-sources--polling-intervals)
13. [Command-Line Options](#13-command-line-options)
14. [Configuration & Certificates](#14-configuration--certificates)
15. [Keyboard Shortcuts](#15-keyboard-shortcuts)
16. [Performance Tuning](#16-performance-tuning)
17. [Diagnostics & Error Handling](#17-diagnostics--error-handling)
18. [Known Limitations & Quirks](#18-known-limitations--quirks)
19. [Feature Availability Matrix](#19-feature-availability-matrix)

---

## 1. Overview

STORM is a standalone desktop application purpose-built for severe weather storm chasing situational awareness. It integrates offline vector maps, real-time NEXRAD radar, GOES satellite imagery, SPC/NWS hazard products, collaborative field annotations, meteorological drawings, and live multi-vehicle tracking — all in a single interface designed to function reliably in the field.

**Core Technology Stack:**

| Component | Technology |
|-----------|-----------|
| UI Framework | PyQt6 (QMainWindow + QWebEngineView) |
| Map Renderer | MapLibre GL JS (embedded in browser engine) |
| Vector Tiles | Offline MBTiles served via custom `storm://` URL scheme |
| Radar Decoding | MetPy + matplotlib (Level 3, Unidata THREDDS) |
| Satellite Imagery | IEM WMS (GOES-East, GOES-West) |
| Hazard Data | SPC GeoJSON MapServer + NWS API |
| Vehicle/Annotation Sync | MQTT over AWS IoT (TLS) |
| Python-to-JS Bridge | QWebChannel (`bridge` object) |

**Run Command:**
```bash
python main.py
```

---

## 2. Installation & Requirements

### Prerequisites
- Python 3.10+
- PyQt6 with WebEngine (`pip install PyQt6 PyQt6-WebEngine`)
- MetPy, matplotlib, numpy, scipy (`pip install metpy matplotlib numpy scipy`)
- paho-mqtt (`pip install paho-mqtt`)
- The offline MBTiles vector tile database (`storm.mbtiles`, ~500 MB–1 GB, distributed separately)

### MQTT Certificates
Certain network features (vehicle sync, annotation/drawing sync) require AWS IoT TLS certificates. Place them at:
- `/aws/storm.pem` — CA certificate
- `/aws/storm.pem.crt` — device certificate
- `/aws/storm-private.pem.key` — private key

If certificates are missing, STORM starts in a degraded state — map, radar, satellite, and SPC/NWS hazard features remain fully functional, but MQTT-dependent sync features are disabled.

### Single-Instance Guard
Only one instance of STORM can run per machine (an internal port is used as a lock). If a second launch is attempted, a warning dialog appears and the second instance exits immediately.

---

## 3. Launching STORM

### Launch Dialog

Every time STORM starts (unless `--monitor` mode is passed on the command line), a configuration dialog appears before the main window opens.

#### Launch Mode

Choose one of three mutually exclusive roles for this session:

| Mode | Description |
|------|-------------|
| **VEHICLE** | Full participant — publishes local obs (file watcher / GPS) and syncs annotations, drawings, and cones over MQTT. Requires a vehicle passphrase. |
| **MONITOR** | View-only participant — all map overlays and MQTT inbound work, but no local obs are published. Requires a monitor passphrase. |
| **VIEWER** | Fully offline / no-network mode — no MQTT, no obs publishing. Map, radar, satellite, and SPC/NWS hazards still function. |

#### Vehicle Config (VEHICLE mode only)

**Vehicle ID**
Your vehicle's identifier on the network (e.g., "Chase1", "Mesonet-TX"). Pre-filled from the previous session; edit freely. The ID is published with every observation, annotation, and drawing you send to other vehicles.

**Vehicle Icon**
Choose the icon that represents your vehicle on the map. Options: car, drone, mesonet, lidar, radar, hailcam.

**Data Directory (Track A)**
Optional folder path for a local FOFS instrument logger. If left blank, file-watcher input is disabled for the session. Use **Browse** to pick a folder. The app scans for `YYYYMMDD.txt` and polls it every 10 seconds.

#### Data on Launch (collapsible)

A collapsible section lets you pre-select which data layers to enable automatically on startup:

- **SPC / NWS / RADAR** — multi-select toggle buttons to auto-enable those overlays
- **Satellite** — exclusive selector: OFF | CONUS | AUTO-MESO
- **Surface obs** — multi-select: OK MESONET | WTM | KS MESONET

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
| **Outlook Panel** | Left-side slide-out — SPC/NWS text discussion viewer |
| **Status Bar** | Bottom strip — mouse coordinates, zoom, status messages, clock, network indicator |

---

## 5. Map Navigation

### Mouse Controls

| Action | Result |
|--------|--------|
| **Scroll wheel** | Zoom in / zoom out |
| **Click + drag** | Pan the map |
| **Click a feature** | Open info popup (SPC outlook, watch, warning, annotation, etc.) |
| **Right-click** | Finish current drawing or measurement |
| **Hover over feature** | Show tooltip |

### Map Layers (Render Order, bottom to top)

1. Offline vector base map (terrain, streets, water bodies, labels)
2. SPC categorical outlook polygons (MRGL / SLGHT / ENH / MDT / HIGH)
3. SPC probabilistic outlook polygons (tornado / wind / hail probability shading + hatching)
4. NWS watches (county-polygon outlines)
5. SPC Mesoscale Discussions (polygon outline + numeric label)
6. NWS warnings (storm-polygon fills, phenom-specific colors)
7. GOES satellite imagery (optional, opacity-controlled)
8. NEXRAD radar overlay (semi-transparent PNG raster)
9. Surface obs station model circles (mesonet obs)
10. Station plot icons (MetPy-style, at vehicle positions)
11. Vehicle markers (colored dots + info popups)
12. Annotations (road closure / construction / flooding / downed-lines / debris markers)
13. Drawings (fronts, polylines, polygons)
14. Storm motion cones
15. Routing line (active route geometry)
16. Measurement tool geometry
17. Deployment location markers

---

## 6. Floating Toolbar

The floating toolbar lives in the top-left corner of the map window. Each button either toggles a layer directly or expands a collapsible drawer with additional controls. Drawers animate open/closed smoothly.

---

### 6.1 Radar

**Button:** `RADAR` (checkable toggle)

Clicking the button expands the radar drawer. Checking the checkbox inside enables radar fetching and display.

#### Drawer Controls

**Site Selector (dropdown)**
Choose from 31 pre-configured NEXRAD sites (e.g., KTLX, KOUN, KVNX, KFWS, etc.) or type a custom 4-letter site code. Changing the site immediately clears cached frames and begins fetching for the new site.

**Product Selector (dropdown)**

| Option | NEXRAD Product | Description |
|--------|---------------|-------------|
| REFLECTIVITY (SR) | N0B (fallback: N0Q → N0R) | Base reflectivity (dBZ) |
| VELOCITY | N0U | Base velocity (kt) |

**Show Data Checkbox**
Enables or disables radar fetching. When unchecked, radar is hidden from the map but site/product selections are preserved.

#### Playback Controls

| Button | Action |
|--------|--------|
| ⏮ | Jump to oldest cached frame |
| ⏪ | Step back one frame |
| ▶ / ⏸ | Start / pause auto-playback loop (500 ms per frame) |
| ⏩ | Step forward one frame |
| ⏭ | Jump to most recent (live) frame |
| Timeline slider | Scrub to any cached frame by dragging |
| Time label | Shows current frame's UTC time (HH:MMZ) |

#### How Radar Data Works

- **Source:** Unidata THREDDS Level 3 catalog (public, no authentication required)
- **Polling:** Every 2 minutes
- **Frame cache:** Up to 12 frames per product
- **First fetch:** Backfills 6 reflectivity frames and 6 velocity frames
- **Render pipeline:** Level 3 polar data decoded by MetPy → reprojected to lat/lon grid → rendered as RGBA PNG via matplotlib → base64-encoded → pushed to MapLibre as a raster layer
- **Grid size:** 512×512 by default (configurable via `--render-grid-size`)
- **Render time:** ~80–150 ms on modern hardware at default grid size

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

These can be enabled in any combination, independent of the SPC product selection.

#### Legend

When any hazard product is active, a compact color legend row appears below the drawer controls showing color swatches and probability labels. It animates in and out with the drawer.

#### Clicking Hazard Features

**SPC Categorical / Tornado / Wind / Hail polygons:**
- Click a polygon → popup with risk level and probability label
- Click "Read Discussion" in the popup → fetches full text from IEM/SPC and displays it in the [Outlook Panel](#8-outlook-text-panel)

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

#### Coverage Mode Selection

| Button | Description |
|--------|-------------|
| **CONUS** | Full continental US view (1600×800 px, updates every 5 min) |
| **MESO-1** | Mesoscale sector 1 (1024×1024 px, updates every 1 min) |
| **MESO-2** | Mesoscale sector 2 (1024×1024 px, updates every 1 min) |

MESO-1 and MESO-2 buttons are disabled until the satellite system confirms those sectors are available. Hover over MESO-1 or MESO-2 buttons to preview the sector boundary on the map as a semi-transparent overlay.

#### Opacity Slider

Controls satellite image transparency from 0% (invisible) to 100% (fully opaque). Default is 70%. Useful for seeing underlying map features through the imagery.

#### Playback Controls

Identical layout to the radar playback row:

| Button | Action |
|--------|--------|
| ⏮ | Jump to oldest frame |
| ⏪ | Step back one frame |
| ▶ / ⏸ | Start / pause auto-playback (500 ms per frame) |
| ⏩ | Step forward one frame |
| ⏭ | Jump to newest frame |
| Timeline slider | Scrub to any cached frame |
| Time label | Current frame UTC time (--:--Z if no data yet) |

#### How Satellite Data Works

- **Source:** IEM GOES-East WMS (`mesonet.agron.iastate.edu`) serving current operational GOES satellite (GOES-19 as of 2025)
- **Request format:** WMS GetMap PNG
- **Frame cache:** Up to 10 frames per mode
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
| **NSSL** | NSSL CLAMPS DL truck soundings via THREDDS | CLAMPS markers appear when data is available; click one |

In HRRR mode, a click on the map immediately starts fetching. In OBS and NSSL modes, station/truck markers are rendered on the map first; clicking a marker fetches and opens the sounding dialog.

See [Section 9](#9-point-soundings) for full dialog documentation.

---

### 6.5 Surface Obs

**Button:** `SURFACE` (checkable toggle, expands drawer)

Displays live surface observation station models from up to three mesonet networks simultaneously.

#### Network Toggles (multi-select)

| Button | Network | Coverage |
|--------|---------|----------|
| **OK MESONET** | Oklahoma Mesonet (OU) | ~120 stations across Oklahoma |
| **WTM** | West Texas Mesonet (TTU) | ~50 stations across West Texas |
| **KS MESONET** | Kansas Mesonet (K-State) | ~50 stations across Kansas |

Any combination can be active at once. Each network polls independently every 5 minutes.

#### Station Model Display

Each station renders a compact model circle on the map showing:

| Position | Data |
|----------|------|
| Upper-left | Temperature (°F) |
| Lower-left | Dewpoint (°F) |
| Center | Wind barb |
| Right | Wind speed / direction |

Toggle station models on or off with the **Show plots** checkbox.

---

### 6.6 Routing

**Button:** `ROUTING` (checkable toggle, expands drawer)

Provides turn-by-turn driving directions rendered on the map.

#### Setting Origin and Destination

- **Origin:** Type an address, lat/lon pair, or click **Use My Location** to use the current GPS position.
- **Destination:** Type an address or lat/lon pair, or click a point on the map while the destination field is focused.

Click **Get Directions** to fetch the route. The route geometry is drawn on the map as a colored line, and the directions list appears below with each maneuver's distance and turn arrow (↑, ←, →, ↖, ↗, ↩, etc.).

#### Auto-Re-Routing

If the vehicle drifts more than 100 m off the calculated route for 3 or more consecutive GPS fixes, STORM automatically re-fetches directions from the current position.

#### Arrival

When the vehicle is within 30 m of the destination, STORM shows an arrival notification and clears the route from the map.

#### Data Sources

| Function | Service |
|----------|---------|
| Turn-by-turn routing | OSRM public demo server (`router.project-osrm.org`) |
| Address geocoding | Nominatim (`nominatim.openstreetmap.org`) |

---

### 6.7 Annotations

**Button:** `ANNOTATIONS` (checkable toggle, expands drawer)

Annotations are field-condition markers placed on the map and synced to all vehicles over MQTT.

#### Annotation Types

| Icon | Type | Color | Use For |
|------|------|-------|---------|
| ✕ | Road Closure | Red (#E53935) | Blocked road — no through traffic |
| ▲ | Construction | Yellow (#FFD166) | Active road work |
| ~ | Flooded Road | Blue (#4A9EFF) | Standing water over road |
| ⚡ | Downed Power Lines | Yellow (#FFD166) | Electrical hazard on road |
| ! | Road Debris | Orange (#FF6B35) | Debris field on road |

#### Placing an Annotation

1. Click the desired annotation type button in the drawer — it highlights in that type's color.
2. Click any location on the map.
3. A dialog opens showing the clicked coordinates and an optional "Note" text field.
4. Add any relevant note (e.g., "Road completely blocked at intersection with CR-1234").
5. Click **Add to Map**.
6. The annotation marker appears on the map immediately and is published to all connected vehicles via MQTT.

#### Editing or Deleting an Annotation

1. Click an existing annotation marker on the map.
2. The same dialog opens, pre-filled with the existing note.
3. Edit the note or click **Delete** to remove it.
4. Click **Save** to confirm.

Changes and deletions are published to all connected vehicles.

---

### 6.8 Drawings

**Button:** `DRAWINGS` (collapsible drawer)

Drawings are meteorological features sketched on the map — synced to all vehicles over MQTT.

#### Front Types

| Button | Front | Symbol | Color |
|--------|-------|--------|-------|
| Cold Front | Cold Front | Triangles pointing in direction of motion | Blue (#4A9EFF) |
| Warm Front | Warm Front | Semicircles pointing in direction of motion | Red (#E53935) |
| Stationary Front | Stationary Front | Alternating triangles + semicircles on one line | Blue + Red |
| Occluded Front | Occluded Front | Alternating triangles + semicircles, same side | Purple (#9C27B0) |
| Dryline | Dryline | Scalloped arcs above the line | Brown (#D4872E) |

#### Custom Shapes

| Button | Shape | Color |
|--------|-------|-------|
| — | Polyline | White (#E8EAF0) |
| □ | Polygon | White (#E8EAF0) |

#### Drawing a Front or Shape

1. Click the front type or shape button — it highlights and the map enters drawing mode (crosshair cursor).
2. Click the map to place the first point, then continue clicking to trace the line.
3. **Finish:** Right-click anywhere or press **Escape**.
4. A dialog prompts for a title (optional for fronts; required for polylines and polygons; defaults to the type name if left blank).
5. The feature appears on the map with the appropriate symbol and a title label at its center.

#### Editing or Deleting a Drawing

1. Click an existing drawing on the map.
2. The dialog opens with the current title.
3. For fronts: an option to flip direction (reverses the symbol orientation) is available.
4. Edit the title, flip direction, or click **Delete**.
5. Click **Save** to confirm.

All changes are synced via MQTT.

#### Disabled If

`--disable-annotations` is passed at launch — this flag disables annotations, drawings, station plots, and the storm motion cone together.

---

### 6.9 Storm Motion Cone

**Button:** `CONE`

Places a storm motion cone on the map — a forward-propagating sector showing the expected storm track with a 20° angular spread and time rings.

#### Creating a Cone

1. Click the **CONE** button.
2. Click any location on the map (typically the current storm location).
3. A dialog opens with two fields:
   - **Speed (knots):** Expected storm forward speed. Default 35 kts. Range: 0–200.
   - **Heading (degrees):** Direction the storm is moving *toward*. Default 240° (toward the SSW). Range: 0–359.
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

Cones are synced via MQTT (`storm/cones/{id}` topic).

#### Disabled If

`--disable-annotations` is passed at launch.

---

### 6.10 Measurement Tool

**Button:** `MEASURE` (checkable toggle)

Measures great-circle distances on the map.

#### Usage

1. Click the **MEASURE** button — the map cursor changes to a crosshair.
2. Click a start point on the map — a dot appears.
3. Move the mouse — a preview line and live distance label follow the cursor.
4. Click an end point — the segment is fixed.
5. Continue clicking to measure additional segments (multi-segment path).
6. **Finish:** Right-click or press **Escape**.
7. Distance is shown in miles (e.g., "47.3 mi") with total path length.

**Clear:** A **Clear** button removes all measurement geometry from the map.

**Color:** Cyan lines and labels

---

### 6.11 Station Plots

**Button:** `STATION PLOTS` (checkable toggle)

Displays MetPy-style meteorological station plot icons at each tracked vehicle's current position.

#### Station Plot Layout

```
  [Temp °F]   [Pressure code]
       ○──wind barb
  [Dewpt °F]
```

| Position | Data | Color |
|----------|------|-------|
| Upper-left (NW) | Temperature (°F) | Red text |
| Lower-left (SW) | Dewpoint (°F) | Green text |
| Upper-right (NE) | Pressure code (last 3 digits × 10, e.g., 1013.2 mb → "132") | White text |
| Center | Wind barb (meteorological convention, barbs point toward station) | White |
| Center dot | Anchor | White circle |

**Wind Calm:** If wind speed is ≤2 kt, only a small circle is rendered (no barbs).

#### How Station Plots Are Rendered

Station plots are generated as PNG images (135×135 px, transparent background) by MetPy and matplotlib, then pushed to the map as image markers. Each vehicle's plot is cached and only re-rendered when new observation data arrives.

#### Disabled If

`--disable-annotations` is passed at launch.

---

### 6.12 Deployment Locations

**Button:** `DEPLOY LOCS` (checkable toggle, expands drawer)

Displays historical deployment locations as markers on the map, filtered by research quality metrics.

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

- **File:** `data/deployment_locations.json`
- Markers are pre-loaded at startup; toggling visibility or threshold is instant.

#### Disabled If

`--disable-deploy-locs` is passed at launch, or the locations file is missing.

---

### 6.13 Vehicle Panel

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

---

## 7. Status Bar

The status bar runs along the bottom of the main window.

| Section | Content |
|---------|---------|
| **Left** | Mouse coordinates (lat/lon) and zoom level, updated live as you move the cursor over the map. Example: `35.2413 | -97.4521   Z 6.2` |
| **Center** | Status messages: "Ready" (default), "MQTT connected", error messages (e.g., "Radar fetch error: timeout") |
| **Right** | Current UTC clock (updates every second). Example: `18:45:32 UTC` |
| **Far right** | Network status indicator pill: **Green** = Online, **Yellow** = Slow (>1 s response), **Red** = Offline. Checked every 30 seconds via Cloudflare DNS. |

---

## 8. Outlook Text Panel

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

## 9. Point Soundings

STORM supports three independent sounding sources, selected via the **SOUNDINGS** drawer (see [Section 6.4](#64-soundings)). All three sources display in the same Skew-T log-P dialog.

### HRRR Model Soundings

Click any map location within the HRRR CONUS domain. Data fetched from open-meteo (free tier, no API key required). The dialog opens automatically when data arrives (typically 1–3 seconds). Clicking a new location updates the same dialog in place.

### Observed Radiosonde Soundings

Switch to OBS mode in the SOUNDINGS drawer. Radiosonde station markers appear on the map at active upper-air sites. Click a marker to fetch the most recent balloon launch profile from IEM RAOB. STORM automatically selects the 00Z or 12Z launch closest to the current UTC time.

### NSSL CLAMPS DL Truck Soundings

Switch to NSSL mode in the SOUNDINGS drawer. CLAMPS truck markers appear when data is available from the NSSL THREDDS fileserver. Click a marker to fetch and display the most recent truck sounding. Up to 12 hours of soundings per truck are available.

### Triggering a Sounding

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
│  LR 700-500  LR 0-3km  SFC θe  PW  Conv Temp  STP  SCP  EHI│
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
| Conv Temp | Convective temperature — surface temperature required for convection initiation (°C) |
| STP | Significant Tornado Parameter |
| SCP | Supercell Composite Parameter |
| EHI | Energy-Helicity Index |

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
| Source | NSSL CLAMPS DL truck soundings via THREDDS fileServer |
| Catalog | `data.nssl.noaa.gov/thredds/catalog/FRDD/CLAMPS/dltruck/` |
| Availability | Only when NSSL DL truck is actively collecting data |
| History | Up to 12 hours of soundings per truck available |

---

## 10. Vehicle Tracking & Observations

STORM tracks vehicles through several parallel data input channels. All inputs feed into the live vehicle state used by the vehicle panel, station plots, and map markers.

### Track A — Local File Watcher

Polls a FOFS truck logger format file every 10 seconds. Enabled only if a data directory was selected at launch.

**Expected filename:** `YYYYMMDD.txt` (today's date)

**Expected columns (tab or comma delimited):**

| Column | Format | Description |
|--------|--------|-------------|
| `gps_date` | DDMMYY | GPS date |
| `gps_time` | HHMMSS | GPS time (UTC) |
| `lat` | decimal degrees | Latitude |
| `lon` | decimal degrees | Longitude |
| `t_fast` | °F | Fast-response temperature |
| `dewpoint` | °F | Dewpoint temperature |
| `sfc_wspd` | knots | Surface wind speed |
| `sfc_wdir` | degrees | Surface wind direction |
| `pressure` | mb | Atmospheric pressure |

### Track B — GPS Reader

Automatically detects and reads NMEA sentences from a connected serial GPS device. Provides position-only data (no met obs). Active only if `--disable-data-inputs` is not passed.

### MQTT — Inbound Vehicle Observations

Receives observations from other vehicles via the `storm/vehicles/{id}` topic. Active whenever MQTT is connected and not disabled.

## 11. Network & MQTT Sync

STORM uses MQTT over AWS IoT (TLS) to synchronize annotations, drawings, cones, and vehicle observations across all connected vehicles in the network.

### Topics

| Topic | Content |
|-------|---------|
| `storm/vehicles/{id}` | Vehicle observations (lat, lon, temp, dewpoint, wind, pressure) |
| `storm/annotations/{id}` | Road condition annotations (create / edit / delete) |
| `storm/drawings/{id}` | Meteorological drawings (fronts, polylines, polygons) |
| `storm/cones/{id}` | Storm motion cones |

### Behavior

- On connect, retained messages for all active annotations, drawings, and cones are received and displayed automatically.
- Disconnect/reconnect is handled automatically — STORM continuously attempts to reconnect with exponential backoff.
- If `--disable-mqtt` is passed, all sync features are disabled and the vehicle panel shows only locally-observed vehicles.
- Status bar shows "MQTT connected" or an error message.

---

## 12. Data Sources & Polling Intervals

| Data Source | Polling Interval | Notes |
|-------------|-----------------|-------|
| NEXRAD Radar (Unidata THREDDS) | Every 2 minutes | Level 3 products; no auth required |
| SPC Categorical Outlook | Every 15 minutes | Day 1 only |
| SPC Tornado / Wind / Hail Probabilities | Every 15 minutes | All fetched together with categorical |
| SPC Watches | Every 2 minutes | County polygons, tornado + SVR tstm |
| SPC Mesoscale Discussions | Every 2 minutes | MD polygon geometry + number |
| NWS Warnings | Every 2 minutes | All active VTEC phenomenons |
| GOES Satellite (CONUS) | Every 5 minutes | Full CONUS, up to 10 frames |
| GOES Satellite (MESO-1/2) | Every 1 minute | When active, per-sector |
| HRRR Point Sounding (open-meteo) | On demand | 1 API call per map click |
| Observed Radiosonde (IEM RAOB) | On demand | Nearest 00Z / 12Z launch |
| NSSL CLAMPS DL Truck | On demand | Fetched from NSSL THREDDS on click |
| OK / KS / WTM Mesonet (surface obs) | Every 5 minutes | Per-network, independent toggles |
| Turn-by-turn Routing (OSRM) | On demand | Re-fetched automatically if off-route |
| Local Obs File (Track A) | Every 10 seconds | Today's YYYYMMDD.txt |
| GPS (Track B) | Real-time / continuous | NMEA sentences |
| MQTT Inbound | Real-time | Vehicle obs, annotations, drawings, cones |
| Network Health Check | Every 30 seconds | Cloudflare 1.1.1.1 ping |

---

## 13. Command-Line Options

Run `python main.py --help` for the full list. Key options:

### General

```
--debug                        Enable debug logging and the debug panel (Ctrl+D)
--log-level LEVEL              Logging verbosity: DEBUG, INFO, WARNING, ERROR (default: WARNING)
--monitor                      Monitor mode: suppresses local obs inputs
```

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
--render-grid-size N            Radar rendering grid: 128, 256, 512 (default), 768, or 1024
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
--mqtt-no-tls                  MQTT without TLS (development use only, not for field use)
```

---

## 14. Configuration & Certificates

### config.py

Edit `config.py` for persistent defaults:

| Constant | Description |
|----------|-------------|
| `ACCENT_COLOR` | UI accent color (default: `"#00CFFF"`, cyan) |
| `VEHICLE_ID` | Default vehicle ID (overridden by launch dialog) |
| `OBS_FILE_DIR` | Default data directory path (overridden by launch dialog) |
| `MQTT_HOST` | AWS IoT endpoint hostname |
| `MQTT_PORT` | MQTT port (default: 8883 for TLS) |
| `MQTT_USE_TLS` | Whether to require TLS (default: True) |

### Persistent Session State (QSettings)

The following are saved automatically and restored at next launch:
- Window geometry and dock layout
- Last-used vehicle ID
- Last-used data directory

Settings are stored under the `"NSSL" / "STORM"` namespace in your OS's native settings storage.

### TLS Certificate Files

Place these three files exactly at these paths before using MQTT:

| File | Role |
|------|------|
| `/aws/storm.pem` | CA certificate |
| `/aws/storm.pem.crt` | Device certificate |
| `/aws/storm-private.pem.key` | Private key |

Missing certificates cause MQTT to fail silently — all other features remain operational.

---

## 15. Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Escape** | Cancel active drawing / annotation placement / measurement mode |
| **Ctrl+D** | Toggle debug panel |
| **Ctrl+E** | Toggle error log panel |
| **Right-click** | Finish drawing or measurement in progress |

---

## 16. Performance Tuning

### Radar Grid Size

The radar rendering grid controls the sharpness and rendering time of the NEXRAD overlay.

| Grid Size | Render Time (approx.) | Recommended Use |
|-----------|----------------------|-----------------|
| 128 | ~5–10 ms | Very slow hardware, emergency fallback |
| 256 | ~20–40 ms | Older laptops, safe-mode deployments |
| **512** | **~80–150 ms** | **Default — balanced quality/speed** |
| 768 | ~200–400 ms | High-end hardware, sharper image |
| 1024 | ~400–800 ms | Workstation-class machines only |

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

## 17. Diagnostics & Error Handling

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
| "MQTT disconnected" in status bar | Certificate missing or endpoint unreachable | Verify `/aws/` certificate files exist |
| Station plots missing | `--disable-annotations` passed | Relaunch without that flag |
| MESO-1/2 buttons grayed out | Sectors not currently available | Wait — buttons enable automatically when sectors are active |
| Map tiles blank/gray | `storm.mbtiles` missing or corrupted | Verify the MBTiles file is present and not zero-byte |
| Slow/choppy radar rendering | Grid size too large for hardware | Use `--render-grid-size 256` |
| Hatch patterns not visible | WebGL limitation | Known quirk; patterns are built as pixel data, not canvas — should render in all supported modes |

---

## 18. Known Limitations & Quirks

**Canvas Readback**
MapLibre GL running inside QWebEngineView cannot reliably read back canvas pixel data. Hatch patterns (CIG/SIGN significant areas) are therefore built entirely as raw `Uint8Array` pixel data and added to MapLibre via `addImage()`, rather than drawn on a canvas. This is a known architectural constraint.

**QWebChannel Async Initialization**
The `bridge` object that connects Python and JavaScript is initialized asynchronously after the map loads. All JavaScript API functions are stubbed as no-ops (`_stormNoop`) until the bridge is ready. In practice this is transparent to the user, but very rapid actions in the first second after launch may be silently dropped.

**MBTiles File Not Included in Repository**
The offline vector tile database (`storm.mbtiles`) is large (500 MB–1 GB) and is not included in the git repository. It must be downloaded separately and placed in the project root.

**SPC Text Product Latency**
IEM AFOS sometimes responds slowly (up to 20 seconds per request). STORM waits patiently rather than timing out aggressively. This is normal behavior during high-traffic SPC issuance periods.

**MESO Sectors Availability**
GOES MESO-1 and MESO-2 sectors are not always active or available over your area of interest. STORM automatically enables/disables the MESO buttons based on sector availability. This is a function of NOAA's operational GOES scanning schedule.

**GOES Satellite Band**
The IEM WMS endpoint serves the default operational visible or IR band for the current GOES satellite. Band selection is not user-configurable from within STORM.

**SPC Data Lag**
SPC GeoJSON products (tor, wind, hail) are typically updated once or twice daily. They may not reflect very recent issuances until the next polling cycle completes (up to 15 minutes after the fact). SPC Watches and MDs poll every 2 minutes and are effectively near-real-time.

---

## 19. Feature Availability Matrix

| Feature | Default | Disabled By |
|---------|---------|-------------|
| Offline vector base map | ✅ Always | n/a (requires storm.mbtiles) |
| NEXRAD Radar overlay | ✅ Enabled | `--disable-radar` |
| GOES Satellite overlay | ✅ Enabled | n/a |
| SPC Hazards (outlook, tor, wind, hail) | ✅ Enabled | n/a |
| SPC Watches | ✅ Enabled | n/a |
| SPC Mesoscale Discussions | ✅ Enabled | n/a |
| NWS Warnings | ✅ Enabled | n/a |
| Outlook Text Panel | ✅ Enabled | n/a |
| HRRR Point Soundings | ✅ Enabled | n/a |
| Observed Radiosonde Soundings | ✅ Enabled | n/a |
| NSSL CLAMPS Truck Soundings | ✅ Enabled (when data available) | n/a |
| Surface Obs (OK / KS / WTM Mesonet) | ✅ Enabled (when toggled on) | n/a |
| Turn-by-turn Routing | ✅ Enabled | n/a |
| Annotations (road conditions) | ✅ Enabled | `--disable-annotations` |
| Drawings (fronts, polylines, polygons) | ✅ Enabled | `--disable-annotations` |
| Storm Motion Cone | ✅ Enabled | `--disable-annotations` |
| Station Plots (vehicle obs) | ✅ Enabled | `--disable-annotations` |
| Measurement Tool | ✅ Enabled | n/a |
| Deployment Locations | ✅ Enabled | `--disable-deploy-locs` or missing data file |
| Vehicle Panel | ✅ Enabled | n/a |
| MQTT Sync (annotations, drawings, cones) | ✅ Enabled | `--disable-mqtt` or `--disable-annotations` |
| MQTT Vehicle Obs (inbound) | ✅ Enabled | `--disable-mqtt` |
| Local File Watcher (Track A) | ✅ Enabled (if dir set) | `--disable-data-inputs` or no dir selected |
| GPS Reader (Track B) | ✅ Enabled (if device present) | `--disable-data-inputs` |
| Radar Playback (loop mode) | ✅ Enabled | `--disable-radar` |
| Satellite Playback (loop mode) | ✅ Enabled | n/a |

---

*STORM — Severe Thunderstorm Observation and Reconnaissance Monitor*
*Internal field operations tool — NSSL Mobile Mesonet program*
