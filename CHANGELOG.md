# Changelog

All notable changes to STORM will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
