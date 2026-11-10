# STORM performance plan

Goal: make STORM lighter and faster on weak hardware (Raspberry Pis, older Windows laptops) without a frontend rewrite.

## Findings from the audit

- The map is already a web frontend: `ui/map/widget.py` hosts MapLibre GL in a `QWebEngineView` (`ui/map/map_template.html`, ~4.5k lines). Radar, surface plots, fronts, markers and drawings are JS/canvas.
- The ~11k lines of Qt around it are cheap native widgets. No meaningful `QPainter` or OpenGL work.
- Real costs:
  - QtWebEngine (bundled Chromium): memory, GPU process, install size, and GPU crashes (see `SAFE_MAP_MODE`).
  - The Python science stack: `metpy`, `cartopy`, `xarray`, `scipy`, `matplotlib` dominate startup time and install size.
  - Radar rendering in `ui/map/radar_overlay.py`: polar to Cartesian via `scipy.ndimage.map_coordinates`, matplotlib colormapping, then PNG encoding, all on the CPU in Python.
- Decision: a full JS rewrite (Electron/Tauri) would keep a Chromium, still need a Python backend for the science code, and add a local-socket bridge. Not worth it for performance.
- Moving the Qt panels into the web page would not make things faster either. It only helps maintainability and portability, so skip it unless wanted for its own sake.

## Step 0: Profile first

Don't optimise blind. On the slow target hardware, measure:

- Time to launch dialog, and time to main window.
- Idle and with-radar RSS of the Python process and the Chromium (`QtWebEngineProcess`) processes.
- Per-scan radar render time (`radar_overlay.py` already tracks `perf_counter` and adapts grid size).
- CPU and GPU use while panning/zooming with radar plus surface plots on.

Output: a short table of what actually dominates, to decide the order below.

## Step 1: Lazy heavy imports (cheap, startup win)

- `main.py` imports `ui.launch.dialog` and `ui.map.radar_overlay` at module level, and `radar_overlay` imports matplotlib and scipy eagerly.
- Defer `metpy`, `cartopy` and `matplotlib` until the sounding, VAD and timeseries dialogs or the radar layer first need them.
- Check that `matplotlib.use("Agg")` ordering still holds after the move.
- Done when: launch dialog appears noticeably sooner; `python -X importtime main.py` shows the heavy modules no longer loaded at startup.

## Step 2: Chromium and WebEngine tuning

- Try these flags via `QTWEBENGINE_CHROMIUM_FLAGS`, one at a time, on the target hardware: `--disable-gpu-compositing`, `--in-process-gpu`, `--renderer-process-limit=1`.
- Keep the existing `SAFE_MAP_MODE` / `runtime_flags` mechanism as the opt-in route and expose any flags that help there.
- Done when: measured memory/GPU improvement on target hardware with no new crashes.

## Step 3: Lite mode

- A runtime flag (alongside `runtime_flags.py` / `feature_flags.py`) that caps:
  - radar render grid (`MAX_RENDER_GRID_SIZE`, `ADAPTIVE_GRID_STEPS`)
  - surface-plot density
  - animation / refresh rate
- Consider auto-enabling when the adaptive radar grid keeps stepping down.

## Step 4: GPU radar rendering (biggest runtime win, largest change)

- Send raw polar scan data (as a texture) to the map and do reprojection and colormapping in a WebGL shader, instead of scipy + matplotlib + PNG in Python.
- Removes the Python CPU hot path and the PNG encode/base64 transfer over the bridge.
- Risks: GPU-crash-prone Windows devices (keep the PNG path as fallback under `SAFE_MAP_MODE` / lite mode), colormap parity with the current matplotlib output, and Level 2 vs. other scan formats (`core/radar_scan.py`, `core/level2_radar_scan.py`).
- Do a small prototype with one product (e.g. reflectivity) before committing.

## Not planned

- Full frontend rewrite in JS.
- Moving Qt panels into the web page (revisit only for maintainability or a browser-only build; it would need a Python backend over a local socket and would not improve performance).

## Suggested order

0 (profile) -> 1 -> 2 -> 3 -> 4, reassessing after each based on the Step 0 numbers.

---

# Cache and memory audit plan (2026-11-09)

Target: the desktop GUI on normal laptops. The Pis run the headless publisher and are not affected by any of this.

Baseline numbers (this Mac, `storm` env, synthetic 720x1840 scan): radar render 123 ms at grid 768 and 290 ms at 1536; one live scan 31.8 MB; radar stack import 4.2 s; station plot render 6.9 ms.

Each item is its own commit. Order is by payoff and by which items share code.

## A. Radar scan storage and render path (audit items 1 and 2)

Files: `core/radar_scan.py`, `core/level2_radar_scan.py`, `data/radar/radar_decoder.py`, `ui/map/radar_overlay.py`, `ui/app/main_window.py`, `archive/fetchers/radar_archive_fetcher.py`, `ui/dialogs/vad_dialog.py` and anything else reading `scan.lats` / `scan.lons` / `scan.data`.

1. Geometry instead of lat/lon grids. `RadarScan` stores `radar_lat`, `radar_lon`, `azimuths_deg`, `first_gate_m`, `gate_width_m`. `lats` / `lons` become lazy properties for any remaining caller, not stored arrays.
2. Live Level 3 scans store the raw 8-bit codes (`uint8`) plus the 256-entry value table from `f.map_data(np.arange(256))`. Lossless, 1 byte per gate. `data` stays available as a property that applies the table, for the VAD dialog, cursor readout and tests.
3. Archive Level 2 scans store `float16` (decided), upcast to float32 only inside the render.
4. Render through a lookup table: build a 256-entry RGBA table per (colormap, vmin, vmax, value table) once, then colour a frame with one `np.take`. The 8 dBZ reflectivity cut and the velocity/CC/KDP reflectivity mask become boolean tables in code space. Float scans keep the matplotlib path.
5. Cache the reprojection index per (radar location, n_az, n_gates, az_offset, first gate, gate width, grid size): output bounds plus a flat gate index for every output pixel. Small LRU (4 entries). A frame is then take -> RGBA table -> PNG.
6. Cache rendered PNGs per (scan identity, grid size, mask scan identity), bounded to the loop length per product. `_show_scan` and the loop tick inject the cached PNG and only render on a miss, off the main thread.
7. Merge `RadarOverlay._render_to_png` into `render_scan_to_png`.

Done when: pixel-identical PNG against the current renderer for a saved Level 3 reflectivity and velocity file and one Level 2 file; live scan cache under 30 MB with all four products backfilled; loop playback does no render work after the first pass; warm render under 40 ms at grid 768 on this Mac.

## B. Archive radar caches (item 3)

File: `archive/fetchers/radar_archive_fetcher.py`.

- Keep parsed `Level2File` objects only for the current scan and its neighbours (+-1); raw files on disk still cover the +-8 window so a product or tilt switch re-parses from disk.
- Replace per-scan threads with one 2-worker pool for download and decode; prefetch in playback direction first.
- Replace `idx.index(t)` scans with a time -> position dict built with the index.
- Measure RSS before and after with a real session so the saving is a number, not an estimate.

Done when: archive playback RSS is flat while scrubbing a full day, and no more than 2 fetch/decode threads exist at once.

## C. Satellite, live and archive (items 4 and 7)

Files: `data/fetchers/satellite_fetcher.py`, `archive/fetchers/satellite_archive_fetcher.py`, `ui/map/tile_scheme_handler.py`, `ui/map/widget.py`, `ui/map/map_template.html`, `ui/app/main_window.py`.

Shared:
- Frames hold PNG `bytes`, not base64. The scheme handler serves them at `storm://app/satellite/<mode>/<frame-id>.png`; the map gets a URL, not a multi-megabyte JS string. Remove the throwaway `Image` pre-decode in `stormSetSatelliteFrame`.

Live:
- Poll a mesoscale sector only while the satellite layer is on and that sector is selected. Capabilities polling (cheap XML) stays so the sector buttons and boxes keep working.
- On selecting a sector, fetch immediately, as today.

Archive:
- Replace the cartopy `imshow` with a direct resample: for a Web Mercator output grid, compute GOES fixed-grid scan angles in closed form, turn them into array indices, and cache that index per (mode, sector origin, output size). CONUS is constant all day; a mesoscale sector is constant until it is moved.
- Read `CMI` from memory with h5py on a `BytesIO`; no temp file, no xarray on this path.
- Encode as 8-bit grey + alpha instead of RGBA.
- Keep the bbox download's result: it currently downloads the last file of the day and throws the pixels away.
- 2-worker pool, prefetch the next frames in playback direction.
- Measure the per-frame download size first. If CONUS files are as large as expected, look at range requests for only the chunks covering the area of interest as a follow-up.

Done when: no mesoscale requests in the log with the satellite layer off; archive frame render time measured before and after; visual match with the current output at a few zooms.

## D. Vehicle history (item 5)

Files: `ui/app/main_window.py`, `ui/dialogs/vehicle_timeseries_dialog.py`, `archive/vehicle_speed.py`, new small store module.

- Nothing is dropped. Replace the deque of `Observation` objects with a compact per-vehicle column store (time, lat, lon, T, Td, wind speed, wind dir, pressure as growing numeric arrays). About 40 bytes per obs: ten vehicles at 1 Hz for 8 hours is roughly 12 MB.
- Persist: append every obs to a per-day SQLite file in the app data folder, and reload today's history on startup so a restart loses nothing. Keep today's and yesterday's files (2 days, decided) and prune anything older on startup.
- Per-obs work becomes constant: the speed calculation reads only the last 30 s; the timeseries dialog appends and redraws at most once a second instead of a full copy and redraw per obs.

Done when: restart mid-session and the timeseries dialog shows the earlier data; per-obs handling time does not grow over a multi-hour replay.

## E. Station plot cache keys (item 6)

Files: `ui/layers/station_plot_layer.py`, `ui/layers/surface_plot_layer.py`, `ui/app/main_window.py`, `ui/map/map_template.html`, `ui/map/widget.py`.

- Vehicle plots: key on displayed values only. A position-only change calls a new `stormMoveStationPlot` (`setLngLat`), with no render and no DOM rebuild.
- Surface plots: key on displayed values plus the age-colour bucket, not the timestamp. Re-evaluate the bucket on each fetch cycle so a station that stops reporting still turns yellow, then red.
- Share one PNG cache keyed by (values, colour) across stations, bounded LRU.

Done when: a moving vehicle with steady met values triggers zero renders; an unchanged mesonet cycle triggers zero renders.

## F. Lazy heavy imports (item 8)

This is Step 1 above. Do it after A and C, since both change what `radar_overlay` and the archive satellite path import.

## G. CWA shapefile (item 9)

Files: `cwa_shp/`, new `scripts/` converter, `ui/map/widget.py`, `ui/map/tile_scheme_handler.py`, `ui/map/map_template.html`.

- One-off script converts the shapefile to a simplified GeoJSON (tolerance chosen so borders look the same at the zooms STORM uses), committed under `static/`.
- The map loads it by URL (`storm://app/static/...`) as a normal GeoJSON source. Remove the in-app SHP/DBF parser and the `runJavaScript` push.

Done when: enabling the layer is instant and the borders look the same at zoom 5 to 10.

## H. Surface-analysis tile files (item 11)

File: `data/fetchers/sfcoa_overlay_fetcher.py`.

- On startup, and after each download, delete files in `storm_sfcoa_mbtiles` older than 24 hours and cap the folder size.

## Optional, not scheduled

- Persistent read-only SQLite connections for tile lookups (item 10): 0.31 ms -> 0.02 ms per tile here. Not noticeable on a normal computer.
- Cap the land-cover hover tile cache in the map page.
- PNG compression level: revisit only if encode time shows up after A.

---

# Status (2026-11-10)

- Cache/memory items A–H: done (uncommitted at time of writing).
- F / Step 1 lazy imports: done. `tests/test_startup_imports.py` fails if matplotlib, metpy, scipy, cartopy or xarray load at startup.
- Step 0: `scripts/profile_storm.py` (add `--rss` while STORM runs). Still needs running on the slow target hardware. This Mac: main window imports 325 ms, metpy 1.3 s on first sounding, radar warm render 38 ms at grid 768.
- Step 2: `--webengine-flags "..."` appends Chromium flags so each can be measured one at a time. The defaults are unchanged. Not yet measured on target hardware.
- Step 3: `--lite` sets radar grid to 512 (max 768), one surface plot per 0.25° cell, loops at least 1 s per frame, `--renderer-process-limit=1`. The radar overlay logs a hint to use `--lite` when renders stay slow at the smallest grid.
- Step 4: `--gpu-radar` (opt-in, off in safe/lite mode) draws live Level 3 reflectivity with a WebGL custom layer (`ui/map/radar_gl.py`, `stormSetRadarGL` in the map page). Other products, masked products and Level 2 stay on the PNG path. `scripts/check_radar_gl_parity.py` result: coverage identical, azimuth exact, ~1.2% of pixels one gate over at gate edges. Checked by eye in the running app with live data (2026-11-10): looked right.
