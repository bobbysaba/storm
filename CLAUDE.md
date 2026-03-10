# STORM Project

## Run Command
```
python main.py
```

## Stack
- **UI**: PyQt6 + QWebEngineView embedding MapLibre GL JS
- **Map server**: Flask (localhost:8765) serving MBTiles vector tiles + static assets
- **Data**: SPC GeoJSON endpoints, NWS alerts API, MQTT for vehicle/annotation sync
- **Python bridge**: QWebChannel (`bridge` object in JS ↔ Python slots)

## Key Files
- `ui/map_widget.py` — all map HTML/JS (single large f-string in `build_map_html()`)
- `ui/main_window.py` — top-level window, wires signals/slots
- `ui/hazard_controls.py` — SPC/NWS toggle panel
- `data/hazard_fetcher.py` — background polling of SPC + NWS feeds
- `config.py` — app-wide constants (ACCENT_COLOR, etc.)

## SPC GeoJSON Notes (confirmed by live fetch)
- LABEL field format is **decimal strings**: `'0.02'`, `'0.05'`, `'0.10'`, `'0.15'`, `'0.30'`, `'0.45'`, `'0.60'`
- Significant areas use **`LABEL='SIGN'`** (legacy, used for tor) or **`LABEL='CIG1'`/`'CIG2'`/`'CIG3'`** (SPC Conditional Intensity Groups, used for wind/hail — and potentially tor)
- CIG/SIGN features are **separate GeoJSON features** that spatially overlap the probability polygons — rendered with their own fill layers filtered by LABEL
- `LABEL2` has human-readable text: `"5% Tornado Risk"`, `"Hail Conditional Intensity Group 1 Risk"`, etc.
- `DN` is the numeric probability value (e.g. DN=5 for LABEL='0.05')
- Color expressions match both `'0.05'` and `'5'` forms for safety
- CIG/SIGN polygons have `stroke="#000000"`, `fill="#888888"` in SPC data — we render as gray base + hatch pattern

## Hatching Patterns (map_widget.py)
- `sig-hatch-cig1`: dashed diagonal lines (for CIG1 features)
- `sig-hatch-cig2`: solid diagonal lines (for CIG2 and SIGN features)
- `sig-hatch-cig3`: checkerboard (for CIG3 features)
- Built as raw `Uint8Array` pixel data (no canvas — canvas readback fails in QWebEngineView)
- Each product (tor/wind/hail) has layers: `fill`, `line`, `sig-base`, `sign`, `cig1`, `cig2`, `cig3`, `sig-line`

## MD Text Loading (confirmed working)
- **Day 1 outlook**: IEM AFOS → `https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?pil=SWODY1&limit=1&fmt=text`
- **MDs**: SPC direct URL → `https://www.spc.noaa.gov/products/md/md{nnnn}.txt` (IEM rejects `SPCMCD{nnnn}` as too long)
- MD `name` property from MapServer is `"MD 0179"` format — extract digits with `re.search(r'\d+', name)`

## New Collapsible Drawer Widget Checklist
Every new collapsible drawer (like RadarControls, SatelliteControls, HazardControls) **must** register in `ui/theme.py` or it will show a black background box:
1. Inner drawer QWidget needs a unique `objectName` (e.g. `"satelliteDrawer"`)
2. Add to `#floatingToolbar QWidget#<name>` → `background: transparent` block
3. Add to `#floatingToolbar QWidget#<name> > QWidget` → `background: transparent` block
4. Add to the `QCheckBox` transparent block if it contains checkboxes
5. If it has a playback row, name it (e.g. `"satPlaybackRow"`) and add mirroring QSS rules
6. Playback buttons: `setFixedSize(32, 26)` with `padding: 1px 3px; font-size: 13px;` in QSS

## MapLibre in QWebEngineView — Known Quirks
- Canvas readback can silently fail → build hatch patterns as raw `Uint8Array` pixel data, NOT via canvas
- `fill-pattern` with `addImage({width, height, data})` should work; verify with `map.hasImage()` console logs
- The `bridge` object (QWebChannel) is async-initialized; all API functions are stubbed as `_stormNoop` until ready

## Permissions (settings.local.json)
- `WebFetch(domain:*)` — fetch SPC/NWS/IEM endpoints and docs
- `WebSearch` — look up MapLibre GL JS API, SPC data formats
- `Bash(python *)` — run the app and capture output for debugging
