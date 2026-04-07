# Vehicle Timeseries — Implementation Plan

## Overview

Add a historical timeseries dialog for vehicles that are pushing meteorological data (T, Td, wind speed, wind direction, pressure). The feature works in both live mode (MQTT) and archive mode (THREDDS JSONL replay). The dialog matches the visual language of the existing `SoundingDialog` (dark palette, interactive cursor readout, matplotlib backend).

---

## Data Layer

### In-Memory Observation History

- Add `_vehicle_history: dict[str, deque[Observation]]` to `MainWindow._init_stations()`.
- In `update_vehicle_obs()`, after updating the vehicle marker, append the observation to the deque **only if**:
  1. The vehicle is **not** the user's own vehicle (`obs.vehicle_id != config.VEHICLE_ID`).
  2. The observation contains met data — at least one of `temperature_c`, `dewpoint_c`, `wind_speed_ms`, or `pressure_mb` is not `None`.
- No `maxlen` cap — a full day of 10-second data for 10 vehicles is ~13 MB, well within reason.
- The history resets when the app is closed (expected — archive mode covers post-session review).

### Archive Mode

- No new storage needed. `ArchiveMQTTReader._data["vehicles"]` already holds all JSONL vehicle records for the day, sorted by timestamp.
- Filter by `vehicle_id` and discard GPS-only records (no met fields) before passing to the dialog.

---

## UI Integration — Vehicle Detail Panel

### Triggering the Dialog

- In `_make_vehicle_detail_section()`, add a small **chart button** (e.g. a "TIMESERIES" label or a chart icon `QToolButton`) to the top row of each vehicle's detail section, next to the name/badge/age row.
- The button is **only shown** for vehicles that have met data history (i.e. `vid in self._vehicle_history and len(self._vehicle_history[vid]) > 0`, or equivalent archive-mode check).
- Clicking the button opens a `VehicleTimeseriesDialog` for that vehicle, passing in the list of `Observation` objects.
- If the dialog is already open for that vehicle, raise/focus it instead of opening a duplicate.

---

## VehicleTimeseriesDialog — Layout

New file: `ui/vehicle_timeseries_dialog.py`

### Window Properties

- `QDialog` with `Qt.WindowType.Window` flag (floatable, independent of main window).
- `WA_DeleteOnClose = False` — reusable; update data and re-plot when re-opened.
- Title: `"Vehicle Timeseries — {vehicle_id}"`
- Minimum size: ~640x600, default ~720x700.

### Structure (top to bottom)

```
+--------------------------------------------------+
|  Title bar (vehicle_id, obs count, time range)   |
+--------------------------------------------------+
|                                                  |
|  Panel 1: Temperature / Dewpoint (shared y-axis) |
|    - T line: #ff6b6b (matches sounding _TEMP_CLR)|
|    - Td line: #3ddc84 (matches _DEWP_CLR)        |
|    - Y-axis: degrees F                           |
|                                                  |
+--------------------------------------------------+
|                                                  |
|  Panel 2: Wind Speed / Direction (dual y-axes)   |
|    - Left y-axis: wind speed (kt), line plot     |
|      Color: #4fc3f7 (light blue)                 |
|    - Right y-axis: wind direction (deg), scatter  |
|      Color: #ffd700 (gold), small markers        |
|    - Direction y-axis: 0-360, ticks at 90/180/270|
|                                                  |
+--------------------------------------------------+
|                                                  |
|  Panel 3: Pressure (single y-axis)               |
|    - Line: #b39ddb (light purple)                |
|    - Y-axis: mb/hPa                              |
|                                                  |
+--------------------------------------------------+
|  Cursor readout label (single line, like sonde)  |
+--------------------------------------------------+
```

### Shared X-Axis

- All three panels share the same time axis (bottom panel shows tick labels, upper two panels hide x-tick labels but stay synced).
- X-axis formatted as `HH:MM UTC`.
- Default view: full time range of available data.

### Interactive Features

**Zoom/Pan (matplotlib built-in):**
- Scroll wheel zooms the time axis (all three panels stay synced).
- Click-and-drag to pan.
- This comes from matplotlib's `NavigationToolbar2` pan/zoom or from manual `scroll_event` + `button_press_event` handling on the shared x-axis.

**Cursor Readout:**
- On `motion_notify_event`, find the nearest observation by timestamp (snap to closest data point).
- Draw a vertical line across all three panels at that time.
- Update the readout label at the bottom:
  ```
  18:42:30 UTC  ·  T 78°F  Td 65°F  ·  Wind 225° @ 32 kt  ·  1008.3 mb
  ```
- On `axes_leave_event`, clear the readout and hide the vertical line.

---

## Color Palette & Theming

Reuse constants from `sounding_dialog.py`:

| Constant   | Value     | Usage                        |
|------------|-----------|------------------------------|
| `_FIG_BG`  | `#0a0a0f` | Figure background            |
| `_AX_BG`   | `#0f0f1a` | Axes background              |
| `_TEXT`    | `#e8eaf0` | Axis labels, tick labels      |
| `_MUTED`  | `#666688` | Grid lines, secondary text    |
| `_BORDER` | `#2a2a40` | Axis spines                   |
| `_TEMP_CLR`| `#ff6b6b` | Temperature line              |
| `_DEWP_CLR`| `#3ddc84` | Dewpoint line                 |
| `_LM_CLR` | `#4fc3f7` | Wind speed line               |

New or borrowed:

| Color       | Value     | Usage                |
|-------------|-----------|----------------------|
| Wind dir    | `#ffd700` | Direction scatter    |
| Pressure    | `#b39ddb` | Pressure line        |
| Cursor vline| `#ffffff` | Vertical cursor, 40% alpha |

---

## Unit Conversions

All observations arrive in metric. Convert for display:
- Temperature/Dewpoint: C -> F (`t * 9/5 + 32`)
- Wind speed: m/s -> knots (`wspd * 1.94384`)
- Wind direction: degrees (no conversion)
- Pressure: mb/hPa (no conversion)

These match the existing conversions in `_make_vehicle_detail_section()`.

---

## Live Update Behavior

- While the dialog is open in live mode, new observations appended to the deque should update the plot.
- Connect a lightweight refresh (e.g. every 10 seconds via `QTimer`) that redraws only if the dialog is visible and the data has grown since last draw.
- Preserve the user's current zoom/pan state — only auto-extend the x-axis if the user is viewing the latest edge of the data (i.e. not zoomed/panned to an earlier window).

---

## Archive Mode Considerations

- When the app is in archive mode, the timeseries button should still appear for vehicles with met data.
- Data source: filter `ArchiveMQTTReader._data["vehicles"]` by vehicle_id, convert each record to an `Observation` via `_observation_from_payload()`, and discard GPS-only records.
- The `on_time_changed()` callback in archive mode replays up to a given time — the timeseries dialog should show data **up to the current archive time**, not the full day. As the user scrubs forward, more data appears.

---

## File Changes Summary

| File | Change |
|------|--------|
| `ui/vehicle_timeseries_dialog.py` | **New** — the dialog class with matplotlib figure, three-panel plot, cursor readout |
| `ui/main_window.py` | Add `_vehicle_history` dict, append logic in `update_vehicle_obs()`, timeseries button in `_make_vehicle_detail_section()`, dialog open/management, archive-mode data extraction |
| `ui/theme.py` | Possibly add QSS for the dialog if needed (may not be required since it's a standalone `QDialog` with matplotlib) |
