# STORM UI/UX plan

Goal: make the Qt interface easier to read and safer to use mid-chase on a laptop: data you can trust at a glance, failures you can't miss, and no controls lost off-screen. No frontend rewrite.

## Findings from the audit

- Data age is not shown. Radar and satellite show only a frame time (`--:--Z`, `ui/controls/radar_controls.py:261`, `ui/controls/satellite_controls.py:135`).
- Open drawers stack under the toolbar in `MainWindow._layout_overlays` with no height cap or scrolling, so on a 768px-tall screen the lower ones go off-screen or behind the status pill.
- Radar, satellite, hazard and SFCOA errors share one 10px `status_msg_label`, overwrite each other and clear after 10 s.
- Text is small and often low contrast: 109 inline styles at 10px, 26 at 9px, 4 at 8px. The version label is `#4A5268` on `#0A0A0F`.
- Few shortcuts: Ctrl+D, Ctrl+E, Esc, plus the archive playback keys.
- The toolbar has up to 12 uppercase text buttons. Below about 1280px it overflows (`tb_x = max(0, ...)` just pins it left).
- Only geometry, layer order, NWS filters and scan sectors persist. Open layers, radar product and opacities do not.
- Sounding, measure, route pick, annotation placement and drawing all capture map clicks. They turn each other off through ad-hoc lambdas in `_init_toolbar`.
- 777 hard-coded hex colors in `ui/`. 57 inline `setStyleSheet` calls in `main_window.py` alone, despite `ui/theme.py`.
- `toggle_drawer` with its own `maximumHeight` animation is copied in 12 files, each slightly different.
- Overlay positions are refreshed by a 60 fps timer for 220 ms after every drawer toggle (`_start_layout_pulse`).

## Ordering

The groundwork items (theme tokens, a shared drawer base class) come first, because most visible fixes are small once they exist and painful before. Next is one shared per-layer status model, which delivers both data age and error badges. After that, a single click-mode controller, which shortcuts and Esc depend on. Session restore is last, because it needs the stable drawer and layer state the earlier steps create.

Each step is its own commit and leaves the app shippable. Run `pytest` and a manual pass in VEHICLE, MONITOR, VIEWER and ARCHIVE modes after each step.

## Step 1: Theme tokens (audit item 9)

Files: `ui/theme.py`, `ui/launch/styles.py`, then every file with inline colors, starting with `ui/app/main_window.py`.

- Add a small palette module (`ui/palette.py`): named tokens for background, surface, border, text, muted text, accent, and the status colors ok / warn / error / info, plus the mode colors (VEHICLE, MONITOR, VIEWER, ARCHIVE, ADMIN).
- Generate `DARK_THEME` from the tokens instead of literal hex.
- Replace inline `setStyleSheet` color strings with object names or dynamic properties (`status="ok|warn|error"`) styled once in the theme. Where an inline style must stay, read the token.
- Do it file by file. The mechanical pass is large but low risk. Leave the map HTML and the matplotlib dialogs (sounding, VAD, timeseries) out of this step.
- Done when: `grep -rn "#[0-9A-Fa-f]\{6\}" ui --include=*.py` only hits `palette.py`, `ui/sounding/theme.py` and colormap/data tables. The app looks pixel-identical.

## Step 2: Shared drawer base class and event-driven layout (audit items 10 and 11)

Files: the 12 `toggle_drawer` implementations in `ui/controls/*`, `ui/widgets/annotation_tools.py`, `ui/app/main_window.py`.

- Add `ui/widgets/drawer.py` with a `Drawer(QWidget)` base class that owns:
  - the open/close animation (measure the natural height at open time, animate `maximumHeight`, release the cap on finish)
  - an `is_open` state, separate from widget visibility
  - signals `opened`, `closed`, and `geometry_changed`, which fires on each animation value change and on content resize, replacing the per-class `content_resized`.
- Port each control to subclass `Drawer` and delete its copy of `toggle_drawer`. Port one simple drawer first (satellite), then radar, which has the `_expanded_height` special case, then the rest.
- Replace `_start_layout_pulse` with a coalesced relayout: `geometry_changed` → `_request_layout()`, which schedules one `_layout_overlays` call per event-loop turn (single-shot 0 ms timer guarded by a flag).
- Done when: no `toggle_drawer` definitions remain outside `drawer.py`, `_start_layout_pulse` is gone, and drawers open and close with no jitter.

## Step 3: Drawer stacking that fits the window (audit item 2)

Files: `ui/app/main_window.py` (`_layout_overlays`), `ui/app/overlay_geometry.py`, `tests/test_overlay_geometry.py`.

Status: done. As built (this replaced a first draft that used a scroll area and per-drawer pin buttons):

- `ui/app/drawer_manager.py` replaces the hard-coded pairs of toolbar buttons that closed each other (RADAR, HAZARDS, SATELLITE, SURFACE, MESO, SFCOA, ANNOTATE). Drawers are always closed through their button, so button side effects still run.
- Modes, set under MAP → DRAWERS and saved in QSettings: AUTO (ONE below 900px of map height, STACK otherwise), ONE, STACK.
- Instead of a scroll area, STACK closes the oldest unpinned drawers when the stack would pass the status pill or archive bar (`drawers_to_close` in `overlay_geometry.py`). The newest drawer is never closed. This avoids scrolling a column of controls over the map.
- Pins: right-click a toolbar button → "Keep open when other drawers open". Pinned buttons get an accent outline. PREV LOCS and SOUNDING are pinned by default, because closing them hides the deploy-location layer or exits sounding click mode. ROUTE and LANDCOVER (opened from MAP) are never auto-closed.
- Map-click exclusivity (SOUNDING / MEASURE / ANNOTATE) is unchanged; that is Step 6.

- Done when: any combination of open drawers at 1366×768 keeps every control reachable, and the geometry tests pass.

## Step 4: Layer status model, data age and error badges (audit items 1 and 3)

Files: new `ui/layer_status.py`, `ui/app/main_window.py`, `ui/widgets/layer_order_pill.py`, `ui/controls/radar_controls.py`, `ui/controls/satellite_controls.py`, `ui/controls/sfcoa_controls.py`, `ui/controls/hazard_controls.py`, `ui/controls/surface_controls.py`.

Status: built, pending manual pass. Notes on what was built:

- States are `off`, `loading`, `ok`, `warn`, `stale`, `error`. `warn` only colors the drawer time label amber. The toolbar dot appears at `stale` (amber) and `error` (red).
- A layer is active when one of its map layer keys is on (the existing `_set_layer_active` calls feed `LayerStatusBoard.set_source_active`). Satellite is the exception: it is active while a mode is selected, because its pill key follows the drawer. Turning a layer off clears its data and errors.
- Hazards and surface obs have no valid time, so their age is the time since the last successful fetch. In ARCHIVE that age is not tracked; radar and satellite age is measured against playback time.
- Mesoanalysis and SFCOA frames are picked by the user, so they track errors only (no staleness thresholds). Only the radar and satellite drawers show `HH:MMZ · age`. The hazard, surface and layer-order-pill files were left unchanged.
- The 10 s error-clear timers and the radar/hazard/satellite error writes to `status_msg_label` are gone.

- `LayerStatus(QObject)`: one instance per data layer (radar, satellite, hazards, SFCOA, mesoanalysis, surface obs). It holds `valid_time`, `last_success`, `last_error`, an expected refresh interval and a `state` (`off`, `loading`, `ok`, `stale`, `error`). It emits `changed`. A single 15 s timer re-evaluates staleness.
- Staleness thresholds are per layer, in `config.py`, e.g. radar warn at 8 min and stale at 12 min, satellite warn at 10 min and stale at 20 min. Archive mode measures age against the time controller, not the wall clock.
- Wire the existing success and error slots (`_on_radar_error`, `_on_scan_decoded`, `_on_satellite_frames_updated`, `_on_hazard_error`, `_on_spc_received`, etc.) to update the status objects instead of writing `status_msg_label` directly.
- Display:
  - Time labels in each drawer show `14:32Z · 6m`, colored by state.
  - Toolbar buttons get a status dot (dynamic property `layerState`, styled in the theme). It is amber when stale and red on error, and stays until the next success.
  - The tooltip on the dot shows the last error and when it happened. Clicking a red dot opens that drawer.
  - `status_msg_label` keeps transient progress messages ("loading KTLX…") only.
- Tests: unit-test the state transitions and thresholds with an injected clock.
- Done when: unplugging the network turns active layers amber and then red on their toolbar buttons within one refresh cycle, and they recover on their own when the network returns.

## Step 5: Readability and text scale (audit item 4)

Files: `ui/palette.py`, `ui/theme.py`, `ui/app/main_window.py` (status pill), drawer controls.

Status: built, pending manual pass. Notes on what was built:

- Text tokens folded into one scale: TEXT, TEXT_BRIGHT, TEXT_SECONDARY, TEXT_LABEL, TEXT_MUTED (≥4.9:1 on every surface), TEXT_DISABLED (~3:1, disabled controls and inactive rows only). Background, border and warn variants are not folded yet.
- Floor: nothing below 10px in `ui/`. Drawers, the vehicle pills, tooltips and the archive bar are 11px. The status pill is styled in the theme through `pillRole` / `pillStatus` properties (`_set_pill_status`): 11px text, 12px badges (GPS, AWS, NET, mode) and clock. Error text in the pill uses `error_soft`, because `error` is only 4.0:1 on raised surfaces. The matplotlib dialogs (sounding, VAD, timeseries) are unchanged.
- `ui/text_scale.py` wraps `QWidget.setStyleSheet` at startup (`main.py`) and scales every `font-size: Npx`, so inline styles and the theme scale together and a change applies live. The application font is scaled too. Font sizes must stay in stylesheets (not `QFont` point sizes or rich-text spans) to scale.
- The `wide` toolbar property is gone; 115 % replaces it.
- Fixed widths that clipped scaled text (meso grid, SFCOA refresh, annotation buttons, route labels) are now minimums.

- With tokens in place, raise the floor: status pill and drawer body text 11px minimum (12px for GPS, AWS, clock and mode badge), no text below 10px anywhere. Muted-text tokens must reach a 4.5:1 contrast ratio against the background; fix the version label.
- Add a UI text scale setting (100 / 115 / 130 %) in the MAP drawer, stored in QSettings, applied by generating the stylesheet with scaled font sizes. Replace the current `wide` property hack with the same mechanism.
- Optional follow-up: a high-contrast day palette, which becomes a token swap after Step 1.
- Done when: the status pill is readable at arm's length on a laptop at full brightness outdoors, and 130 % scale doesn't clip any drawer (relies on Step 3).

## Step 6: Single map click-mode controller (audit item 8)

Files: new `ui/click_mode.py`, `ui/app/main_window.py`, `ui/map/widget.py`, routing, sounding, measure, annotation and drawing code paths.

Status: built, pending manual pass. Notes on what was built:

- Modes: `sounding`, `measure`, `route_pick`, `annotation:<type>`, `drawing:<type>`, `storm_motion_fix`, `asos_bbox`. Each kind registers an exit callback in `MainWindow._init_click_modes`; plain map clicks go to `measure`, `annotation`, `drawing` and `storm_motion_fix`. Sounding, route pick and the ASOS box still arrive on their own map signals (the JS routes them), but only while their mode is on.
- Exit callbacks are told the next mode, so switching tools inside the ANNOTATE drawer keeps the new tool highlighted. The controller drops a mode before calling its exit callback, so a callback that unchecks a button whose handler calls `exit` again is harmless.
- Opening a drawer no longer cancels a mode. Only entering another mode does. Before, opening ANNOTATE turned off MEASURE and SOUNDING, and opening ROUTE turned off MEASURE.
- Prompts moved from `status_msg_label` to an on-map banner (`QLabel#clickModeBanner`) under the drawer stack. Drag-to-move prompts for annotations, drawings and cones use the same banner without the Esc hint.
- Esc exits the mode, or else closes the newest open drawer (`DrawerManager.close_newest`), pinned or not.
- Finishing a polyline or polygon now clears the tool highlight. Before, the button stayed highlighted after the drawing mode had ended.
- Tests: `tests/test_click_mode.py` (controller) and `tests/test_main_window_click_modes.py` (the real MainWindow handlers with a fake map widget).

- `ClickModeController` holds the one active mode (`none`, `sounding`, `measure`, `route_pick`, `annotation:<type>`, `drawing`, `storm_motion_fix`, `asos_bbox`). Entering a mode exits the previous one through its registered exit callback. It emits `mode_changed`.
- Replace the cross-cancelling lambdas in `_init_toolbar` and the scattered `if self.btn_x.isChecked()` checks in `_on_map_click` with dispatch on the current mode.
- Generalize `_set_placement_prompt` into an on-map banner driven by `mode_changed` ("Click map for HRRR sounding · Esc to cancel").
- Esc exits the current mode first, then closes the topmost drawer.
- Done when: `_on_map_click` is a single dispatch, and no two click modes can be active at once (add a test with a fake map widget).

## Step 7: Keyboard shortcuts (audit item 5)

Files: new `ui/shortcuts.py`, `ui/app/main_window.py`, `ui/controls/archive_controls.py`.

Status: built, pending manual pass. Notes on what was built:

- `SHORTCUTS` in `ui/shortcuts.py` is the only place keys are assigned. Each entry lists the modes it applies to, and an import-time check rejects a key bound twice in one mode. Live: Space loop, `[` / `]` radar frame. ARCHIVE: Space playback, `[` / ← / A step back, `]` / → / D step forward, Home / End jump. O (surface obs) is live only, because ARCHIVE hides SURFACE.
- `ArchiveControls` no longer installs its own `QShortcut`s; its step and skip handlers are public (`step_back`, `toggle_play`, ...) and bound by `MainWindow._init_shortcuts`. `RadarControls` gained `toggle_loop()` and `step_frame(delta)`, which do nothing until there are frames.
- Toolbar keys click the button only if it isn't hidden and is enabled, so a feature-flagged-off or mode-hidden button ignores its key. M works while the MAP drawer is closed.
- Keys that type text (no Ctrl/Alt/Cmd, anything but Esc) are disabled while a text input has focus, through `QApplication.focusChanged`.
- Ctrl+S is ⌘S on macOS (Qt's mapping). Tooltips and the `?` overlay show ⌘ for modifier keys and plain names (Esc, Home, ←) otherwise.
- Esc order: close the `?` overlay, then exit the click mode, then close the newest drawer.
- Tests: `tests/test_shortcuts.py` (table, typing suppression, tooltips) and `tests/test_main_window_shortcuts.py` (the real `_init_shortcuts` in live and archive mode).

- One table maps actions to keys: R radar, S satellite, H hazards, V vehicles, O surface obs, K sounding mode, M measure, N annotate, Space radar loop play/pause (live) or playback (archive), `[` / `]` previous/next radar frame, Ctrl+S screenshot, `?` shortcut overlay. Ctrl+D and Ctrl+E stay.
- Register shortcuts with `Qt.ShortcutContext.WindowShortcut` and disable them while a text input has focus (routing address box, dialogs), so typing an address doesn't toggle layers.
- Move the archive shortcuts into the same table so Space and the arrow keys don't conflict between modes.
- The `?` overlay is generated from the table. Tooltips append the key ("Show/hide radar controls (R)").
- Done when: every shortcut works in each mode where its button is visible, and typing in the routing box triggers none.

## Step 8: Compact, responsive toolbar (audit item 6)

Files: `ui/app/main_window.py` (`_init_toolbar`, `_layout_overlays`), `ui/theme.py`, `ui/launch/icons.py`.

Status: built, pending manual pass. Notes on what was built:

- `ui/widgets/responsive_toolbar.py`: `ResponsiveToolbar` owns the buttons, groups and separators. Tiers are `full` (icon + label), `icons` (icon only) and `overflow` (icon only, MESO / SFCOA / PREV LOCS in the `⋯` menu). `fit(width)` measures each tier's real width (cached until the buttons, icon size or style change) and applies the widest that fits; `_layout_overlays` calls it with the map width minus margins.
- Buttons dropped for a mode use `set_available(btn, False)` instead of `hide()`, so tier changes don't bring them back. A group with no visible buttons drops its separator; with nothing to overflow (ARCHIVE), `icons` is the narrowest tier.
- Tooltips always start with the label ("RADAR: Show/hide radar controls (R)"), so icon-only buttons still name themselves.
- `⋯` menu entries click the real button, so drawer bookkeeping and side effects run. MESO and SFCOA status dots are copied onto `⋯` while they sit in the menu. Overflowed buttons can't be right-clicked, so the `⋯` menu ends with a "Keep open when other drawers open" section that has a Pin toggle for each of them (`set_pin_handlers`).
- Icons are stroked SVGs in `ui/launch/icons.py` (`toolbar_icon`), dark on the accent background when checked, sized 14px × text scale.
- Measured offscreen with every feature flag on: about 1030 px full, 400 px icon-only, 365 px overflow. So 1024 px windows get icon-only, and `overflow` only kicks in for much narrower map areas.
- Tests: `tests/test_responsive_toolbar.py` (tier choice, unavailable buttons, separators, menu, and a 1024 px fit at 130 % for each mode and flag combination).

- Group buttons by purpose, with separators: Data (RADAR, SATELLITE, HAZARDS, SURFACE, MESO/SFCOA) | Analysis (SOUNDING) | Team (VEHICLES, PREV LOCS, ANNOTATE) | MAP.
- Add small icons next to the labels. Below a width breakpoint, drop to icon-only, with the label in the tooltip (the shortcut key from Step 7 is there too). Below a second breakpoint, low-use buttons (PREV LOCS, MESO, SFCOA) move into a `⋯` overflow menu.
- Breakpoints are computed from the measured toolbar width, not fixed pixel values, so feature-flag changes don't break them.
- Done when: the toolbar fits without overflow from 1024px wide upward in every mode and feature-flag combination.

## Step 9: Restore last session state (audit item 7)

Files: `ui/app/main_window.py`, the `Drawer` base class, the controls.

Status: built, pending manual pass. Notes on what was built:

- `ui/session_state.py` stores one JSON dict per launch mode under `session/vehicle|monitor|viewer|archive` (in the `NSSL/STORM` settings, like geometry and pins). Pins, drawer mode, text scale, layer order and NWS filters already persisted on their own, so they are not duplicated.
- The controls have `save_state()` / `restore_state()`; there is no `Drawer`-level state. Restore goes through the normal buttons and signals, so fetchers start as if clicked.
  - Radar: product, opacity and "show data". The site is restored only in MONITOR; the other live modes pick the nearest site from GPS. The opacity slider (10–100 %, default 75 %) was added to the radar drawer for this step. It sets `window._stormRadarOpacity`, which both the PNG layer and the WebGL radar layer read instead of their hard-coded 0.75.
  - Satellite: CONUS/MESO and opacity. The band is always visible (ABI channel 2, live and archive), so there is no band to save. A MESO sector that isn't known yet is applied when it arrives, and falls back to CONUS if it is gone.
  - Hazards: SPC day and product, watches, MDs, NWS warnings, CWA. Surface: OK/WTM/KS/CO/NE/SD and "show plots". ASOS is left out, because it needs a box drawn each session.
  - MESO / SFCOA: active products. Their fetchers start at restore if products were saved, and the products turn on once the fetcher lists them.
  - Open drawers are reopened oldest first, so ONE mode keeps the newest. SOUNDING stays closed, because opening it enters sounding click mode. The vehicle panel already comes back with the window state.
- Restore runs on the event-loop turn after `_apply_launch_prefs`. It only turns things on, so launch-dialog layers stay on. The two exclusive choices (satellite sector, SPC product) are skipped when the launch dialog set them. ARCHIVE skips radar, surface, MESO, SFCOA, PREV LOCS and ANNOTATE.
- Saved in `closeEvent`, but only after a restore has run, so quitting mid-startup doesn't overwrite the last session with defaults.
- MAP → RESET LAYOUT closes every drawer, restores the default pins, and sets AUTO and 100 % text. Layers are left as they are.
- Tests: `tests/test_session_state.py` (storage, each control's round trip and pending cases, DrawerManager `open_keys` / `close_all` / `reset`, and MainWindow's restore with stand-in controls: launch-pref precedence, MONITOR-only site, ARCHIVE isolation).

- Each drawer and control exposes `save_state() -> dict` / `restore_state(dict)` for the settings worth keeping: open/pinned state, radar product and opacity, satellite mode/band and opacity, enabled hazard layers, surface sources, SFCOA products, text scale and drawer mode.
- Save to QSettings on close, per launch mode (`session/vehicle`, `session/monitor`, ...), so ARCHIVE never inherits live layers.
- Restore after the startup phases finish (after `_start_post_startup_fetchers`), so restored layers don't race the launch dialog prefs. Launch-dialog choices win where they overlap.
- Add a "Reset layout" action in the MAP drawer.
- Done when: quitting with radar velocity at 70 % opacity plus hazards open brings back the same view on the next launch, in the same mode.

## Not planned

- Moving Qt panels into the web map (see the performance plan; no UX gain on its own).
- A light theme as a goal in itself. A high-contrast day palette is possible after Step 5 if it's wanted.

## Suggested order

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9.

If the field-critical fixes are needed sooner, a minimal version of Step 4 (age labels and toolbar error dots using inline styles) can ship before Steps 1 to 3 and be cleaned up when the tokens land.
