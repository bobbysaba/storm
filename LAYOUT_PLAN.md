# STORM docked layout plan

> **Decision (2026-11-11): docked was tried and not adopted.** Classic stays the only layout. Only the loop timeline from step 4 was kept, now a classic feature: it sits at the true bottom centre (720 px wide at most), narrows to clear the corner overlays, and when even its narrowest won't fit it rises above the status pill. Its Vehicles button was dropped, so VEH stays in the radar drawer. The radar drawer's playback row keeps only SYNC and VEH while the timeline exists, and the loop chip is hidden. Everything else built in steps 0–6 was removed: the layout switch and `--layout`, the layers panel, the tools bar, the right column and gear, and the compact / embedded drawer modes. The rest of this file is kept as the record of what was designed and built.

A second layout, "docked", built next to the current one ("classic"). Classic stays the default and keeps working unchanged until docked is formally adopted. Then classic is removed in one cleanup step (see "Adopting docked").

## Why

The audit (VIEWER screenshots, `_init_toolbar`, `DrawerManager`) found:

- The toolbar mixes four kinds of control behind identical buttons: data layers (RADAR, SATELLITE, HAZARDS, SURFACE, MESO, SFCOA, PREV LOCS), click tools (SOUNDING, ANNOTATE, and MEASURE / ROUTE inside MAP), a panel (VEHICLES) and app settings (MAP holds drawer mode, text scale and reset layout).
- A lit button means "drawer open", not "layer on the map", and buttons have three looks (filled, outlined when pinned, plain). Nothing on screen answers "what am I looking at?"
- Drawers stack across the top centre of the map, where the storm usually is. Three open drawers cover about 220 px. Drawer modes (AUTO / ONE / STACK), pins and `enforce_fit` exist to manage that.
- One layer's controls are spread around: opacity in its drawer, draw order in the LAYERS pill (bottom left), legend bottom right, health as a dot on the toolbar button. Radar and satellite each have a playback row, plus SYNC / VEH, though `LoopSync` already drives them as one loop.

## Target layout

| Region | Contents | Replaces |
|---|---|---|
| Left: layers panel | One row per layer: eye, name, age, health dot, chevron. The chevron expands that layer's existing controls in place. Collapses to a 44 px rail of icons with health dots. Floats over the map. | RADAR … PREV LOCS buttons, their drawers' placement, LAYERS pill, status dots, drawer modes and pins |
| Top centre: tools bar | Sounding, Annotate, Measure, Route, ⌘K. One tool active at a time. The existing mode banner sits under it ("Click the map for a sounding · Esc to exit"). | SOUNDING, ANNOTATE buttons, MEASURE / ROUTE inside MAP |
| Bottom: timeline | Step, play, scrubber with radar and satellite frame ticks, loop time and age, Live, speed. | Radar and satellite playback rows, SYNC / VEH, the loop chip |
| Right: team | Vehicle panel and vehicle detail, under the nav pill. | VEHICLES button and centre-stacked vehicle panel |
| Bottom left: status | The status pill as today, under the layers panel. | — |
| Gear menu | Text scale, layout switch, reset layout. | Settings half of the MAP drawer |

The command palette, map right-click menu, click-mode controller, `LayerStatusBoard`, `LoopSync` and session restore carry over unchanged in both layouts.

## Rules for this plan

- Classic must behave exactly as it does today. Every docked change is reached only through `layout == "docked"` checks or docked-only modules. The full test suite runs against classic unchanged, and new tests cover docked.
- Each step is a commit that leaves both layouts shippable. Docked may be incomplete between steps (it's opt-in), but must not crash in any of VEHICLE, MONITOR, VIEWER and ARCHIVE.
- Reuse, don't fork. The controls classes, toolbar buttons and their signal wiring are built the same way in both layouts. Docked only re-hosts and re-flows them. That keeps one copy of every handler and the side effects tied to toggling a button (lazy MESO / SFCOA init, satellite mode on open, sounding click mode, PREV LOCS visibility).
- New logic goes in its own module, testable without Qt where possible, like phase 2.

## Key design decision: the hidden toolbar stays the backbone

In docked, `ResponsiveToolbar` and all `btn_*` buttons are still created and wired, then the toolbar is hidden. Docked widgets are views onto those buttons:

- Expanding a layer section calls `btn.setChecked(True)`. Collapsing it unchecks. So "section expanded" means exactly what "drawer open" means today, and every existing `toggled` handler still runs.
- A tools-bar button mirrors its hidden toolbar button both ways.
- Palette, shortcuts and session restore keep talking to `btn_*` and keep working.

Without this, about 40 call sites that reference `btn_*` would each need a docked branch.

## Step 0: The layout switch

Files: `runtime_flags.py`, `main.py`, `ui/app/main_window.py`, `ui/controls/map_controls.py`, new `ui/layout_mode.py`.

Status: built, not committed. Notes on what was built:

- `ui/layout_mode.py` (`load`, `save`, `current`), `RuntimeFlags.layout` and `--layout`. `MainWindow` reads `self._layout_mode` before `_init_toolbar`.
- `_layout_overlays` dispatches. The old body is now `_layout_overlays_classic` with its text unchanged. `MainWindowDockedMixin` (`ui/app/main_window_docked.py`) hides the toolbar in `_init_docked_layout` and, for now, `_layout_overlays_docked` calls the classic body. So drawers opened from shortcuts or the palette still stack where the toolbar would be.
- The MAP drawer has `LAYOUT: CLASSIC | DOCKED`, showing the saved choice. Picking one saves it and shows a "from the next launch" tooltip. The gear menu doesn't exist yet, so in docked the switch is reached through the palette ("Classic layout" / "Docked layout"), which runs the same buttons.
- The palette's availability checks use `btn.isHidden()`, which a hidden parent doesn't set. So in docked every drawer, action and shortcut is still listed and works.
- There is no separate geometry snapshot test: the classic body wasn't edited, only renamed, and the full suite passes.
- Tests: `tests/test_layout_mode.py` (setting, override, parser, MAP row, dispatch, toolbar hidden) and a palette check for the two layout commands. A scripted run of the real window in VIEWER with `--layout classic` and `--layout docked` showed the MAP row in classic, the toolbar hidden in docked, RADAR opening through its hidden button, and the layout commands in the palette.

- `ui/layout_mode.py`: `CLASSIC`, `DOCKED`, `current()`, `load(settings)` and `save(settings, mode)` using QSettings key `ui/layout`, default `classic`.
- `--layout {classic,docked}` CLI flag that overrides the setting for one run without saving it. Stored on `runtime_flags.FLAGS.layout`.
- The layout is chosen once at startup. Switching means a restart ("Takes effect next launch"), which avoids re-parenting live widgets and keeps classic's code path untouched. A live switch can come later if it's wanted.
- The MAP drawer gains `LAYOUT: CLASSIC | DOCKED`. In docked the same control is in the gear menu.
- `MainWindow.__init__` reads the mode into `self._layout_mode`. `_layout_overlays` becomes a dispatcher: classic calls the existing body (renamed `_layout_overlays_classic`, unchanged), docked calls `_layout_overlays_docked` in the new mixin.
- Tests: the flag and setting resolve correctly, and a classic window's overlay geometry matches a snapshot taken before the change.
- Done when: `--layout docked` starts a window that is classic with the toolbar hidden, and nothing else has changed.

## Step 1: Docked shell and layers panel frame

Files: new `ui/app/main_window_docked.py` (`MainWindowDockedMixin`), new `ui/widgets/layer_panel.py`, `ui/widgets/drawer.py`, `ui/app/drawer_manager.py`.

- `LayerPanel(QWidget)`: header (title, collapse button), a `QScrollArea` of `LayerSection`s, and a footer slot for the status pill. Width 300 px expanded, 44 px as a rail. It floats over the map and doesn't resize it.
- `LayerSection`: header row (eye, icon, label, age, health dot, chevron) and a body that hosts one existing drawer.
- `Drawer.set_embedded(True)`: no open / close animation, no max-height cap, and `toggle_drawer` just shows or hides the widget. Classic never calls it.
- Sections are created from the same `(key, drawer_attr, btn_attr)` list `_init_drawer_manager` uses, in this order: Radar, Satellite, Hazards, Surface, Mesoanalysis, SFCOA, Previous locations, Basemap (landcover and satellite basemap toggles moved out of MAP). Missing attributes (feature flags, ARCHIVE) skip their section as today.
- `DrawerManager` in docked is forced to `stack` with fit enforcement off, because the panel scrolls. Pins are ignored and their menu isn't installed.
- Rail mode: clicking an icon opens that one section as a flyout beside the rail and closes it on outside click. The panel starts as a rail when the map is under 1,100 px wide.
- Tests: section list per mode and flag set; expand ↔ `btn.isChecked()` both ways; embedded drawers ignore animation; rail flyout opens and closes.
- Done when: every layer's controls open from the panel in all four modes and behave as they do from the toolbar. Layout inside sections may still be too wide (fixed in step 2).

Status: built, not committed. Notes on what was built:

- `ui/widgets/layer_panel.py` has `LayerPanel` and `LayerSection`. A header click calls `button.click()`, so `clicked` handlers run too. The header and rail icon follow `button.toggled`. A section without a button (Basemap) expands locally.
- Rail flyout rules: expanding a section from anywhere (rail icon, shortcut, palette, session restore) opens the flyout, which lists only the expanded sections. A click outside the panel hides the flyout but leaves the sections open, so PREV LOCS keeps its layer on. Clicking the icon of an open section whose flyout is hidden shows the flyout again. A second click closes the section. While the flyout is open the panel watches clicks app-wide, ignoring clicks while a popup (a combo list) is open.
- `Drawer.set_embedded(True)`: open and close only show and hide the drawer, there's no height cap, `occupies_space()` is False (so the classic stack skips it) and `content_changed` emits `geometry_changed` straight away. A theme rule drops the drawer's own pill (`#floatingToolbar[embedded="true"]`). The drawers keep the `floatingToolbar` object name so their buttons keep their styling.
- `DrawerManager.lock_mode("stack")` (ignores `set_mode`, not saved) and `set_fit_enabled(False)`. `ResponsiveToolbar.is_available` / `icon_key` let the panel skip buttons this mode drops (ARCHIVE) and reuse the toolbar icons. Header icons stay light when expanded (the toolbar's checked icon is dark, for the accent fill).
- `_layout_overlays_docked` runs the classic pass first, so every overlay not yet docked keeps its place, then puts the panel top left above the LAYERS pill and status pill. The rail rule is applied once, on the first layout pass after the window is shown. It uses the restored window size, so a saved 1024 px window starts as a rail.
- Until step 2, sections are as wide as their widest row and the panel scrolls sideways, so the chevron sits left of the icon. Sounding, annotate, MAP and route drawers still stack top centre and can sit under the panel's right edge.
- Tests: `tests/test_layer_panel.py` (button sync, header clicks, local section, rail and flyout, outside click, height), embedded cases in `tests/test_drawer.py`, lock and fit cases in `tests/test_drawer_manager.py`, and docked host tests in `tests/test_layout_mode.py` (section list, ARCHIVE drop, Basemap move, rail rule). A scripted run of the real window in VIEWER covered docked expanded, rail and flyout, Basemap, and classic unchanged.

## Step 2: Re-flow controls for a narrow column

Files: new `ui/widgets/flow_layout.py`, each `ui/controls/*_controls.py`, `ui/widgets/annotation_tools.py`.

The controls are laid out in wide `QHBoxLayout` rows (radar row 1 is about 600 px). In docked they need to fit 300 px.

- `FlowLayout`: the standard Qt wrapping layout (items wrap to the next line at the width limit).
- Each controls class gets `set_compact(True)`, called once before first show in docked. It moves each row's widgets into a `FlowLayout` (or a vertical stack where wrapping would read badly). It doesn't touch signals or state. Classic never calls it.
- In docked, the radar and satellite playback rows are hidden (`set_playback_visible(False)`), since the timeline (step 4) replaces them. Until step 4 they stay visible.
- Per control, in order of use: radar (station, product, opacity, VAD, show data), satellite (sector buttons, opacity), hazards (day, products, overlay toggles, legend under them), surface (networks, show plots), deploy locations, SFCOA, mesoanalysis (product grids already wrap), landcover.
- Legends move into their layer's section: hazard legend under the hazard toggles, radar colour bar under radar opacity. The classic bottom-right LEGEND pill is hidden in docked.
- Tests: each control at 300 px has `sizeHint().width() <= 300` in compact mode, and every widget that existed before compact mode is still reachable and wired.
- Done when: no section scrolls sideways, and the four-mode pass shows every control usable.

Status: built, not committed. Notes on what was built, and where it differs from the design above:

- `ui/widgets/flow_layout.py` has `FlowLayout`, `reflow(row)`, `compact_rows(root, max_width)`, `stack_groups(grid)` and `clear_layout`. A flow's size hint is its one-line width capped at 256 px, not its widest item. Otherwise a parent that sizes a row to its hint (most drawer columns) left it about 160 px wide and every item wrapped.
- `Drawer.set_compact()` is generic, so most classes needed no code of their own. It walks the drawer's plain `QWidget` / `QFrame` rows and nested layouts (not custom widgets) and re-flows every horizontal row wider than `COMPACT_CONTENT_WIDTH` (256). Stretches are dropped, thin divider rules hidden, and a nested layout wraps as one item. Radar, satellite, surface and deploy locations use only this. Deploy locations' rows already fit.
- Per-class additions: hazards re-flows its legend row (empty at startup, so the width rule skips it) and points `_legend_layout` at the new flow, so `update_legend` keeps working. Mesoanalysis, SFCOA and the landcover legend become stacked groups: each group title on its own line with its buttons wrapping underneath. A fixed 3-column grid didn't fit once the theme's button padding applied. SFCOA rebuilds its grid on every listing, so in compact mode `set_products` swaps in an empty grid first and regroups after.
- The design's step-2 legend items aren't done: there is no radar colour bar to move yet, and the bottom-right LEGEND pill stays. They fit better with step 3's layer rows.
- `LayerPanel.preferred_height` measures `totalHeightForWidth` at the column width, because wrapped rows are taller than their size hint.
- The playback rows stay until step 4. Radar's wraps onto three lines (VEH ends up alone on the last one).
- Tests: `tests/test_flow_layout.py` covers wrapping, `reflow` on a row widget and a nested row, `stack_groups`, every controls class at panel width with no widget lost, the hazard legend after compact, an SFCOA listing twice, and classic drawers untouched. The tests stub radar's availability probe and keep their controls alive for the module. Collecting a `SatelliteControls` mid-run fires its event filter on a half-destroyed object, which aborts pytest. That filter was already there and the app never deletes these widgets. A scripted VIEWER run showed radar, hazards, satellite, surface, SFCOA and Basemap wrapping inside the panel, and classic's toolbar and drawer area pixel-identical to before.

## Step 3: Layer rows (eye, age, health, order)

Files: new `ui/layer_adapters.py`, `ui/widgets/layer_panel.py`, `ui/app/main_window_docked.py`, `ui/widgets/layer_order_pill.py` (read-only reuse of `load_layer_order` / `save_layer_order`).

- `LayerAdapter` (no Qt): `key`, `label`, `is_on()`, `set_on(bool)`, `status_name`. `set_on(False)` remembers the layer's configuration and `set_on(True)` restores it. Each layer already has its own idea of "on":

  | Layer | `is_on` | Eye off | Eye on |
  |---|---|---|---|
  | Radar | "show data" checked | uncheck "show data" | re-check it |
  | Satellite | a sector is selected | remember sector, clear it | reselect it |
  | Hazards | any SPC product, watch, MD, warning or CWA toggle on | remember the set, turn all off | restore the set (Day 1 outlook if none saved) |
  | Surface | any network on and plots shown | uncheck show plots | re-check it |
  | Mesoanalysis, SFCOA | any product active | remember products, clear | restore |
  | Previous locations | `btn_prev_locs` checked | uncheck | check |

- Age and health come from `LayerStatusBoard` (`age_text`, `state`, `tooltip`). The section listens to the same signals `LayerStatusDot` uses. A red dot expands the section, as clicking a red toolbar dot opens the drawer today.
- Order: dragging a section header reorders groups. Each group owns a block of `DEFAULT_LAYERS` keys (Hazards owns `cwa` … `nws_warnings`, Surface owns the networks). A drag moves the block, keeps the order inside it and calls `save_layer_order`, so classic and docked share one saved order. The finer per-key order stays editable in classic's pill. Whether docked needs it is an open question.
- The rail shows the eye state as icon brightness, plus the health dot.
- Tests: each adapter's off / on round trip restores the exact config; group drag produces the expected key order; status changes reach the row.
- Done when: the panel answers "what's on, how old, is it healthy" with every section collapsed, and the LAYERS pill is hidden in docked.

Status: built, not committed. Notes on what was built, and where it differs from the design above:

- `ui/layer_adapters.py` holds `LayerAdapter` (capture / clear / restore around each drawer's own buttons, so every handler runs) and one builder per row of the table, plus `SECTION_LAYERS`, `sort_sections` and `regroup`. The drawers gained small public helpers instead of the adapters reaching into private widgets: `RadarControls.set_data_enabled`, `SurfaceControls.set_plots_visible` / `any_source_on`, `HazardControls.active_spc_product` / `clear_spc_product`, and `product_buttons` / `select_products` on mesoanalysis and SFCOA.
- An eye with nothing to restore expands the section so the user can choose there: Surface with no network picked, or Mesoanalysis / SFCOA when nothing was on before. Hazards falls back to the Day 1 outlook and satellite to CONUS, as designed.
- Previous locations: its markers show exactly while its drawer is open, so its eye and its expand state are the same button. The Basemap section also got an eye (land cover / satellite basemap, remembering which).
- ARCHIVE: radar has no eye, because "show data" is hidden there and radar follows the archive scans.
- Section order is the draw order, top of the map first, read from the shared saved order at startup. Previous locations owns no map layer key, so it sits on top and can't be dragged. A drag refills the slots the moved groups use, keeps each group's internal order and every other key's slot (route, annotations, scan sectors), saves it, applies it to the map and hands it to the hidden pill, whose copy `closeEvent` saves.
- Eyes redraw on `_set_layer_active` (coalesced to one pass per event-loop turn), on the section's button and status, and on surface's "show plots", which changes no layer key.
- Legends: the hazard legend is already under the hazard toggles (step 2). The bottom-right map legend is the HTML roads legend inside the map, not a data legend, so it stays. There is still no radar colour bar to move. Both are left out of this step.
- Tests: `tests/test_layer_adapters.py` (order mapping, and each adapter's off / on round trip on the real controls), new panel tests in `tests/test_layer_panel.py` (eye, expand on nothing to restore, age and dot, red dot expands, rail dimming, move / set order, drop index) and a docked-host test in `tests/test_layout_mode.py` (startup order, a drag saves, re-stacks the map and updates the pill). A scripted VIEWER run in docked showed the rows, eyes switching radar / satellite / hazards, the reorder and the rail; the LAYERS pill was hidden.

## Step 4: Timeline bar

Files: new `ui/widgets/loop_timeline.py`, `ui/app/main_window_loop.py`, `ui/controls/radar_controls.py`, `ui/controls/satellite_controls.py`.

- `LoopTimeline(QWidget)`: step back, play / pause, step forward, a scrubber, the loop time label (`21:04Z · −18m`, warn colour while looping), Live, speed menu, and a small "Vehicles" toggle (the old VEH).
- It holds no loop state. It calls the driving drawer's existing methods (`toggle_loop`, `step_frame`, `set_frame`, `jump_to_latest`, speed) through `LoopSync`'s driver, and redraws from `LoopSync`'s time signal. So it drives radar, or satellite when radar is off, exactly like phase 2.
- Ticks: radar scan times on the upper edge, satellite frame times on the lower edge. A satellite tick outside tolerance is drawn hollow, matching the dimmed "no frame" state.
- SYNC: on by default and moved to the timeline's overflow menu. Off means satellite keeps its own loop, which is then only reachable in its section. That case is rare enough not to need its own bar.
- Placed bottom centre, between the layers panel and the right column. Hidden in ARCHIVE, where the existing archive bar keeps the bottom, and when no layer can loop.
- The status pill's loop chip is hidden in docked, because the timeline shows the same thing.
- Tests: the timeline with a fake `LoopSync` and fake drawers covers each button routed to the driver, tick placement, Live snapping and the driver switching when radar is turned off.
- Done when: in VEHICLE with radar, CONUS satellite and a truck replay, the timeline alone plays, steps, scrubs and returns to live, and both drawers' playback rows are hidden.

Status: built, not committed. Notes on what was built, and where it differs from the design above:

- `ui/widgets/loop_timeline.py`: `LoopTimeline` (back, play / pause, forward, `TimeStrip`, time label, LIVE, speed menu, Vehicles, ⋯ with SYNC) and `TimeStrip` (ticks and cursor; scrubbing emits on release, like the drawers' sliders). It only displays what `set_state` / `set_ticks` give it.
- Routing lives in `MainWindowLoopMixin` (`_attach_loop_timeline`, `_loop_driver`, `_refresh_loop_timeline`), attached at the end of `_init_loop_sync` when a timeline exists. The driver is radar while its data is on, else satellite while a sector is selected, else none (timeline hidden). It isn't read from `LoopSync.source`, which stays at radar when sync is off. Scrub picks the driver frame nearest the time. The label is the driver's frame time, so it is right with SYNC off too.
- Both drawers gained one playback API: `frame_count`, `request_frame`, `speed_labels` / `speed_index` / `set_speed_index`, `set_playback_row_visible`; satellite also got `toggle_loop` / `step_frame`, and radar `set_sync` / `set_sync_vehicles`. Vehicles and SYNC go through radar's own VEH and SYNC buttons, so their existing handlers and saved state are unchanged.
- Playback rows: radar's is always hidden in docked. Satellite's shows only while radar drives with SYNC off, the one case where satellite loops on its own.
- Redraws: on the loop's signals and the drawers' frame / loop / mode / speed signals (coalesced), plus a 2 s timer that picks up new scans and frames. The loop chip stays hidden while a timeline exists.
- Placement: bottom centre, up to 720 px, right of the status pill (and of the layers panel if it reaches the bottom), clear of the team panel.
- Not done: it needs the live loop, so with `--disable-radar` there is no timeline (classic has the same limit for sync). ARCHIVE keeps its own bar. Space and `[` / `]` still go to radar directly; step 6 routes them through the timeline's driver.
- Tests: `tests/test_loop_timeline.py` covers the widget (label, buttons, ticks and hollow frames, scrub on release) and the routing with fake drawers (radar drives, label and chip, SYNC / Vehicles, satellite takes over with radar off, hidden with nothing to loop). A scripted VIEWER run in docked stepped back, played, went live, turned SYNC off and handed over to satellite. The VEHICLE truck-replay check in "Done when" is still to do by hand.

## Step 5: Tools bar

Files: new `ui/widgets/tools_bar.py`, `ui/app/main_window_docked.py`, `ui/controls/map_controls.py`.

- `ToolsBar`: Sounding, Annotate, Measure, Route and a ⌘K button that opens the palette. Each tool button mirrors its hidden button (`btn_sounding`, `btn_annotate`, `btn_measure`, `btn_route`) both ways.
- One tool at a time: turning one on turns the others off through their own buttons, so `ClickModeController` and every exit path (Esc, finishing a measurement) work as today.
- Annotate's drawer (types, cone) opens directly under the tools bar, the only drawer still centre-top. Route's drawer opens in the right column under the nav pill, where the nav pill's ▾ already puts it. Sounding's drawer (source choice) opens under the tools bar.
- The mode banner moves to sit under the tools bar or its open drawer.
- `MAP` disappears in docked: measure and route go to the tools bar, basemap toggles to the Basemap section (step 1), settings to the gear (step 6).
- Tests: mirroring both ways, mutual exclusion, Esc exits the active tool, and VIEWER hides Route as the toolbar does.
- Done when: every click tool is reachable from the tools bar in each mode where its toolbar button is.

Status: built, not committed. Notes on what was built, and where it differs from the design above:

- `ui/widgets/tools_bar.py`: `ToolsBar.add_tool(key, label, tip, hidden)` mirrors a hidden button both ways. One tool at a time is enforced on the hidden buttons' `toggled`, so a tool turned on by a shortcut, the palette or the map menu also turns the others off. If a hidden button refuses, the bar follows it.
- `MainWindowDockedMixin._init_tools_bar` offers a tool where classic does: toolbar buttons by `is_available`, measure / route by the MAP drawer's visibility (VIEWER has no Route; ARCHIVE has no Annotate). `_place_tools` puts the bar top centre (right of the layers panel when the map is narrow), the sounding and annotate drawers and the mode banner under it, and the route drawer at the top of the right column (the nav pill hides while it is open, as in classic).
- Styles are shared with the timeline (`#toolsBar` next to `#loopTimeline` in `theme.py`). Labels are text only.
- Not done here: the `MAP` drawer still opens by its shortcut and palette entry, because its settings half has no other home until the gear (step 6). The `⌘K` label shows on every platform.

## Step 6: Right column, gear menu, and parity

Files: `ui/app/main_window_docked.py`, `ui/app/main_window_vehicles.py` (placement only), `ui/app/main_window_palette.py`, `ui/shortcuts.py`.

- Right column, top to bottom: nav pill, route drawer when open, team panel (vehicle panel and detail). The outlook panel overlays the column while open, as it does now. A team header toggle replaces VEHICLES. The vehicle panel's size watermark carries over.
- Gear button (top right, beside the screenshot button): text scale, layout switch, reset layout. Reset layout in docked resets panel width, rail state, expanded sections and group order.
- Palette: the Drawers provider says "Expand Radar" etc. in docked and expands the section (it already goes through the buttons). The toolbar-parity rule (`shown or overflowed`) gets a docked branch: a button counts as available when its section or tool exists.
- Shortcuts: R, S, H, O, K, N expand or collapse their section (and open the rail flyout when collapsed). Space and `[` / `]` drive the timeline. No new keys.
- Session state (per mode, as today): panel width, rail or expanded, expanded sections, timeline speed. Docked state lives under its own keys so it can't disturb classic's restore.
- Tests: palette and shortcut parity per mode in docked, gear actions, session save / restore round trip.
- Done when: anything reachable in classic is reachable in docked, checked mode by mode against the classic toolbar.

Status: built, not committed. Notes on what was built, and where it differs from the design above:

- `ui/widgets/column_header.py`: `ColumnHeader` heads the right column with a Team toggle (mirrors VEHICLES both ways; absent where VEHICLES is) and the gear: Text size, Layout (next launch), Reset layout. Choices go through the MAP drawer's own choosers, so both stay in step. The gear sits in this header at the top right rather than beside the screenshot button, which lives bottom right.
- `_place_right_column`, top to bottom: header, nav pill, route drawer (from the Route tool or the pill's ▾), vehicle panel with its size watermark, vehicle detail. The outlook panel still overlays the column.
- Reset layout in docked also un-collapses the rail and restores the default group order (and so the section order). Expanded sections close with the other drawers, as before. The panel has a fixed width, so there is no width to reset or save.
- Parity: `_toolbar_button_available` asks `_docked_button_available` in docked, true for buttons with a section, tool or the Team toggle. MAP has none, so it leaves the palette with its drawer-mode commands. Drawer commands read "Expand / Collapse Radar" for sections and "Turn on / off Sounding" for tools (and Land cover, a Basemap toggle).
- Shortcuts: R, S, H, O already go through the drawer buttons, which expand the section and open the rail flyout. K and N have no entry in the shortcut table, so nothing was added for them ("no new keys"). Space and `[` / `]` go to `_loop_key`, which drives whatever the timeline drives when there is one. Their labels still say "radar loop".
- Session: docked saves `session/docked_<mode>` (expanded sections, rail, radar and satellite speed). The shared `session/<mode>` keeps control state, and in docked keeps classic's last drawer list unchanged. A saved rail choice beats the narrow-map default.

## Comparing the two

Before deciding, run both side by side on real chase days:

- A scripted screenshot run (like the one used for this audit) for each layout in each mode at 1600×1000 and 1100×750, saved next to each other.
- Things to watch: how much of the map centre stays clear, how fast a layer can be turned on or off, whether the rail is enough on the truck laptop, and whether anyone misses the free-floating drawers.

## Adopting docked

When docked is chosen, one cleanup phase:

1. Make `docked` the default and keep `--layout classic` for one release as a fallback.
2. Then remove classic: `_layout_overlays_classic`, `ResponsiveToolbar`'s visible use (keep the buttons, or replace them with plain `QAction`s if nothing needs the widgets), `DrawerManager` modes, pins and `enforce_fit`, `overlay_geometry.drawers_to_close`, `LayerOrderPill`, the toolbar status dots and overflow dots, the LEGEND pill, the loop chip, `Drawer`'s animation, and the MAP drawer's settings half. Drop the `set_compact` / `set_embedded` switches by making compact and embedded the only layout.
3. Update `STORM_USER_GUIDE.md`, the `?` overlay text and tests that assert classic geometry.

## Open questions

- Panel on the left (assumed) or the right? The right would keep the team panel and outlook text together on the left instead.
- Panel width: 300 px assumed. Fine on 1600 px, tight on 1100 px, where the rail is the default.
- Does docked need the fine per-key draw order, or is group order enough?
- Should the switch become live (no restart) later? It isn't needed to compare the layouts.
