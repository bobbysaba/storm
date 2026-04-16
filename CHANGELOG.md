# Changelog

All notable changes to STORM will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] - 2026-04-16
### Added
- ASOS/AWOS surface observations — draw a map bounding box to fetch IEM ASOS current observations, then render them as full station plots alongside OK Mesonet and West Texas Mesonet data
- ASOS bbox reuse and redraw workflow — toggling ASOS back on reuses the last domain; the surface status text includes a `new box` link to select a replacement domain
- Surface station freshness coloring — surface station plots color their center dot by observation age, with wider freshness thresholds for hourly ASOS reports
- Custom drawing styling — polylines and polygons can be titled and styled with custom color and line style; saved edits sync through the existing drawing workflow
- ASOS station plot transport optimization — station plot PNGs are served through the in-process `storm://` scheme instead of being embedded as base64 payloads

### Changed
- Surface obs drawer now supports OK Mesonet, WTM, and ASOS controls from one place
- ASOS selection uses the application accent color for the click-drag bounding box
- Surface plot caching now includes observation timestamp so station freshness colors update from the observation valid time rather than stale cached plot images
- OK Mesonet and WTM surface plots render and appear as a single batch; ASOS plots are chunked because user-selected domains may contain many stations

### Fixed
- ASOS plot URL decoding for station IDs served via `storm://app/plots/...`
- ASOS bbox draw mode now exits and restores map interaction state immediately after selection
- Stale ASOS render batches are discarded when a new bbox is requested

---

## [1.0.0] - 2026-04-14
### Added
- Vehicle meteorological timeseries dialog — interactive time-series plots of temperature, dewpoint, wind speed/direction, and pressure for any tracked vehicle; scroll-wheel zoom, click-drag selection zoom, double-click to reset, and inline cursor readouts with 10-second grid snapping; works in both live and archive modes
- VAD wind profile — initial support for VAD-derived wind profiles from NEXRAD data
- Layer ordering pill — floating UI control to reorder map layer draw order (radar, satellite, hazards, annotations, drawings) at runtime
- Screenshot capability — save the current map view as an image from the layer pill
- CWA warning display — NWS warning polygons now include county warning area context and improved text formatting
- Warning filtering — filter active NWS warnings by type; improved relevance for field operations
- Annotation expiration — annotations now carry a configurable expiration time and auto-clear from the map when expired
- Auto environment updating — `conda env update --prune` step integrated into the in-app update flow

### Changed
- NSSL/OBS sounding dialog defaults to the most recent available sounding on open
- NSSL sounding unit handling fixed (temperature/dewpoint consistency)

### Fixed
- Radar data toggle state bug — toggling radar off/on no longer drops the last fetched frame
- Surface station display bug during network interruptions
- Layer pill layout and sizing on various screen resolutions
- Satellite/radar map projection alignment fix
- Various stability patches and minor UI refinements

---

## [0.9.0] - 2026-03-31
### Added
- Archive mode — replay any past session with full data reconstruction; select a date/time at launch to enter archive playback
- Central time controller with play/pause, configurable speed multipliers (1×–300×), and ←/→ step buttons (30-second steps)
- Archive fetchers for NEXRAD radar, satellite, hazards, soundings, and MQTT vehicle position data — all synchronized to the archive clock
- Archive controls bar with timeline scrubber, playback speed selector, and per-layer status indicators (radar, satellite)
- Archive loading dialog — shows fetch progress before playback begins
- Window title and status pill reflect the active archive timestamp (`[ARCHIVE YYYY-MM-DD HH:MMZ]`)

---

## [0.8.0] - 2026-03-25
### Added
- Mesonet surface observation overlay — live station data fetched and displayed on the map
- Observed sounding dialog — fetch and display real-time vertical profiles from surface obs networks
- CLAMPS sounding support — additional sounding data source via CLAMPS fetcher
- Routing and turn-by-turn navigation with off-route recalculation and arrival detection
- On-launch data fetch selection — choose which data products to load at startup

### Changed
- Status pill top row reorganized: version anchor (`STORM vX.X.X`), update indicator, and status message now occupy a dedicated top row above mode/position and connectivity rows
- Launch window and viewer mode patching

---

## [0.7.0] - 2026-03-19
### Added
- HRRR point sounding dialog — click any map location to fetch a live vertical atmospheric profile from the open-meteo HRRR API (free tier, single HTTP request per click)
- Skew-T log-P diagram with temperature, dewpoint, virtual temperature curve, wind barbs, surface-based parcel profile, and CAPE/CIN shading
- SHARPpy-inspired features: dendritic growth zone shading (-10 to -20°C), effective inflow layer bracket on left spine, AGL height reference lines (0.5–9 km) in red
- Hodograph inset (color-coded by height: 0–3 km red, 3–6 km gold, 6–9 km blue) with EIL segment highlight, Bunkers RM/LM dots, and storm motion dir/spd readout
- Parcel table showing CAPE, CIN, LCL, LFC, and EL for surface-based, mixed-layer, and most-unstable parcels
- Kinematics table showing bulk shear, SRH, and mean storm-relative wind for 0–500 m, 0–1 km, 0–3 km, and 0–6 km layers
- Composite indices row: LR 700–500, LR 0–3 km, SFC θe, PW, Convective Temperature, STP, SCP, EHI — with threshold-based color coding
- F0–F3 forecast hour scrubber in the dialog header (cyan accent); header displays both init time and valid time
- Interactive pressure-level cursor readout (hover over SkewT to see T, Td, wind, height)

### Changed
- Point sounding data source: open-meteo HRRR CONUS at 3 km / hourly resolution; rate limit 10,000 calls/day on free tier

## [0.6.0] - 2026-03-13
### Added
- Internet connectivity indicator in status pill (● NET OK / ● NET SLOW / ● NO INTERNET) — TCP check to 1.1.1.1:53 every 30 seconds
- "AWAITING VEHICLES..." placeholder in vehicle panel that auto-hides after first fetch completes

### Changed
- Tile and asset serving migrated from Flask (localhost:8765) to QWebEngineUrlSchemeHandler (storm://app/) — no open TCP port, no firewall exposure, faster startup
- Flask and Werkzeug removed as dependencies from both Mac and Windows env files
- MQTT status indicator renamed: CONNECTED → AWS OK, OFFLINE → AWS OFFLINE
- Monitor mode badge in status pill renamed: OBSERVER → MONITOR
- Update check failure message changed from red error to amber warning with "PROCEED AND TRY AGAIN LATER" guidance
- Update available text simplified from "N updates available" to "UPDATE AVAILABLE"
- Git fetch timeout in launch dialog reduced from 10s to 5s for faster failure on slow connections

### Fixed
- Hazard error clear timer was incorrectly wired to the radar error clear method — each now only clears its own prefix
- Radar error in status bar now clears immediately when a successful scan arrives instead of waiting for the timer
- Vehicle panel placeholder visibility check used `isVisible()` which returned False when panel was closed — now hides unconditionally after first fetch
- Net connectivity indicator used `QTimer.singleShot` from a background thread (unreliable) — replaced with `_NetChecker` QObject using a proper pyqtSignal

---

## [0.5.0] - 2026-03-08
### Added
- Hazard overlay panel (SPC and NWS layers accessible via HAZARDS toolbar button)
- SPC Day 1 convective outlook (MRGL / SLGT / ENH / MDT / HIGH risk tiers)
- SPC tornado, wind, and hail probability overlays
- SPC severe thunderstorm and tornado watches
- SPC Mesoscale Discussions via NOAA MapServer GeoJSON endpoint
- NWS active warnings with per-event color coding
- Click any SPC outlook or MD polygon to read the full discussion text in a sliding panel
- Version number displayed in window title and status overlay

### Fixed
- SPC outlook now correctly renders ENH (Enhanced) and MRGL (Marginal) risk tiers — previously silently dropped
- Hazard and annotation drawers now have transparent backgrounds consistent with the radar drawer
- NWS warning bounding box updates dynamically as the map is panned

---

## [0.4.0] - 2026-03-08
### Added
- Variable radar resolution control
- Front annotations (cold, warm, stationary, occluded, dry line)

### Fixed
- Default window size and position settings on Windows
- Vehicle ID assignment bug

---

## [0.3.0] - 2026-03-06
### Added
- Previous deployment locations overlay
- Windows compatibility (Chromium/ANGLE GPU workarounds, setup scripts)
- macOS and Windows application build and update scripts

### Fixed
- Various Windows setup and startup bugs
- Small road layer visibility adjustments

---

## [0.2.0] - 2026-03-05
### Added
- NEXRAD radar overlay with site selector, product toggle (reflectivity / velocity), and frame playback
- Annotation tools (road conditions, storm motion, point markers)
- Measure tool

---

## [0.1.0] - 2026-02-28
### Added
- Initial build — MapLibre GL map with local MBTiles tile server
- Basic application shell, dark theme, floating toolbar
