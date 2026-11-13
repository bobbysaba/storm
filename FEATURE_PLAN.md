# STORM feature plan (phase 2)

Scope, after review: shrink `main_window.py`, add a synced radar / satellite / vehicle loop, a map right-click menu and a command palette. Proximity alerts, cursor readout, SRV, trails, LSRs, saved views and a day palette are out.

## Findings from the audit

- `ui/app/main_window.py` is 5,893 lines and about 300 methods. Two mixins already exist (`MainWindowMapHelpersMixin`, `MainWindowDebugMixin`), so there is a precedent for splitting by mixin.
- The radar loop and the satellite loop are separate. Each has its own `QTimer` (`_loop_timer`, `_satellite_loop_timer`), play button, speed setting and frame index, and they know nothing about each other's time.
- Both caches already carry times: `RadarScan.scan_time` (12 frames per product, about 25–60 min depending on VCP) and satellite frames with `timestamp` / `time_iso` (CONUS via the nowCOAST WMS time list; MESO as 2048² PNGs, 1-minute cadence, 10 frames backfilled).
- ARCHIVE mode already syncs everything to one `TimeController.time_changed`. The live loop can follow the same pattern with a frame-based clock.
- Vehicle positions are not kept over time in live modes. `update_vehicle_obs` only stores history for non-local vehicles that have met data, so the local truck and position-only vehicles have no past positions.
- The map's `contextmenu` handler (`map_template.html:2730`) only finishes drawings and measurements. Every other map action needs a trip to the toolbar first.
- `SHORTCUTS` and `DrawerManager` already describe every toolbar action, which is the data a command palette needs.

## Feasibility of the synced loop

Feasible. Radar and satellite are straightforward, because both caches are timestamped lists and only the "which frame to show" choice changes. Vehicles need a small new position buffer, and that is the only new data kept. Limits to know:

- The time windows differ. Radar covers roughly the last hour. MESO satellite has 10 minutes backfilled. CONUS covers about an hour at 5-minute steps. The loop uses the radar window and shows the nearest satellite frame at or before each radar time. If none is within a tolerance (10 min CONUS, 3 min MESO), the satellite is dimmed and its label says "no frame".
- To fill the MESO gap, the sync can backfill MESO at a 5-minute stride over the radar window (about 12 frames). Each frame is a full 2048² PNG, so this costs bandwidth. It stays off in lite mode and is a checkbox otherwise.
- CONUS frames are WMS tiles that reload on each time change, which is how the satellite loop already works. At 3× speed tiles may not finish loading. The loop waits for the map's tile `idle` event before advancing, and only stretches a step when tiles are slow.
- Vehicles: a teammate first seen after launch has no positions earlier than that, so it is hidden for loop times before its first fix. MONITOR sessions start with an empty buffer.
- ARCHIVE is already synced by `TimeController`, so this is live-only (VEHICLE, MONITOR, VIEWER).

## Rules for this phase

- Every step is a commit that leaves the app shippable. Run `pytest` and a manual pass in VEHICLE, MONITOR, VIEWER and ARCHIVE after each.
- New logic goes in its own module with injected clocks and data, so it can be tested without Qt.

## Step 1: Split `main_window.py` into mixins

Files: `ui/app/main_window.py` and new `ui/app/main_window_*.py` mixins.

Status: done (commits `9c1ffe5`..`94d9d1b`). Notes on what was built:

- `main_window.py` is down from 5,893 to 1,354 lines. Module-level constants and helpers are in `main_window_util.py`. The eight mixins match the table below, except that MQTT connect and disconnect went to `main_window_status.py`, and `_try_auto_select_radar_station` / `_nearest_nexrad` went to radar instead of archive.
- Methods were moved by an AST script, and each commit was checked to be byte-identical to the original source for all 296 methods. Each mixin imports only the names its methods use. Mixins log to `ui.app.main_window`, so log output and filtering are unchanged. `_on_escape_pressed` and `_init_screenshot_button` / `_on_screenshot_clicked` stayed in `main_window.py`.
- `tests/test_main_window_split.py` checks every method in `tests/data/main_window_methods.txt` (the snapshot before the split) still resolves on `MainWindow` and none is defined in two classes.
- Found during the smoke run, already there before the split: closing the SATELLITE drawer before `_init_satellite` ran (it waits for the MQTT startup phase) raised `AttributeError: _satellite_loop_timer`, and a sector picked then was ignored. Fixed in a follow-up commit.

- Move methods as-is into mixins that follow the existing `MainWindowMapHelpersMixin` pattern. Methods stay on `MainWindow` through inheritance, so signal connections, `getattr` lookups and the `test_main_window_*` stand-ins keep working unchanged. Mixins are the lower-risk choice for moving 4,000 lines. Turning each one into an injected controller can come later, one at a time, if it's wanted.
- Planned mixins (approximate line counts from the current file):

  | Mixin | Contents | Lines |
  |---|---|---|
  | `main_window_archive.py` | `_begin_archive_startup` … `_on_archive_obs_station_click` | ~570 |
  | `main_window_radar.py` | `_init_radar`, site/product/station picker, fetch, decode, render, inject, loop | ~600 |
  | `main_window_hazards.py` | `_init_hazards`, SPC/NWS slots, legend, outlook text | ~450 |
  | `main_window_overlays.py` | satellite, mesoanalysis, SFCOA, surface obs, NLCD / basemap | ~720 |
  | `main_window_sounding.py` | sounding mode, ASOS box, comparison sources, VAD | ~230 |
  | `main_window_map_objects.py` | annotations, drawings, storm cones, measure | ~640 |
  | `main_window_vehicles.py` | `update_vehicle_obs`, vehicle panel, follow, scan sectors, timeseries | ~780 |
  | `main_window_status.py` | status bar, net check, GPS indicator, update checker, MQTT status | ~230 |

- What stays in `main_window.py`: `__init__`, startup phases, session restore, toolbar, drawer manager, shortcuts, click modes and `_layout_overlays`. Target about 1,300 lines.
- One commit per mixin, with no behavior change. A test checks that every method name that existed before still resolves on `MainWindow`, using a snapshot list made before the first move.
- Move module-level helpers (`_loop_interval_ms`, `_overlay_time_label`, …) to `ui/app/main_window_util.py` when more than one mixin needs them.
- Done when: `main_window.py` is under 1,500 lines, the full test suite passes, and the four-mode manual pass shows no change.

## Step 2: Synced loop

Files: new `ui/loop_sync.py`, `ui/app/main_window_radar.py`, `ui/app/main_window_overlays.py`, `ui/app/main_window_vehicles.py`, `core/vehicle.py`, `ui/controls/radar_controls.py`, `ui/controls/satellite_controls.py`, `ui/map/map_template.html`, `ui/session_state.py`.

Status: built, pending manual pass. Notes on what was built, and where it differs from the design below:

- The radar loop's own timer drives, instead of a new clock replacing both timers. `LoopSync` (`ui/loop_sync.py`) holds the loop time and its driver. `MainWindowLoopMixin` (`ui/app/main_window_loop.py`) publishes the radar frame on screen after the existing radar handlers run: loop tick, step, slider, loop start/stop, site/product change. It publishes `None` when radar is live. The radar code only gained a check in `_advance_loop_frame` that waits for CONUS tiles. With radar data off, the satellite loop drives the same way and only vehicles follow.
- "Live" means not looping and on the latest frame. A playing loop on the newest frame still counts as a loop time, so the chip doesn't flicker on each wrap. Stopping the loop snaps radar to live, as before.
- The radar cache keeps at most 6 scans over 35 minutes, so the loop window is about 25–35 minutes, not an hour.
- MESO backfill is not possible: IEM's WMS has no TIME dimension and only serves the current frame (`_worker_history` already fell back to that). Instead, `thin_meso_frames` keeps frames past the newest 10 at one per 5 minutes, up to 40 minutes back (16 frames in steady state, about 6 more images in memory). Lite mode keeps the old cap of 10. There is no "fill satellite" checkbox.
- No separate loop header in the drawer: the radar time label already shows `HH:MMZ · age`. While synced, the satellite drawer replaces its speed menu with "SYNCED TO RADAR", because the theme has no disabled style for those buttons.
- Vehicles: `PositionTrack` is fed from `update_vehicle_obs` for every vehicle, local included. While the loop shows vehicles, live fixes still update tracks, the panel and station plots (hidden), but markers keep their loop position and follow mode is paused. The age-color refresh in `main_window_map_helpers.py` skips markers while that's on.
- The status chip is `loop_chip` in the status pill (`◷ LOOP HH:MMZ`, warn color). Clicking it calls `jump_to_latest()` on whichever drawer drives.
- Loop sync is created in `_start_post_startup_fetchers`, after satellite init. It is not created in ARCHIVE or with `--disable-radar`, so with radar disabled nothing syncs.
- Tests: `tests/test_loop_sync.py` (LoopSync, `frame_at`, `PositionTrack`, satellite synced mode, MESO thinning), `tests/test_main_window_loop.py` (the mixin with fake controls and map), and the sync settings in `tests/test_session_state.py`. Checked live in VIEWER against real KTLX and CONUS data: stepping back two frames moved satellite to the matching frame and showed the chip; play, stop, the chip click and SYNC off all behaved. The VEH path has only unit tests, because VIEWER has no vehicles.

### 2a: Loop clock

- `LoopClock(QObject)` in `ui/loop_sync.py` replaces both loop timers. It holds an ordered list of frame times from the *driving* layer (radar when radar data is on, else satellite) and an index, and emits `time_changed(datetime | None)`. `None` means live.
- Play, pause, step, jump to start or end, and speed. The radar drawer's playback row and the existing shortcuts (Space, `[` / `]`) drive it. Releasing the slider seeks it.
- When the driving cache gains a frame during playback, the list is rebuilt and the current time is kept, not the index. Stopping the loop returns to live, as radar does today.
- Tests: `tests/test_loop_sync.py`, covering frame list rebuild, wrap-around, stopping returning to live, and switching the driving layer when radar turns off mid-loop.

### 2b: Layers follow the clock

- A pure helper `nearest_at_or_before(times, t, tolerance)` (bisect) picks the frame for each follower.
- Radar: shows the scan for `t`. This is the existing `_show_scan` path, keyed by time instead of index.
- Satellite: when SYNC is on, its own play button is hidden and the drawer shows "synced to radar". It shows the nearest frame within tolerance, otherwise it dims to 30 % opacity and shows `no frame`. When SYNC is off, satellite keeps its own loop as today.
- Tile pacing: `map_widget` reports the map's `idle` event after a CONUS time change. The clock delays the next step until idle or 1.5 s, whichever comes first.
- MESO backfill: a "Fill satellite to radar window" checkbox next to SYNC (off in lite mode) calls a new `SatelliteFetcher.fetch_history_window(mode, start, end, stride_s=300)`.

### 2c: Vehicles follow the clock

- `PositionTrack` in `core/vehicle.py` is a per-vehicle ring buffer of `(t, lat, lon)`, kept for 90 minutes and thinned to one point per 15 s. It covers every vehicle, local included. It is fed from `update_vehicle_obs` before the met-data check and kept in memory only.
- When the loop time is not `None`, each marker moves to its interpolated position at `t` (linear between the two nearest points, held for gaps under 2 minutes). Markers are drawn at 60 % opacity with a small clock badge, so a past position can't be mistaken for a live one. Vehicles with no fix before `t` are hidden. Follow mode is paused during the loop and resumes when the loop stops.
- Off by default in the radar drawer ("Include vehicles"), saved in session state.
- Tests: interpolation, thinning, the 90-minute cap, gap handling, and hide-before-first-fix.

### 2d: UI

- The radar drawer's playback row gains a SYNC toggle (on by default), a "Include vehicles" toggle, and a header showing loop time and age: `LOOP 21:04Z · −18m`.
- While looping, a small "LOOP 21:04Z" chip sits next to the UTC clock in the status pill, in the warn color. Clicking it returns to live.
- SYNC, vehicles and backfill are saved in session state.
- Done when: in VEHICLE mode with radar, CONUS satellite and the truck replay running, playing the loop moves all three together without drift. Stepping with `[` / `]` shows matching times in both drawers. Stopping snaps everything back to live.

## Step 3: Map right-click menu

Files: `ui/map/map_template.html` (`contextmenu` → bridge), `ui/map/bridge.py`, new `ui/widgets/map_context_menu.py`, `ui/app/main_window.py` (or the mixin that owns each action).

Status: built, pending manual pass. Notes on what was built, and where it differs from the design below:

- The audit was wrong about the old handler. Right-click never finished drawings or measurements. With a tool active it did nothing, and otherwise it opened the in-page private pin menu. Right-click with a tool active is unchanged (it still does nothing; double-click finishes a drawing). Python also ignores a menu request while a click mode is active.
- The private pin menu no longer opens on bare map. "Add private pin" (`window.stormAddPrivatePin`) and "Copy lat/lon" are in the Qt menu instead. Right-clicking an existing pin opens that pin's own menu, as a click does.
- `ui/widgets/map_context_menu.py` has `build_menu(MenuContext) -> [Item]` (pure) and `show_menu` (the `QMenu`). `MainWindowContextMenuMixin` (`ui/app/main_window_context_menu.py`) builds the context, shows the menu and runs `_ctx_<action>`, and each of those calls the existing toolbar handler or click mode.
- The page hit-tests drawings, cones, surface stations (canvas, nearest within 24 px) and hazards, in that order. Vehicles, annotations and the fork are DOM markers, so each reports its own right-click. The hazard hit test is now a shared `_stormHazardFeaturesAt`, used by left-click too. Feature items come first, under a disabled title, and the point items always follow, because a warning polygon can cover most of the screen.
- Place annotation leaves out the fork (a fixed screen overlay) and the storm cone (it needs two radar fixes). It places once and turns the tool off again. Draw ▸ enters the drawing mode with the first point placed. Measure from here turns on MEASURE with the first point placed.
- Route to here: in VEHICLE it routes from the GPS fix, the same path as a private pin's "Route to pin". In MONITOR and ARCHIVE there is no own fix, so it fills the destination. Route from here fills the start (`RoutingControls.set_point`). Both open the ROUTE drawer.
- VIEWER keeps the annotation and drawing items, because its toolbar allows local-only annotations. Only routing is left out, matching the hidden ROUTE button. ARCHIVE has no annotation tools and treats map objects as read-only.
- Delete asks for confirmation first, because the delete is published to the whole team. Move and Edit reuse the dialogs' flows (`_start_annotation_move`, `_start_drawing_move` and `_start_storm_cone_move` were pulled out of the edit dialog handlers).
- CLAMPS shows only once a CLAMPS sounding has been fetched and its launch point is within 150 km. Before that, the location is unknown. Follow is offered only for the vehicle that follow mode would track: this truck, or any vehicle when this truck isn't on the map.
- Tests: `tests/test_map_context_menu.py` (contents per mode and feature) and `tests/test_main_window_context_menu.py` (actions through the real handlers with a fake map, the context, the click-mode guard and a `QMenu` round trip). A scripted smoke run of the real window in VIEWER fired right-clicks in the page. A bare-map right-click and a vehicle right-click each reached the menu, the copy action filled the clipboard, and with MEASURE on no menu was requested.

- If a drawing or measurement is in progress, right-click finishes it, as today. Otherwise the map sends `on_context_menu(lat, lon, feature_json | "")` and a `QMenu` opens at the cursor.
- Empty map: Sounding here ▸ (HRRR / nearest RAOB / CLAMPS when in range), Route to here, Route from here, Measure from here, Place annotation ▸ (each type), Draw ▸ (front / polyline / polygon starting here), Nearest radar site ▸ (closest three, which sets the site), Copy lat/lon.
- On a feature: a warning, watch or MD offers "Open text"; an annotation, drawing or cone offers Edit / Move / Delete; a vehicle offers Follow, Center, Timeseries; a surface station offers "Sounding from nearest RAOB".
- Every item calls the same handler or enters the same click mode as its toolbar path, through `ClickModeController`. Items whose feature is flagged off, hidden in this mode, or read-only (VIEWER) are left out.
- Tests: menu contents per mode and feature type (pure function returning item specs); the handlers use a fake map widget.
- Done when: every item works in each mode where its toolbar equivalent is visible, and finishing a drawing or measurement with right-click is unchanged.

## Step 4: Command palette

Files: new `ui/widgets/command_palette.py`, new `ui/command_registry.py`, `ui/shortcuts.py`, `ui/app/drawer_manager.py`.

Status: built, pending manual pass. Notes on what was built, and where it differs from the design below:

- `ui/command_registry.py` has no Qt. Providers are called once when the palette opens (`refresh`), and each keystroke ranks that snapshot, so a provider can sort 160 radar sites by distance. A failing provider is logged and skipped. Recents (10, saved under `ui/palette_recent`) come first for an empty query and get a boost otherwise.
- Scoring is a subsequence match with prefix, first-character, word-start and consecutive-run bonuses and a small gap penalty. Spaces in the query are ignored. The consecutive bonus is set above the word-start bonus, because otherwise "radar" preferred "Radar reflectivity" (it jumped to the "r" of "reflectivity"). The category and the hidden keywords also match, at a penalty.
- The providers live in `MainWindowPaletteMixin` (`ui/app/main_window_palette.py`), not in `drawer_manager.py`. That mixin needs the window's buttons and controls, so `DrawerManager` is unchanged. `ShortcutRegistry` now keeps each bound callback (`callbacks`), and the Actions provider runs those. `Ctrl+K` (⌘K) is in `SHORTCUTS` as `palette`, so it appears in the `?` overlay.
- Toolbar parity: a toolbar button counts as available when it is shown, or when it is hidden only because it sits in the `⋯` overflow menu (`ResponsiveToolbar.overflowed()`). Buttons inside a drawer (ROUTE, MEASURE) use their own visibility. Drawer toggles that also have a key (R, S, H, O, K, N) are listed once, as "Open/Close … drawer" with the key shown, not again under Actions. Esc and the palette itself are left out.
- Providers: Actions, Drawers, radar sites (nearest first when this truck is on the map, through the same path as a station click) and products (through the product box, available ones only), satellite sectors (enabled ones), SPC day, the products of the current day and the watch / MD / warning / CWA toggles, vehicles (Center; Follow only where follow mode would track it), and places.
- Deployment locations: the file has 114k unnamed points (12.6k ranked), so listing them is no use. The palette offers the 5 ranked points nearest this truck (or home). Running one turns on PREV LOCS and flies there.
- Places use a new `search_places` (Nominatim, 5 results) on a daemon thread. A result is dropped if the query changed while it was in flight. "Online" means the last net check wasn't NO INTERNET.
- Small public methods were added so the palette goes through the same buttons as the drawers: `RadarControls.products` / `select_product`, `SatelliteControls.sector_buttons` / `select_mode`, `HazardControls.select_day` / `spc_products` / `select_spc_product` / `overlay_buttons`, `MapControls.choose_drawer_mode` / `choose_text_scale`.
- The search box claims Esc and the arrow keys (ShortcutOverride), so Esc closes the palette instead of running the window's Esc. Typing keys are already switched off while a text input has focus. Clicking anywhere else closes it. The command runs after the palette has closed, so a dialog it opens gets focus.
- Tests: `tests/test_command_registry.py` (scoring, providers, recents), `tests/test_command_palette.py` (keys, running, Esc, typing suppression, focus loss, debounced and stale place search) and `tests/test_main_window_palette.py` (mode filtering and toolbar parity per provider, with fake controls). A scripted run of the real window in VIEWER mode checked these:
  - Ctrl+K opened the palette, and typing "r" in it did not toggle RADAR.
  - "kfdr" + Enter switched the radar to KFDR, which was listed first the next time the palette opened.
  - "norman oklahoma" returned a Nominatim result, and Esc closed the palette.

- ⌘K / Ctrl+K opens a centered search box over the map: type to filter, ↑/↓ to move, Enter to run, Esc to close. It is registered in `SHORTCUTS`, so the `?` overlay and the typing suppression apply.
- `CommandRegistry` collects entries from providers:
  - Actions: every `SHORTCUTS` entry, with its key shown, plus actions that have no key (Reset layout, Screenshot, text scale, drawer mode).
  - Drawers: open or close each one.
  - Radar: sites ("KTLX Oklahoma City", nearest first when there is a GPS fix) and products.
  - Satellite sectors, SPC day and product.
  - Vehicles: center or follow.
  - Deployment locations.
  - Places, through the existing Nominatim geocoder, only when online and the query is 3 or more characters, debounced at 400 ms.
- Matching: a subsequence score, with prefix and word-start bonuses. The last 10 used entries are listed first. Entries that are unavailable in the current mode are left out, using the same rules as the toolbar buttons.
- Each entry runs through the same button or handler as its toolbar path.
- Tests: scoring, mode filtering, provider registration, and recent ordering.
- Done when: every toolbar action and every radar site can be reached from the palette in each mode, and typing in it triggers no shortcuts.

## Suggested order

1 → 2 → 3 → 4.

Step 1 goes first because Step 2 changes the radar, satellite and vehicle code, and it is much easier to change once it lives in its own files. Steps 3 and 4 are independent of each other and of Step 2, so they can run in parallel once Step 1 lands.
