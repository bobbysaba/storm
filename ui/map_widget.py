# ui/map_widget.py
# Embeds a MapLibre GL JS map inside a QWebEngineView.
# Assets and vector tiles are served via a QWebEngineUrlSchemeHandler
# (storm://app/...) — no Flask server or open TCP port required.

import json
import os
import sys
import runtime_flags

# Optional Windows fallback: disable WebGL map rendering only when explicitly
# requested for troubleshooting unstable GPU/ANGLE setups.

SAFE_MAP_MODE = (
    sys.platform == "win32"
    and runtime_flags.FLAGS.safe_map_mode
)

from PyQt6.QtCore import QUrl, QTimer, pyqtSignal, QObject, pyqtSlot, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

if not SAFE_MAP_MODE:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel

from config import ACCENT_COLOR

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_LAT  = 35.22
DEFAULT_LON  = -97.44
DEFAULT_ZOOM = 6
_STORM_BASE  = "storm://app"   # base for all asset/tile URLs

TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tiles", "storm.mbtiles")
)

STATIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "static")
)

# ── Map HTML ──────────────────────────────────────────────────────────────────

def build_map_html() -> str:
    """Build the full HTML page for the MapLibre map."""
    tile_url = f"{_STORM_BASE}/tiles/{{z}}/{{x}}/{{y}}.pbf"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>STORM</title>
  <script src="{_STORM_BASE}/static/maplibre-gl.js"></script>
  <link href="{_STORM_BASE}/static/maplibre-gl.css" rel="stylesheet"/>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; background: #0A0A0F; overflow: hidden; }}
    #map {{ width: 100%; height: 100%; }}
    #map.annotating, #map.measuring, #map.drawing {{ cursor: crosshair; }}
    #front-canvas {{
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      pointer-events: none;
      z-index: 5;
    }}
    .maplibregl-ctrl-attrib {{ opacity: 0.4; font-size: 9px; }}
    .maplibregl-ctrl-group {{
      background: #0F0F1A !important;
      border: 1px solid #1E1E2E !important;
      border-radius: 6px !important;
    }}
    .maplibregl-ctrl-group button {{
      background: transparent !important;
      border-bottom: 1px solid #1E1E2E !important;
    }}
    .maplibregl-ctrl-group button:last-child {{ border-bottom: none !important; }}
    .maplibregl-ctrl-icon {{ filter: invert(0.7); }}
    .maplibregl-ctrl-scale {{
      background: rgba(15,15,26,0.9) !important;
      border: 1px solid #49536F !important;
      color: #C1C9D8 !important;
      font-size: 10px !important;
      padding: 1px 4px !important;
    }}

    /* Scale bar: float it to the left of the legend along the same bottom baseline. */
    .maplibregl-ctrl-bottom-left {{
      left: auto !important;
      right: 234px !important;
      bottom: 10px !important;
      width: auto !important;
      display: flex !important;
      justify-content: flex-end !important;
    }}

    .maplibregl-ctrl-bottom-left .maplibregl-ctrl {{
      margin: 0 !important;
    }}

    .maplibregl-ctrl-bottom-right {{
      right: 10px !important;
      bottom: 10px !important;
    }}

    .maplibregl-ctrl-bottom-right .maplibregl-ctrl {{
      margin: 0 !important;
    }}

    /* ── Legend ── */
    #storm-legend {{
      position: absolute;
      bottom: 10px;
      right: 50px;
      width: 172px;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      z-index: 100;
      font-family: "Helvetica Neue", sans-serif;
    }}

    #legend-toggle {{
      display: flex;
      align-items: center;
      gap: 6px;
      background: rgba(15, 15, 26, 0.92);
      border: 1px solid #49536F;
      border-radius: 6px;
      padding: 5px 10px;
      cursor: pointer;
      color: #C1C9D8;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 1px;
      text-transform: uppercase;
      user-select: none;
      transition: border-color 0.15s, color 0.15s;
    }}

    #legend-toggle:hover {{
      border-color: {ACCENT_COLOR};
      color: {ACCENT_COLOR};
    }}

    #legend-toggle .arrow {{
      font-size: 8px;
      transition: transform 0.2s;
    }}

    #legend-toggle.open .arrow {{
      transform: rotate(180deg);
    }}

    #legend-body {{
      display: none;
      background: rgba(15, 15, 26, 0.92);
      border: 1px solid #49536F;
      border-radius: 6px;
      padding: 10px 12px;
      margin-bottom: 4px;
      min-width: 160px;
    }}

    #legend-body.visible {{
      display: block;
    }}

    #hazard-tooltip {{
      display: none;
      position: absolute;
      pointer-events: none;
      background: rgba(15, 15, 26, 0.92);
      border: 1px solid #49536F;
      border-radius: 5px;
      padding: 5px 9px;
      font-family: "Helvetica Neue", sans-serif;
      font-size: 11px;
      font-weight: 600;
      color: #E8EDF5;
      letter-spacing: 0.3px;
      white-space: nowrap;
      z-index: 200;
    }}

    .maplibregl-popup.storm-hover-tooltip .maplibregl-popup-content {{
      background: rgba(15, 15, 26, 0.96);
      color: #E8EAF0;
      border: 1px solid #49536F;
      border-radius: 6px;
      padding: 4px 8px;
      font-family: "Helvetica Neue", sans-serif;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.3px;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
    }}

    .maplibregl-popup.storm-hover-tooltip .maplibregl-popup-tip {{
      border-top-color: rgba(15, 15, 26, 0.96);
      border-bottom-color: rgba(15, 15, 26, 0.96);
    }}

    .maplibregl-ctrl-top-right {{
      top: 10px !important;
      right: 10px !important;
    }}

    .maplibregl-ctrl-top-right .maplibregl-ctrl {{
      margin: 0 !important;
    }}

    .maplibregl-ctrl-attrib-button {{
      background: rgba(15, 15, 26, 0.95) !important;
      border: 1px solid #49536F !important;
      color: #C1C9D8 !important;
      border-radius: 6px !important;
    }}

    .legend-section-title {{
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: #8E97AB;
      margin-bottom: 6px;
      margin-top: 8px;
    }}

    .legend-section-title:first-child {{
      margin-top: 0;
    }}

    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 5px;
    }}

    .legend-item:last-child {{
      margin-bottom: 0;
    }}

    .legend-line {{
      width: 28px;
      height: 0;
      flex-shrink: 0;
    }}

    .legend-label {{
      font-size: 10px;
      color: #C1C9D8;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <canvas id="front-canvas"></canvas>

  <!-- ── Legend ── -->
  <div id="storm-legend">
    <div id="legend-body">
      <div class="legend-section-title">Roads</div>

      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#CC5528" stroke-width="3"/></svg>
        <span class="legend-label">Motorway</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#5A4A35" stroke-width="2.5"/></svg>
        <span class="legend-label">Trunk</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#4A4A60" stroke-width="2"/></svg>
        <span class="legend-label">Primary</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#35354A" stroke-width="1.5"/></svg>
        <span class="legend-label">Secondary</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#252530" stroke-width="1"/></svg>
        <span class="legend-label">Minor / Residential</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#3A2E1A" stroke-width="1" stroke-dasharray="4,2"/></svg>
        <span class="legend-label">Unpaved / Gravel</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#2E2416" stroke-width="1" stroke-dasharray="2,3"/></svg>
        <span class="legend-label">Track / Farm</span>
      </div>

      <div class="legend-section-title">Boundaries</div>

      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#2A2A3E" stroke-width="1.5" stroke-dasharray="5,3"/></svg>
        <span class="legend-label">State</span>
      </div>
      <div class="legend-item">
        <svg width="28" height="6"><line x1="0" y1="3" x2="28" y2="3" stroke="#1A1A28" stroke-width="1"/></svg>
        <span class="legend-label">County</span>
      </div>

      <div class="legend-section-title">Overlays</div>
      <div class="legend-item">
        <input type="checkbox" id="cwa-toggle" onchange="if(window.stormSetCwaVisible) stormSetCwaVisible(this.checked);" />
        <span class="legend-label">NWS CWA</span>
      </div>
    </div>

    <div id="legend-toggle">
      <span>Legend</span>
      <span class="arrow">▲</span>
    </div>
  </div>

  <!-- ── Hazard Hover Tooltip ── -->
  <div id="hazard-tooltip"></div>

  <script>
    window.onerror = function(msg, src, line) {{
      console.error("JS ERROR: " + msg + " at " + src + ":" + line);
    }};

    // Define no-op bridge functions up front so Python calls stay safe even
    // if MapLibre/WebGL initialization fails on this machine.
    function _stormNoop() {{}}
    window.stormAddVehicle = _stormNoop;
    window.stormRemoveVehicle = _stormNoop;
    window.stormFlyTo = _stormNoop;
    window.stormFollowMove = _stormNoop;
    window.stormSetFollow = _stormNoop;
    window.stormAddAnnotation = _stormNoop;
    window.stormRemoveAnnotation = _stormNoop;
    window.stormAddStormCone = _stormNoop;
    window.stormRemoveStormCone = _stormNoop;
    window.stormAddStationPlot = _stormNoop;
    window.stormRemoveStationPlot = _stormNoop;
    window.stormSetStationPlotsVisible = _stormNoop;
    window.stormAddSurfaceStationPlot = _stormNoop;
    window.stormRemoveSurfaceStationPlot = _stormNoop;
    window.stormSetSurfaceStationPlotsVisible = _stormNoop;
    window.stormLoadDeployLocs = _stormNoop;
    window.stormSetDeployLocsVisible = _stormNoop;
    window.stormMeasureActivate = _stormNoop;
    window.stormMeasureClick = _stormNoop;
    window.stormMeasureClear = _stormNoop;
    window.stormAddDrawing = _stormNoop;
    window.stormRemoveDrawing = _stormNoop;
    window.stormDrawingModeSet = _stormNoop;
    window.stormDrawingUpdatePreview = _stormNoop;
    window.stormSetSpcGeoJSON = _stormNoop;
    window.stormSetSpcCategoryVisible = _stormNoop;
    window.stormSetSpcProductVisible = _stormNoop;
    window.stormSetNwsWarningsGeoJSON = _stormNoop;
    window.stormSetNwsWarningsVisible = _stormNoop;
    window.stormSetSpcWatchesGeoJSON = _stormNoop;
    window.stormSetSpcWatchesVisible = _stormNoop;
    window.stormSetSpcMdsGeoJSON = _stormNoop;
    window.stormSetSpcMdsVisible = _stormNoop;
    window.stormSetSatelliteFrame = _stormNoop;
    window.stormSetSatelliteTime = _stormNoop;
    window.stormSetSatelliteVisible = _stormNoop;
    window.stormSetSatelliteMode = _stormNoop;
    window.stormSetSatelliteOpacity = _stormNoop;
    window.stormSetMesoSectors = _stormNoop;
    window.stormSetMesoanalysisFrame = _stormNoop;
    window.stormSetMesoanalysisOpacity = _stormNoop;
    window.stormSetMesoanalysisVisible = _stormNoop;
    window.stormClearMesoanalysisFrame = _stormNoop;
    window.stormSetSfcOASectors = _stormNoop;
    window.stormPreviewSfcOASector = _stormNoop;
    window.stormClearSfcOAPreview = _stormNoop;
    window.stormSetRadarStations = _stormNoop;
    window.stormSetRadarStationsVisible = _stormNoop;
    window.stormSetSoundingStations = _stormNoop;
    window.stormClearSoundingStations = _stormNoop;
    window.stormSetRoute = _stormNoop;
    window.stormClearRoute = _stormNoop;
    window.stormSetRoutePickMode = _stormNoop;
    // CWA overlay hooks (populated when the map loads)
    window.stormSetCwaGeoJSON = _stormNoop;
    window.stormSetCwaVisible = _stormNoop;
    window.stormSetDestinationMarker = _stormNoop;
    window._radarStationsVisible = false;
    window._stormDrawings = {{}};
    window._stormDrawingActive = false;
    window._soundingModeActive = false;
    window._soundingObsModeActive = false;
    window._stormDrawingType = '';
    window._drawingConfirmedPts = [];
    window._drawingRubberPt = null;

    // Suppress MapLibre's benign AbortController warning that fires when
    // updateImage() cancels a prior in-flight radar image fetch.
    (function() {{
      const _warn = console.warn.bind(console);
      console.warn = function() {{
        if (arguments[0] && String(arguments[0]).includes("signal is aborted without reason")) return;
        _warn.apply(console, arguments);
      }};
    }})();

    // ── Qt Bridge ─────────────────────────────────────────────────────────
    let bridge = null;
    if (typeof QWebChannel !== "undefined") {{
      new QWebChannel(qt.webChannelTransport, function(channel) {{
        bridge = channel.objects.bridge;
        // If map.on("load") already fired before bridge was ready, notify now.
        if (window._stormMapLoaded) bridge.on_map_loaded();
      }});
    }}

    // Forward console messages to Python via the Qt bridge so they can be
    // captured in the application's stdout/logs.  This helps diagnose
    // renderer-side stalls when updateImage/fetches are in-flight.
    (function() {{
      const _orig = {{ log: console.log.bind(console), warn: console.warn.bind(console), error: console.error.bind(console), info: console.info.bind(console) }};
      function _fmtArgs(args) {{
        try {{
          return Array.prototype.slice.call(args).map(function(a) {{
            try {{ if (typeof a === 'object') return JSON.stringify(a); }} catch(e) {{}}
            return String(a);
          }}).join(' ');
        }} catch(e) {{ return String(args); }}
      }}
      function _forward(level, args) {{
        try {{
          const payload = level + ' ' + _fmtArgs(args);
          if (bridge && bridge.on_js_console) try {{ bridge.on_js_console(payload); }} catch(e) {{}}
        }} catch(e) {{}}
      }}
      console.log = function() {{ _forward('log', arguments); _orig.log.apply(console, arguments); }};
      console.warn = function() {{ _forward('warn', arguments); _orig.warn.apply(console, arguments); }};
      console.error = function() {{ _forward('error', arguments); _orig.error.apply(console, arguments); }};
      console.info = function() {{ _forward('info', arguments); _orig.info.apply(console, arguments); }};
    }})();

    // ── Map Style ─────────────────────────────────────────────────────────
    const STORM_STYLE = {{
      version: 8,
      name: "STORM Dark",
      glyphs: "{_STORM_BASE}/static/fonts/{{fontstack}}/{{range}}.pbf",
      sources: {{
        "storm-tiles": {{
          type: "vector",
          tiles: ["{tile_url}"],
          minzoom: 0,
          maxzoom: 14
        }}
      }},
      layers: [
        // ── Background ────────────────────────────────────────────────────
        {{
          id: "background", type: "background",
          paint: {{ "background-color": "#0D0D14" }}
        }},

        // ── Landcover ─────────────────────────────────────────────────────
        {{
          id: "landcover", type: "fill",
          source: "storm-tiles", "source-layer": "landcover",
          paint: {{
            "fill-color": ["match", ["get", "class"],
              "farmland",  "#0C0F0A",
              "forest",    "#0A0F0A",
              "grass",     "#0B0F0A",
              "scrub",     "#0B100A",
              "wetland",   "#0A0E10",
              "wood",      "#0A0F0A",
              "#0D0D14"],
            "fill-opacity": 0.8
          }}
        }},

        // ── Landuse ───────────────────────────────────────────────────────
        {{
          id: "landuse", type: "fill",
          source: "storm-tiles", "source-layer": "landuse",
          paint: {{
            "fill-color": ["match", ["get", "class"],
              "residential",  "#111118",
              "commercial",   "#111118",
              "industrial",   "#0F0F16",
              "retail",       "#111118",
              "park",         "#0A110A",
              "cemetery",     "#0D110D",
              "hospital",     "#110F0F",
              "school",       "#0F0F14",
              "#0D0D14"],
            "fill-opacity": 0.8
          }}
        }},

        // ── Water ─────────────────────────────────────────────────────────
        {{
          id: "water", type: "fill",
          source: "storm-tiles", "source-layer": "water",
          paint: {{ "fill-color": "#0A1628" }}
        }},
        {{
          id: "waterway", type: "line",
          source: "storm-tiles", "source-layer": "waterway",
          minzoom: 8,
          paint: {{
            "line-color": "#0A1628",
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 14, 2]
          }}
        }},

        // ── Boundaries ────────────────────────────────────────────────────
        {{
          id: "boundary-country", type: "line",
          source: "storm-tiles", "source-layer": "boundary",
          filter: ["==", ["get", "admin_level"], 2],
          paint: {{ "line-color": "#3A3A5A", "line-width": 2 }}
        }},
        {{
          id: "boundary-state", type: "line",
          source: "storm-tiles", "source-layer": "boundary",
          filter: ["==", ["get", "admin_level"], 4],
          paint: {{
            "line-color": "#FFFFFF",
            "line-width": 1.5
          }}
        }},
        {{
          id: "boundary-county", type: "line",
          source: "storm-tiles", "source-layer": "boundary",
          filter: ["==", ["get", "admin_level"], 6],
          minzoom: 7,
          paint: {{ "line-color": "#1A1A28", "line-width": 0.75 }}
        }},

        // ── Roads ─────────────────────────────────────────────────────────

        // Unpaved / dirt / gravel
        {{
          id: "road-unpaved", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["in", ["get", "surface"], ["literal",
            ["unpaved","dirt","gravel","compacted","fine_gravel","grass","ground","sand","earth"]]],
          minzoom: 10,
          paint: {{
            "line-color": "#3A2E1A",
            "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 14, 1.5],
            "line-dasharray": [3, 2]
          }}
        }},

        // Track / farm access roads
        {{
          id: "road-track", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "track"],
          minzoom: 11,
          paint: {{
            "line-color": "#2E2416",
            "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.5, 14, 1.2],
            "line-dasharray": [2, 3]
          }}
        }},

        // Path / footway
        {{
          id: "road-path", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "path"],
          minzoom: 13,
          paint: {{
            "line-color": "#252520",
            "line-width": 0.75,
            "line-dasharray": [1, 2]
          }}
        }},

        // Minor / residential / service
        {{
          id: "road-minor", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["in", ["get", "class"], ["literal", ["minor","service","residential"]]],
          minzoom: 8,
          paint: {{
            "line-color": "#252530",
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.3, 14, 2]
          }}
        }},

        // Secondary / tertiary
        {{
          id: "road-secondary", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["in", ["get", "class"], ["literal", ["secondary","tertiary"]]],
          minzoom: 8,
          paint: {{
            "line-color": "#35354A",
            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.75, 14, 3]
          }}
        }},

        // Primary
        {{
          id: "road-primary", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "primary"],
          paint: {{
            "line-color": "#4A4A60",
            "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.75, 14, 5]
          }}
        }},

        // Trunk
        {{
          id: "road-trunk", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "trunk"],
          paint: {{
            "line-color": "#5A4A35",
            "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 14, 6]
          }}
        }},

        // Motorway glow/casing
        {{
          id: "road-motorway-casing", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "motorway"],
          paint: {{
            "line-color": "{ACCENT_COLOR}",
            "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2, 14, 9],
            "line-opacity": 0.25
          }}
        }},

        // Motorway fill
        {{
          id: "road-motorway", type: "line",
          source: "storm-tiles", "source-layer": "transportation",
          filter: ["==", ["get", "class"], "motorway"],
          paint: {{
            "line-color": "#CC5528",
            "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 14, 6]
          }}
        }},

        // ── Road Labels ───────────────────────────────────────────────────
        // Motorway + trunk: orange, bold, ref number preferred
        {{
          id: "road-label-motorway", type: "symbol",
          source: "storm-tiles", "source-layer": "transportation_name",
          filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk"]]],
          minzoom: 6,
          layout: {{
            "text-field": ["coalesce", ["get", "ref"], ["get", "name:latin"], ""],
            "text-font": ["Noto Sans Bold"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 14, 14],
            "symbol-placement": "line",
            "text-max-angle": 30,
            "text-padding": 10,
            "symbol-spacing": 400
          }},
          paint: {{
            "text-color": "#E89050",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 2
          }}
        }},
        // Primary + secondary: muted, regular weight
        {{
          id: "road-label-primary", type: "symbol",
          source: "storm-tiles", "source-layer": "transportation_name",
          filter: ["in", ["get", "class"], ["literal", ["primary", "secondary"]]],
          minzoom: 8,
          layout: {{
            "text-field": ["coalesce", ["get", "name:latin"], ["get", "ref"], ""],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 14, 13],
            "symbol-placement": "line",
            "text-max-angle": 30,
            "text-padding": 8,
            "symbol-spacing": 350
          }},
          paint: {{
            "text-color": "#A8A8C8",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 1.5
          }}
        }},
        // Tertiary: backed by road-secondary lines visible at zoom 8
        {{
          id: "road-label-tertiary", type: "symbol",
          source: "storm-tiles", "source-layer": "transportation_name",
          filter: ["==", ["get", "class"], "tertiary"],
          minzoom: 8,
          layout: {{
            "text-field": ["get", "name:latin"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "symbol-placement": "line",
            "text-max-angle": 30,
            "text-padding": 6,
            "symbol-spacing": 300
          }},
          paint: {{
            "text-color": "#888898",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 1.5
          }}
        }},
        // Minor / local: only when zoomed in (lines not visible before zoom 8)
        {{
          id: "road-label-minor", type: "symbol",
          source: "storm-tiles", "source-layer": "transportation_name",
          filter: ["in", ["get", "class"], ["literal", ["minor", "residential", "service"]]],
          minzoom: 8,
          layout: {{
            "text-field": ["get", "name:latin"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "symbol-placement": "line",
            "text-max-angle": 30,
            "text-padding": 6,
            "symbol-spacing": 300
          }},
          paint: {{
            "text-color": "#888898",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 1.5
          }}
        }},

        // ── Place Labels ──────────────────────────────────────────────────
        // States: shown early, always visible, very muted
        {{
          id: "state-label", type: "symbol",
          source: "storm-tiles", "source-layer": "place",
          filter: ["==", ["get", "class"], "state"],
          minzoom: 3,
          maxzoom: 7,
          layout: {{
            "text-field": ["get", "name:latin"],
            "text-font": ["Noto Sans Bold"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 4, 11, 7, 14],
            "text-transform": "uppercase",
            "text-letter-spacing": 0.15,
            "text-allow-overlap": false,
            "text-ignore-placement": false
          }},
          paint: {{
            "text-color": "#3A3A52",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 1
          }}
        }},
        // Cities: collision-filtered so they don't pile up
        {{
          id: "place-city", type: "symbol",
          source: "storm-tiles", "source-layer": "place",
          filter: ["in", ["get", "class"], ["literal", ["city", "town"]]],
          minzoom: 4,
          layout: {{
            "text-field": ["get", "name:latin"],
            "text-font": ["Noto Sans Bold"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 12, 16],
            "text-anchor": "center",
            "text-max-width": 8,
            "text-padding": 4,
            "text-allow-overlap": false,
            "text-ignore-placement": false
          }},
          paint: {{
            "text-color": "#C8CAD4",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 2
          }}
        }},
        // Villages: only shown when zoomed in, collision-filtered
        {{
          id: "place-village", type: "symbol",
          source: "storm-tiles", "source-layer": "place",
          filter: ["in", ["get", "class"], ["literal", ["village", "hamlet", "suburb"]]],
          minzoom: 9,
          layout: {{
            "text-field": ["get", "name:latin"],
            "text-font": ["Noto Sans Regular"],
            "text-size": ["interpolate", ["linear"], ["zoom"], 9, 9, 14, 13],
            "text-anchor": "center",
            "text-padding": 4,
            "text-allow-overlap": false,
            "text-ignore-placement": false
          }},
          paint: {{
            "text-color": "#8A8B9A",
            "text-halo-color": "#0D0D14",
            "text-halo-width": 1.5
          }}
        }}
      ]
    }};

    // ── Initialize Map ────────────────────────────────────────────────────
    const map = new maplibregl.Map({{
      container: "map",
      style: STORM_STYLE,
      center: [{DEFAULT_LON}, {DEFAULT_LAT}],
      zoom: {DEFAULT_ZOOM},
      minZoom: 3,
      maxZoom: 18,
      attributionControl: false,
      scrollZoom: true,
      touchZoomRotate: true,
      doubleClickZoom: true,
      keyboard: true
    }});

    map.addControl(new maplibregl.NavigationControl({{
      showCompass: false,
      showZoom: true,
      visualizePitch: false
    }}), "bottom-right");

    map.addControl(new maplibregl.AttributionControl({{
      compact: true
    }}), "top-right");

    map.addControl(new maplibregl.ScaleControl({{
      maxWidth: 120,
      unit: "imperial"
    }}), "bottom-left");

    map.on("load", function() {{
      console.log("STORM map loaded.");
      const glyphCheck = "{_STORM_BASE}/static/fonts/Noto%20Sans%20Regular/0-255.pbf";
      fetch(glyphCheck).then(r => console.log("Glyph check:", r.status, glyphCheck))
        .catch(err => console.error("Glyph check failed:", err));

      // ── Measure Tool Sources & Layers ───────────────────────────────────
      var empty = {{type:'FeatureCollection',features:[]}};
      map.addSource('measure-points', {{type:'geojson', data:empty}});
      map.addSource('measure-line',   {{type:'geojson', data:empty}});
      map.addSource('measure-label',  {{type:'geojson', data:empty}});
      map.addSource('measure-rubber', {{type:'geojson', data:empty}});

      map.addLayer({{id:'measure-rubber', type:'line', source:'measure-rubber',
        paint:{{'line-color':'#FFFFFF','line-width':1.5,
                'line-dasharray':[4,3],'line-opacity':0.45}}}});
      map.addLayer({{id:'measure-line', type:'line', source:'measure-line',
        paint:{{'line-color':'#FFFFFF','line-width':2}}}});
      map.addLayer({{id:'measure-label', type:'symbol', source:'measure-label',
        layout:{{'text-field':['get','label'],'text-size':11,
                 'text-font':['Noto Sans Bold'],
                 'text-anchor':'center',
                 'text-allow-overlap':false}},
        paint:{{'text-color':'#FFFFFF','text-halo-color':'#0A0A0F','text-halo-width':2}}}});
      map.addLayer({{id:'measure-points', type:'circle', source:'measure-points',
        paint:{{'circle-radius':5,'circle-color':'#FFFFFF',
                'circle-stroke-width':2,'circle-stroke-color':'#0A0A0F'}}}});

      // Deployment locations (historical truck positions)
      map.addSource('deploy-locs', {{type:'geojson', data:{{type:'FeatureCollection',features:[]}}}});
      map.addLayer({{
        id: 'deploy-locs-circles',
        type: 'circle',
        source: 'deploy-locs',
        layout: {{'visibility': 'none'}},
        paint: {{
          'circle-radius': 6,
          'circle-color': ['match', ['coalesce', ['get', 'rank_abi'], 0],
            1, '#2DC653', 2, '#A8C538', 3, '#FFD166', 4, '#FF8C42', 5, '#EF233C',
            '#888888'],
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#0A0A0F',
          'circle-opacity': 0.85
        }}
      }});
      // Flush any deploy locs data that arrived before the map was ready
      if (window._deployLocsData) {{
        map.getSource('deploy-locs').setData(JSON.parse(window._deployLocsData));
        window._deployLocsData = null;
      }}

      map.addSource('radar-stations', {{type:'geojson', data:empty}});
      map.addLayer({{
        id: 'radar-stations-circle',
        type: 'circle',
        source: 'radar-stations',
        layout: {{'visibility': 'none'}},
        paint: {{
          'circle-radius': 7,
          'circle-color': '#00CFFF',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#0A0A0F',
          'circle-opacity': 0.92
        }}
      }});
      map.addLayer({{
        id: 'radar-stations-label',
        type: 'symbol',
        source: 'radar-stations',
        layout: {{
          'visibility': 'none',
          'text-field': ['get', 'site_id'],
          'text-font': ['Noto Sans Bold'],
          'text-size': 11,
          'text-anchor': 'top',
          'text-offset': [0, 1.1],
          'text-allow-overlap': true,
          'text-ignore-placement': true
        }},
        paint: {{
          'text-color': '#E8EDF5',
          'text-halo-color': '#0A0A0F',
          'text-halo-width': 2
        }}
      }});
      map.on('mouseenter', 'radar-stations-circle', function() {{
        map.getCanvas().style.cursor = 'pointer';
      }});
      map.on('mouseleave', 'radar-stations-circle', function() {{
        map.getCanvas().style.cursor = '';
      }});
      if (window._radarStationsData) {{
        map.getSource('radar-stations').setData(JSON.parse(window._radarStationsData));
        window._radarStationsData = null;
      }}
      window.stormSetRadarStationsVisible(window._radarStationsVisible);

      // ── Sounding Station layer ────────────────────────────────────────────
      map.addSource('sounding-stations-src', {{type:'geojson', data:empty}});
      map.addLayer({{
        id: 'sounding-stations',
        type: 'circle',
        source: 'sounding-stations-src',
        layout: {{'visibility': 'none'}},
        paint: {{
          'circle-radius': 6,
          'circle-color': '#00E5FF',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#0A0A0F',
          'circle-opacity': 0.88
        }}
      }});
      map.addLayer({{
        id: 'sounding-stations-label',
        type: 'symbol',
        source: 'sounding-stations-src',
        layout: {{
          'visibility': 'none',
          'text-field': ['get', 'id'],
          'text-font': ['Noto Sans Bold'],
          'text-size': 10,
          'text-anchor': 'top',
          'text-offset': [0, 1.0],
          'text-allow-overlap': true,
          'text-ignore-placement': true
        }},
        paint: {{
          'text-color': '#E8EDF5',
          'text-halo-color': '#0A0A0F',
          'text-halo-width': 2
        }}
      }});
      map.on('mouseenter', 'sounding-stations', function() {{
        if (window._soundingObsModeActive) map.getCanvas().style.cursor = 'pointer';
      }});
      map.on('mouseleave', 'sounding-stations', function() {{
        if (window._soundingObsModeActive) map.getCanvas().style.cursor = '';
      }});
      map.on('click', 'sounding-stations', function(e) {{
        if (!window._soundingObsModeActive) return;
        var feat = e.features && e.features[0];
        if (!feat) return;
        var p = feat.properties;
        if (bridge) bridge.on_obs_station_click(
          p.id, p.name,
          feat.geometry.coordinates[1],
          feat.geometry.coordinates[0],
          p.elevation
        );
        e.originalEvent.stopPropagation();
      }});

      // ── GOES Satellite image overlay ─────────────────────────────────────
      // Uses a single image source (like the radar overlay) rather than tiled
      // WMS so that (a) frames can be cached and played back, and (b) nothing
      // appears outside the downloaded bbox.  The source is created on first
      // frame delivery by stormSetSatelliteFrame().

      // MESO sector outline boxes (GeoJSON polygons drawn over the satellite)
      map.addSource('meso-sectors', {{type:'geojson', data:empty}});
      map.addLayer({{
        id: 'meso-sectors-fill',
        type: 'fill',
        source: 'meso-sectors',
        layout: {{ 'visibility': 'none' }},
        paint: {{ 'fill-color': '#00CFFF', 'fill-opacity': 0.06 }}
      }});
      map.addLayer({{
        id: 'meso-sectors-line',
        type: 'line',
        source: 'meso-sectors',
        layout: {{ 'visibility': 'none' }},
        paint: {{
          'line-color': '#00CFFF',
          'line-width': 1.5,
          'line-dasharray': [5, 3],
          'line-opacity': 0.7
        }}
      }});
      map.addLayer({{
        id: 'meso-sectors-label',
        type: 'symbol',
        source: 'meso-sectors',
        layout: {{
          'visibility': 'none',
          'text-field': ['get', 'label'],
          'text-size': 11,
          'text-font': ['Noto Sans Bold'],
          'text-anchor': 'top-left',
          'text-offset': [0.4, 0.4],
          'text-allow-overlap': true
        }},
        paint: {{
          'text-color': '#00CFFF',
          'text-halo-color': '#0A0A0F',
          'text-halo-width': 1.5
        }}
      }});

      // ── Route overlay ────────────────────────────────────────────────────
      var emptyLine = {{type:'Feature', geometry:{{type:'LineString', coordinates:[]}}}};
      map.addSource('route', {{type:'geojson', data:emptyLine}});
      map.addLayer({{
        id: 'route-casing', type: 'line', source: 'route',
        layout: {{'line-join':'round','line-cap':'round'}},
        paint: {{'line-color':'#1A4A8A','line-width':8,'line-opacity':0.55}}
      }});
      map.addLayer({{
        id: 'route-line', type: 'line', source: 'route',
        layout: {{'line-join':'round','line-cap':'round'}},
        paint: {{'line-color':'#4A90E2','line-width':5,'line-opacity':0.9}}
      }});

      // Click-to-pick destination mode
      window._routePickMode = false;
      window._routePickConsumed = false;
      window.stormSetRoutePickMode = function(on) {{
        window._routePickMode = !!on;
        map.getCanvas().style.cursor = window._routePickMode ? 'crosshair' : '';
      }};

      map.on('click', function(e) {{
        if (!window._routePickMode) return;
        window._routePickMode = false;
        window._routePickConsumed = true;
        map.getCanvas().style.cursor = '';
        if (bridge) bridge.on_map_pick_for_route(e.lngLat.lat, e.lngLat.lng);
      }});

      // ── SPC + NWS hazard overlays (all default hidden) ──────────────────
      map.addSource('spc-cat', {{type:'geojson', data:empty}});
      map.addSource('spc-wind', {{type:'geojson', data:empty}});
      map.addSource('spc-hail', {{type:'geojson', data:empty}});
      map.addSource('spc-tor', {{type:'geojson', data:empty}});
      map.addSource('spc-watches', {{type:'geojson', data:empty}});
      map.addSource('spc-mds', {{type:'geojson', data:empty}});
      map.addSource('nws-warnings', {{type:'geojson', data:empty}});

      map.addLayer({{
        id: 'spc-cat-fill',
        type: 'fill',
        source: 'spc-cat',
        layout: {{'visibility': 'none'}},
        paint: {{
          'fill-color': [
            'match', ['get', 'cat'],
            'MRGL', '#80C580',
            'SLGHT', '#F6F67F',
            'ENH', '#E87038',
            'MDT', '#E84038',
            'HIGH', '#930093',
            '#80C580'
          ],
          'fill-opacity': 0.18
        }}
      }});
      map.addLayer({{
        id: 'spc-cat-line',
        type: 'line',
        source: 'spc-cat',
        layout: {{'visibility': 'none'}},
        paint: {{
          'line-color': [
            'match', ['get', 'cat'],
            'MRGL', '#80C580',
            'SLGHT', '#F6F67F',
            'ENH', '#E87038',
            'MDT', '#E84038',
            'HIGH', '#930093',
            '#80C580'
          ],
          'line-width': 2,
          'line-opacity': 0.85
        }}
      }});

      // ── Significant-area fill patterns ────────────────────────────────────
      // White ink on transparent tile — readable on the dark-mode map.
      //
      //  sig-hatch-cig1 – short dashes along a diagonal  (CIG1)
      //  sig-hatch-cig2 – solid continuous diagonal lines (CIG2 + SIGN)
      //  sig-hatch-cig3 – 4×4-pixel checkerboard          (CIG3)
      (function() {{
        function _mkImage(sz, fn) {{
          var d = new Uint8Array(sz * sz * 4);
          for (var y = 0; y < sz; y++) {{
            for (var x = 0; x < sz; x++) {{
              if (fn(x, y, sz)) {{
                var i = (y * sz + x) * 4;
                d[i] = 255; d[i+1] = 255; d[i+2] = 255; d[i+3] = 210;
              }}
            }}
          }}
          return {{width: sz, height: sz, data: d}};
        }}
        // CIG1 – short dashes: 3-px dash, 9-px gap on a 24-px tile.
        // The large gap makes these unmistakably dashed, not solid.
        var cig1 = _mkImage(24, function(x, y, sz) {{
          var diag = ((x - y) % sz + sz) % sz;
          return diag < 2 && (x + y) % 12 < 3;
        }});
        // CIG2 – solid diagonal lines: 2-px stripe every 10 px.
        var cig2 = _mkImage(10, function(x, y, sz) {{
          return ((x - y + sz) % sz) < 2;
        }});
        // CIG3 – checkerboard: alternating 4×4-px squares.
        var cig3 = _mkImage(16, function(x, y) {{
          return (Math.floor(x / 4) + Math.floor(y / 4)) % 2 === 0;
        }});
        ['cig1','cig2','cig3'].forEach(function(k, idx) {{
          var img = [cig1, cig2, cig3][idx];
          try {{
            map.addImage('sig-hatch-' + k, img);
            console.log('sig-hatch-' + k + ' registered OK');
          }} catch(e) {{
            console.warn('sig-hatch-' + k + ' registration failed:', e);
          }}
        }});
      }})();

      // Significant-area label values: SIGN (legacy tor) + CIG1/2/3 (SPC conditional intensity groups)
      var _SIG_LABELS = ['SIGN', 'CIG1', 'CIG2', 'CIG3'];
      var _sigFilter = ['any',
        ['in', ['get', 'LABEL'], ['literal', ['SIGN', 'CIG1', 'CIG2', 'CIG3']]],
        ['in', ['get', 'label'], ['literal', ['SIGN', 'CIG1', 'CIG2', 'CIG3']]]
      ];
      var _nonSignFilter = ['all',
        ['!', ['in', ['get', 'LABEL'], ['literal', ['SIGN', 'CIG1', 'CIG2', 'CIG3']]]],
        ['!', ['in', ['get', 'label'], ['literal', ['SIGN', 'CIG1', 'CIG2', 'CIG3']]]]
      ];

      function _addSpcProductLayers(name, colorExpr) {{
        // Probability fill + outline (non-sig features only)
        map.addLayer({{
          id: 'spc-' + name + '-fill',
          type: 'fill',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          filter: _nonSignFilter,
          paint: {{'fill-color': colorExpr, 'fill-opacity': 0.20}}
        }});
        map.addLayer({{
          id: 'spc-' + name + '-line',
          type: 'line',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          filter: _nonSignFilter,
          paint: {{'line-color': colorExpr, 'line-width': 1.5, 'line-opacity': 0.85}}
        }});

        // Shared base fill for all sig features (neutral gray so pattern shows on top)
        map.addLayer({{
          id: 'spc-' + name + '-sig-base',
          type: 'fill',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          filter: _sigFilter,
          paint: {{'fill-color': '#AAAAAA', 'fill-opacity': 0.14}}
        }});

        // Per-type pattern layers — SIGN and each CIG level get their own pattern.
        // SIGN (legacy) uses solid-line pattern same as CIG2.
        var _sigTypes = [
          {{label: 'SIGN', pat: 'sig-hatch-cig2'}},
          {{label: 'CIG1', pat: 'sig-hatch-cig1'}},
          {{label: 'CIG2', pat: 'sig-hatch-cig2'}},
          {{label: 'CIG3', pat: 'sig-hatch-cig3'}}
        ];
        _sigTypes.forEach(function(t) {{
          var _f = ['any',
            ['==', ['get', 'LABEL'], t.label],
            ['==', ['get', 'label'], t.label]
          ];
          var _hasPat = map.hasImage(t.pat);
          var _paint = _hasPat
            ? {{'fill-pattern': t.pat, 'fill-opacity': 0.16}}
            : {{'fill-color': '#FFFFFF', 'fill-opacity': 0.16}};
          map.addLayer({{
            id: 'spc-' + name + '-' + t.label.toLowerCase(),
            type: 'fill',
            source: 'spc-' + name,
            layout: {{'visibility': 'none'}},
            filter: _f,
            paint: _paint
          }});
        }});

        // White outline around all sig features
        map.addLayer({{
          id: 'spc-' + name + '-sig-line',
          type: 'line',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          filter: _sigFilter,
          paint: {{'line-color': '#FFFFFF', 'line-width': 1.5, 'line-opacity': 0.85}}
        }});
      }}

      // Wind/hail probability color scale (matches SPC official products).
      // SPC GeoJSON LABEL field is a decimal string ('0.05', '0.15', …).
      // Both decimal and integer-string keys are listed so either GeoJSON
      // format is handled gracefully.
      // Colors are tuned for readability on the app's dark background while
      // matching the SPC hue progression: tan → yellow → orange → red → magenta.
      var windHailColor = ['match', ['get', 'LABEL'],
        ['SIGN', 'CIG1', 'CIG2', 'CIG3'], 'rgba(0,0,0,0)',
        ['0.05', '5'],  '#8B5A2A',   // 5%  – warm tan/brown
        ['0.15', '15'], '#F5F500',   // 15% – bright yellow
        ['0.30', '30'], '#FF7700',   // 30% – orange
        ['0.45', '45'], '#EE2222',   // 45% – red
        ['0.60', '60'], '#EE00EE',   // 60% – magenta
        '#8B5A2A'];

      // Tornado probability color scale (matches SPC official products).
      // SPC hue progression: green → brown → yellow → red → magenta → purple → blue.
      // Dark-background-adjusted: greens and blues are brightened for visibility.
      var torColor = ['match', ['get', 'LABEL'],
        ['SIGN', 'CIG1', 'CIG2', 'CIG3'], 'rgba(0,0,0,0)',
        ['0.02', '2'],  '#00CC00',   // 2%  – bright green
        ['0.05', '5'],  '#A0522D',   // 5%  – sienna brown
        ['0.10', '10'], '#F5F500',   // 10% – yellow  (SPC yellow, not orange)
        ['0.15', '15'], '#EE2222',   // 15% – red
        ['0.30', '30'], '#EE00EE',   // 30% – magenta
        ['0.45', '45'], '#9922DD',   // 45% – purple
        ['0.60', '60'], '#2266CC',   // 60% – medium blue (brightened for dark bg)
        '#00CC00'];

      _addSpcProductLayers('wind', windHailColor);
      _addSpcProductLayers('hail', windHailColor);
      _addSpcProductLayers('tor', torColor);

      map.addLayer({{
        id: 'spc-watches-fill',
        type: 'fill',
        source: 'spc-watches',
        layout: {{'visibility': 'none'}},
        paint: {{
          'fill-color': ['coalesce', ['get', 'watch_color'], '#4169E1'],
          'fill-opacity': 0.18
        }}
      }});
      map.addLayer({{
        id: 'spc-watches-line',
        type: 'line',
        source: 'spc-watches',
        layout: {{'visibility': 'none'}},
        paint: {{
          'line-color': ['coalesce', ['get', 'watch_color'], '#4169E1'],
          'line-width': 2,
          'line-opacity': 0.9
        }}
      }});

      map.addLayer({{
        id: 'spc-mds-fill',
        type: 'fill',
        source: 'spc-mds',
        layout: {{'visibility': 'none'}},
        paint: {{
          'fill-color': '#FF66CC',
          'fill-opacity': 0.14
        }}
      }});
      map.addLayer({{
        id: 'spc-mds-line',
        type: 'line',
        source: 'spc-mds',
        layout: {{'visibility': 'none'}},
        paint: {{
          'line-color': '#FF66CC',
          'line-width': 2,
          'line-opacity': 0.9
        }}
      }});

      map.addLayer({{
        id: 'nws-warnings-fill',
        type: 'fill',
        source: 'nws-warnings',
        layout: {{'visibility': 'none'}},
        paint: {{
          'fill-color': ['coalesce', ['get', 'nws_color'], '#FFD700'],
          'fill-opacity': 0.18
        }}
      }});
      map.addLayer({{
        id: 'nws-warnings-line',
        type: 'line',
        source: 'nws-warnings',
        layout: {{'visibility': 'none'}},
        paint: {{
          'line-color': ['coalesce', ['get', 'nws_color'], '#FFD700'],
          'line-width': 2,
          'line-opacity': 0.9
        }}
      }});

      // ── Drawing preview (rubber-band + confirmed points) ─────────────────
      var emptyFC = {{type:'FeatureCollection',features:[]}};
      map.addSource('drawing-preview-line', {{type:'geojson', data:emptyFC}});
      map.addSource('drawing-preview-dots', {{type:'geojson', data:emptyFC}});

      map.addLayer({{
        id: 'drawing-preview-line', type: 'line', source: 'drawing-preview-line',
        paint: {{
          'line-color': '#E8EAF0',
          'line-width': 2,
          'line-opacity': 0.7,
          'line-dasharray': [6, 4]
        }}
      }});
      map.addLayer({{
        id: 'drawing-preview-dots', type: 'circle', source: 'drawing-preview-dots',
        paint: {{
          'circle-radius': 4,
          'circle-color': '#E8EAF0',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#0A0A0F',
          'circle-opacity': 0.85
        }}
      }});

      // ── CWA overlay (NWS County Warning Areas) ───────────────────────────
      map.addSource('cwa', {{type:'geojson', data:{{type:'FeatureCollection',features:[]}}}});
      map.addLayer({{
        id: 'cwa-fill',
        type: 'fill',
        source: 'cwa',
        layout: {{ 'visibility': 'none' }},
        paint: {{ 'fill-color': '#FFCC00', 'fill-opacity': 0.18 }}
      }});
      map.addLayer({{
        id: 'cwa-line',
        type: 'line',
        source: 'cwa',
        layout: {{ 'visibility': 'none' }},
        paint: {{ 'line-color': '#FFCC00', 'line-width': 1.5, 'line-opacity': 0.9 }}
      }});
      map.addLayer({{
        id: 'cwa-label',
        type: 'symbol',
        source: 'cwa',
        layout: {{
          'visibility': 'none',
          'text-field': ['coalesce', ['get', 'WFO'], ['get', 'CWA']],
          'text-font': ['Noto Sans Bold'],
          'text-size': 12,
          'text-anchor': 'center',
          'text-offset': [0, 0],
          'text-allow-overlap': true,
          'text-ignore-placement': true
        }},
        paint: {{ 'text-color': '#E8EDF5', 'text-halo-color': '#0A0A0F', 'text-halo-width': 2 }}
      }});

      // Restore any CWA data queued before the map was ready
      if (window._cwaData) {{ try {{ map.getSource('cwa').setData(JSON.parse(window._cwaData)); }} catch(e) {{}} window._cwaData = null; }}

      // Pointer cursor on CWA polygons when visible
      map.on('mouseenter', 'cwa-fill', function() {{ map.getCanvas().style.cursor = 'pointer'; }});
      map.on('mouseleave', 'cwa-fill', function() {{ map.getCanvas().style.cursor = ''; }});

      // JS bridge functions for CWA data
      window.stormSetCwaGeoJSON = function(geojsonStr) {{
        var src = map.getSource('cwa');
        if (!src) {{ window._cwaData = geojsonStr; return; }}
        try {{ src.setData(JSON.parse(geojsonStr)); }} catch(e) {{ console.warn('CWA setData failed', e); }}
      }};
      window.stormSetCwaVisible = function(visible) {{
        var v = visible ? 'visible' : 'none';
        ['cwa-fill','cwa-line','cwa-label'].forEach(function(lid) {{ if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', v); }});
        if (!visible) {{ var tip = document.getElementById('hazard-tooltip'); if (tip) tip.style.display = 'none'; }}
      }};
    }});

    map.on("error", function(e) {{
      const err = e && e.error ? e.error : null;
      const msg = (err && (err.message || err.statusText)) || "unknown";
      const src = e && e.sourceId ? (" source=" + e.sourceId) : "";
      const url = err && err.url ? (" url=" + err.url) : "";
      const tile = (e && e.tile && typeof e.tile.z !== "undefined")
        ? (" tile=" + e.tile.z + "/" + e.tile.x + "/" + e.tile.y)
        : "";
      if (String(msg).toLowerCase().includes("abort")) {{
        console.warn("MapLibre warning: " + msg + src + url + tile);
      }} else {{
        console.error("MapLibre error: " + msg + src + url + tile);
      }}
    }});

    // ── SPC tooltip helpers (defined once, used in mousemove) ─────────────
    var _SIG_LABEL_SET = {{SIGN:1, CIG1:1, CIG2:1, CIG3:1}};

    // Convert a raw SPC LABEL value ('0.05', '5', 'CIG1', etc.) to a
    // human-readable display string.
    function _spcPctLabel(raw) {{
      if (!raw || raw === '\u2014') return '\u2014';
      if (_SIG_LABEL_SET[raw]) return raw;   // CIG1/CIG2/CIG3/SIGN pass through
      var n = parseFloat(raw);
      if (isNaN(n)) return raw;
      var pct = (n > 0 && n < 1) ? Math.round(n * 100) : Math.round(n);
      return pct + '%';
    }}

    // Build a tooltip label for a probabilistic product.  Handles the case
    // where a CIG/SIGN overlay and a probability polygon both exist at the
    // cursor (e.g. CIG1 hail over 15% hail polygon → "Hail: 15% (CIG1)").
    function _spcProbLabel(prefix, allHits, srcName) {{
      var srcHits = allHits.filter(function(h) {{ return h.source === srcName; }});
      if (!srcHits.length) return '';
      var _sigHit = null, _probHit = null;
      srcHits.forEach(function(h) {{
        var lv = (h.properties.LABEL || h.properties.label || '');
        if (_SIG_LABEL_SET[lv]) {{ if (!_sigHit) _sigHit = h; }}
        else {{ if (!_probHit) _probHit = h; }}
      }});
      var sigLbl = _sigHit ? (_sigHit.properties.LABEL || _sigHit.properties.label) : null;
      var probRaw = _probHit
        ? (_probHit.properties.LABEL || _probHit.properties.label ||
           String(_probHit.properties.DN || _probHit.properties.dn || ''))
        : null;
      if (sigLbl && probRaw) {{
        // e.g. "Hail: 15% (CIG1)"  or  "Sig Tor – 10%"
        if (sigLbl === 'SIGN') return 'Sig ' + prefix + ' \u2013 ' + _spcPctLabel(probRaw);
        return prefix + ': ' + _spcPctLabel(probRaw) + ' (' + sigLbl + ')';
      }}
      if (sigLbl) {{ return 'Sig ' + prefix + (sigLbl !== 'SIGN' ? ' \u2013 ' + sigLbl : ''); }}
      if (probRaw) {{ return prefix + ': ' + _spcPctLabel(probRaw); }}
      return '';
    }}

    // ── Event Listeners ───────────────────────────────────────────────────
    map.on("mousemove", function(e) {{
      if (bridge) bridge.on_map_move(e.lngLat.lat, e.lngLat.lng, map.getZoom());
      if (window._measureAnchor && map.getSource('measure-rubber')) {{
        map.getSource('measure-rubber').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',geometry:{{type:'LineString',
            coordinates:[window._measureAnchor,[e.lngLat.lng,e.lngLat.lat]]}}}}
        ]}});
      }}
      if (window._stormDrawingActive && window._drawingConfirmedPts && window._drawingConfirmedPts.length > 0) {{
        window._drawingRubberPt = [e.lngLat.lng, e.lngLat.lat];
        _updateDrawingPreviewGeoJSON();
      }}
      if (window._stormDrawingDrag && window._stormDrawingDrag.dragging) {{
        var _dd = window._stormDrawingDrag;
        var _deltaLng = e.lngLat.lng - _dd.startLngLat[0];
        var _deltaLat = e.lngLat.lat - _dd.startLngLat[1];
        var _nextCoords = _dd.originalCoords.map(function(c) {{
          return [c[0] + _deltaLat, c[1] + _deltaLng];
        }});
        _updateDrawingData(_dd.id, _nextCoords);
        return;
      }}
      if (window._stormConeDrag && window._stormConeDrag.dragging) {{
        var _cd = window._stormConeDrag;
        var _coneLng = e.lngLat.lng;
        var _coneLat = e.lngLat.lat;
        if (_cd.id) {{
          var _moveDeltaLng = _coneLng - _cd.startLngLat[0];
          var _moveDeltaLat = _coneLat - _cd.startLngLat[1];
          _updateStormConeData(_cd.id, _translateGeoJSON(_cd.originalData, _moveDeltaLng, _moveDeltaLat), _cd.originalAnchor[0] + _moveDeltaLng, _cd.originalAnchor[1] + _moveDeltaLat);
        }}
        return;
      }}

      var _htip = document.getElementById('hazard-tooltip');
      if (_htip) {{
        var _hazardLayers = [
          'spc-cat-fill',
          'spc-tor-fill','spc-tor-sig-base',
          'spc-wind-fill','spc-wind-sig-base',
          'spc-hail-fill','spc-hail-sig-base',
          'spc-watches-fill','spc-mds-fill','nws-warnings-fill'
        ].filter(function(l) {{
          return map.getLayer(l) &&
                 map.getLayoutProperty(l, 'visibility') === 'visible';
        }});
        var _hits = _hazardLayers.length > 0
          ? map.queryRenderedFeatures(e.point, {{layers: _hazardLayers}})
          : [];
        if (_hits.length > 0) {{
          // Build a label for every unique product under the cursor.
          var _tipParts = [];
          var _tipSeen = {{}};
          // Probabilistic layers first (tor/wind/hail) — _spcProbLabel merges sig+prob.
          [['spc-tor','Tor'],['spc-wind','Wind'],['spc-hail','Hail']].forEach(function(pv) {{
            var l = _spcProbLabel(pv[1], _hits, pv[0]);
            if (l) _tipParts.push(l);
          }});
          _hits.forEach(function(h) {{
            var src = h.source || '';
            var props = h.properties || {{}};
            var lbl = '', key = '';
            if (src === 'spc-cat') {{
              key = 'spc-cat';
              if (!_tipSeen[key]) {{
                var _catNames = {{MRGL:'Marginal',SLGHT:'Slight',ENH:'Enhanced',MDT:'Moderate',HIGH:'High'}};
                lbl = _catNames[props.cat] || props.cat || 'Outlook';
              }}
            }} else if (src === 'spc-watches') {{
              key = 'watch:' + (props.watch_num || props.event || '');
              if (!_tipSeen[key]) lbl = props.event || 'Watch';
            }} else if (src === 'spc-mds') {{
              key = 'md:' + (props.name || '');
              if (!_tipSeen[key]) lbl = props.name || 'Mesoscale Discussion';
            }} else if (src === 'nws-warnings') {{
              key = 'warn:' + (props.event || '') + ':' + (props.wfo || '');
              if (!_tipSeen[key]) lbl = props.prod_type || props.event || 'Warning';
            }}
            if (key && lbl && !_tipSeen[key]) {{
              _tipSeen[key] = true;
              _tipParts.push(lbl);
            }}
          }});
          var _lbl = _tipParts.join(' \u2502 ');
          if (_lbl) {{
            _htip.textContent = _lbl;
            var _mx = e.originalEvent.clientX;
            var _my = e.originalEvent.clientY;
            var _mc = map.getContainer().getBoundingClientRect();
            _htip.style.left = (_mx - _mc.left + 14) + 'px';
            _htip.style.top  = (_my - _mc.top  - 10) + 'px';
            _htip.style.display = 'block';
          }} else {{
            _htip.style.display = 'none';
          }}
        }} else {{
          // No hazard layer hit — check for CWA polygon under cursor first
          if (map.getLayer('cwa-fill') && map.getLayoutProperty('cwa-fill','visibility') === 'visible') {{
            var cwaHits = map.queryRenderedFeatures(e.point, {{layers: ['cwa-fill']}}) || [];
            if (cwaHits.length > 0) {{
              var props = cwaHits[0].properties || {{}};
              var wfo = props.WFO || props.CWA || props.wfo || props.cwa || '';
              if (wfo) {{
                _htip.textContent = wfo;
                var _mx = e.originalEvent.clientX;
                var _my = e.originalEvent.clientY;
                var _mc = map.getContainer().getBoundingClientRect();
                _htip.style.left = (_mx - _mc.left + 14) + 'px';
                _htip.style.top  = (_my - _mc.top  - 10) + 'px';
                _htip.style.display = 'block';
                return;
              }}
            }}
          }}
          // No hazard layer hit — check for a nearby surface station.
          var _stLabel = null;
          if (window._stormSurfacePlotsVisible) {{
            var _reg = window._stormSurfaceRegistry || {{}};
            var _threshold = 15;
            var _bestDist  = Infinity;
            var _ids = Object.keys(_reg);
            for (var _si = 0; _si < _ids.length; _si++) {{
              var _st = _reg[_ids[_si]];
              var _sp = map.project([_st.lon, _st.lat]);
              var _dx = _sp.x - e.point.x;
              var _dy = _sp.y - e.point.y;
              var _d  = Math.sqrt(_dx * _dx + _dy * _dy);
              if (_d < _threshold && _d < _bestDist) {{
                _bestDist = _d;
                _stLabel  = _st.label;
              }}
            }}
          }}
          if (_stLabel) {{
            var _smc = map.getContainer().getBoundingClientRect();
            _htip.textContent = _stLabel;
            _htip.style.left  = (e.originalEvent.clientX - _smc.left + 14) + 'px';
            _htip.style.top   = (e.originalEvent.clientY - _smc.top  - 10) + 'px';
            _htip.style.display = 'block';
          }} else {{
            _htip.style.display = 'none';
          }}
        }}
      }}
    }});

    map.on("click", function(e) {{
      if (window._stormSuppressNextClick) {{
        window._stormSuppressNextClick = false;
        return;
      }}
      // Route pick was already handled by the earlier listener — skip everything
      if (window._routePickConsumed) {{
        window._routePickConsumed = false;
        return;
      }}
      // In sounding mode: capture lat/lon and forward to Python — skip all other handling
      if (window._soundingModeActive) {{
        if (bridge) bridge.on_sounding_click(e.lngLat.lat, e.lngLat.lng);
        return;
      }}
      // In drawing mode: skip all hit detection, just emit map_click (point placement)
      if (window._stormDrawingActive) {{
        if (bridge) bridge.on_map_click(e.lngLat.lat, e.lngLat.lng);
        return;
      }}
      // In annotation placement mode: always place, do not open existing features.
      var mapEl = document.getElementById('map');
      if (mapEl && mapEl.classList.contains('annotating')) {{
        if (bridge) bridge.on_map_click(e.lngLat.lat, e.lngLat.lng);
        return;
      }}
      // In measure mode: always forward as map click, skip hit detection
      if (mapEl && mapEl.classList.contains('measuring')) {{
        if (bridge) bridge.on_map_click(e.lngLat.lat, e.lngLat.lng);
        return;
      }}
      // Check drawing hits (fronts + custom shapes)
      const drawIds = Object.keys(window._stormDrawings || {{}});
      const hitLayers = [];
      drawIds.forEach(function(id) {{
        ['drawing-hit-', 'drawing-hit-fill-', 'drawing-lbl-'].forEach(function(pfx) {{
          const lid = pfx + id;
          if (map.getLayer(lid)) hitLayers.push(lid);
        }});
      }});
      if (hitLayers.length > 0) {{
        const drawHits = map.queryRenderedFeatures(e.point, {{layers: hitLayers}});
        if (drawHits.length > 0) {{
          if (bridge) bridge.on_drawing_click(drawHits[0].properties.drawing_id);
          return;
        }}
      }}
      // Intercept storm cone clicks before firing map_clicked
      const coneIds = Object.keys(window._stormCones || {{}});
      const fillLayers = coneIds.map(id => 'storm-cone-fill-' + id).filter(l => map.getLayer(l));
      if (fillLayers.length > 0) {{
        const hits = map.queryRenderedFeatures(e.point, {{layers: fillLayers}});
        if (hits.length > 0) {{
          if (bridge) bridge.on_storm_cone_click(hits[0].properties.cone_id);
          return;
        }}
      }}
      const radarStationLayers = ['radar-stations-circle', 'radar-stations-label'].filter(function(l) {{
        return map.getLayer(l) && map.getLayoutProperty(l, 'visibility') === 'visible';
      }});
      if (radarStationLayers.length > 0) {{
        const radarHits = map.queryRenderedFeatures(e.point, {{layers: radarStationLayers}});
        if (radarHits.length > 0) {{
          const siteId = radarHits[0].properties && radarHits[0].properties.site_id;
          if (siteId) {{
            window.stormSetRadarStationsVisible(false);
            // Hide radar overlay immediately in JS — avoids a Python→JS
            // round-trip that freezes the renderer during click processing.
            if (map.getLayer("radar-overlay"))
              map.setPaintProperty("radar-overlay", "raster-opacity", 0);
            if (bridge) bridge.on_radar_station_click(siteId);
            return;
          }}
        }}
      }}
      // Check SPC hazard polygon clicks (outlook + MDs) — lower priority than drawings/cones
      var spcClickLayers = ['spc-cat-fill', 'spc-mds-fill', 'spc-watches-fill', 'nws-warnings-fill'].filter(function(l) {{ return map.getLayer(l); }});
      if (spcClickLayers.length > 0) {{
        var spcHits = map.queryRenderedFeatures(e.point, {{layers: spcClickLayers}});
        if (spcHits.length > 0) {{
          // Collect one entry per unique text product (deduplicate overlapping polygons).
          var _clickSeen = {{}};
          var _clickFeatures = [];
          spcHits.forEach(function(hit) {{
            var src = hit.source;
            var props = hit.properties || {{}};
            var key;
            if (src === 'spc-cat')          key = 'spc-cat';
            else if (src === 'spc-mds')     key = 'spc-mds:'      + (props.name      || '');
            else if (src === 'spc-watches') key = 'spc-watches:'   + (props.watch_num || props.event || '');
            else if (src === 'nws-warnings') key = 'nws-warnings:' + (props.event     || '') + ':' + (props.wfo || '');
            else return;
            if (!_clickSeen[key]) {{
              _clickSeen[key] = true;
              _clickFeatures.push({{source: src, properties: props}});
            }}
          }});
          if (_clickFeatures.length > 0) {{
            if (bridge) bridge.on_feature_click(JSON.stringify(_clickFeatures));
            return;
          }}
        }}
      }}
      if (bridge) bridge.on_map_click(e.lngLat.lat, e.lngLat.lng);
    }});

    map.on("dblclick", function(e) {{
      if (window._stormDrawingActive) {{
        e.preventDefault();
        if (bridge) bridge.on_map_dblclick(e.lngLat.lat, e.lngLat.lng);
      }}
    }});

    map.on("mousedown", function(e) {{
      if (window._stormDrawingDrag && window._stormDrawingDrag.id) {{
        var _dragId = window._stormDrawingDrag.id;
        var _dragLayers = ['drawing-hit-' + _dragId, 'drawing-hit-fill-' + _dragId, 'drawing-lbl-' + _dragId]
          .filter(function(layerId) {{ return map.getLayer(layerId); }});
        if (_dragLayers.length > 0) {{
          var _dragHits = map.queryRenderedFeatures(e.point, {{layers: _dragLayers}});
          if (_dragHits.length > 0) {{
            var _drawing = window._stormDrawings[_dragId];
            if (_drawing) {{
              window._stormDrawingDrag.dragging = true;
              window._stormDrawingDrag.startLngLat = [e.lngLat.lng, e.lngLat.lat];
              window._stormDrawingDrag.originalCoords = JSON.parse(JSON.stringify(_drawing.coordinates || []));
              window._stormSuppressNextClick = true;
              map.dragPan.disable();
              map.getCanvas().style.cursor = 'grabbing';
              e.preventDefault();
              return;
            }}
          }}
        }}
      }}
      if (window._stormConeDrag && (window._stormConeDrag.id || window._stormConePlacementActive)) {{
        if (window._stormConeDrag.id) {{
          var _coneId = window._stormConeDrag.id;
          var _coneLayers = ['storm-cone-fill-' + _coneId, 'storm-cone-outline-' + _coneId, 'storm-cone-ribs-' + _coneId, 'storm-cone-labels-' + _coneId]
            .filter(function(layerId) {{ return map.getLayer(layerId); }});
          if (_coneLayers.length > 0) {{
            var _coneHits = map.queryRenderedFeatures(e.point, {{layers: _coneLayers}});
            if (_coneHits.length === 0) return;
          }} else {{
            return;
          }}
        }}
        window._stormConeDrag.dragging = true;
        window._stormConeDrag.startLngLat = [e.lngLat.lng, e.lngLat.lat];
        if (window._stormConeDrag.id) {{
          var _activeCone = window._stormCones[window._stormConeDrag.id];
          if (!_activeCone) return;
          window._stormConeDrag.originalAnchor = (_activeCone.anchor || [e.lngLat.lng, e.lngLat.lat]).slice();
          window._stormConeDrag.originalData = JSON.parse(JSON.stringify(_activeCone.data));
        }}
        window._stormSuppressNextClick = true;
        map.dragPan.disable();
        map.getCanvas().style.cursor = 'grabbing';
        e.preventDefault();
      }}
    }});

    map.on("mouseup", function(e) {{
      if (window._stormDrawingDrag && window._stormDrawingDrag.dragging) {{
        var _done = window._stormDrawingDrag;
        window._stormDrawingDrag.dragging = false;
        map.dragPan.enable();
        map.getCanvas().style.cursor = _done.id ? 'grab' : '';
        if (bridge) {{
          var _coordsJson = JSON.stringify((window._stormDrawings[_done.id] || {{coordinates: []}}).coordinates || []);
          bridge.on_drawing_drag_end(_done.id, _coordsJson);
        }}
        return;
      }}
      if (window._stormConeDrag && window._stormConeDrag.dragging) {{
        var _coneDone = window._stormConeDrag;
        window._stormConeDrag.dragging = false;
        map.dragPan.enable();
        map.getCanvas().style.cursor = (_coneDone.id || window._stormConePlacementActive) ? 'grab' : '';
        if (_coneDone.id) {{
          var _finalCone = window._stormCones[_coneDone.id];
          if (_finalCone && bridge) {{
            bridge.on_storm_cone_drag_end(_coneDone.id, _finalCone.anchor[1], _finalCone.anchor[0]);
          }}
        }} else if (window._stormConePlacementActive && bridge) {{
          bridge.on_storm_cone_place_drag_end(e.lngLat.lat, e.lngLat.lng);
        }}
      }}
    }});

    // ── Python-callable Functions ─────────────────────────────────────────
    var _vIcons = {{
      car: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="3" y="9" width="18" height="8" rx="2" fill="'+c+'"/><rect x="6" y="5" width="12" height="7" rx="2" fill="'+c+'" opacity="0.85"/><circle cx="7.5" cy="18" r="2.2" fill="'+c+'"/><circle cx="16.5" cy="18" r="2.2" fill="'+c+'"/></svg>'; }},
      radar: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="9" r="7" fill="'+c+'"/><rect x="11" y="16" width="2" height="3" fill="'+c+'"/><rect x="7" y="19" width="10" height="2" rx="1" fill="'+c+'"/></svg>'; }},
      lidar: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="7" y="14" width="10" height="7" fill="'+c+'"/><line x1="7" y1="14" x2="2" y2="14" stroke="'+c+'" stroke-width="4" stroke-linecap="square"/><line x1="17" y1="14" x2="22" y2="14" stroke="'+c+'" stroke-width="4" stroke-linecap="square"/><circle cx="12" cy="11" r="2.5" fill="'+c+'"/><rect x="11" y="21" width="2" height="3" fill="'+c+'"/></svg>'; }},
      mesonet: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect x="11" y="12" width="2" height="10" rx="1" fill="'+c+'"/><line x1="3" y1="10" x2="22" y2="10" stroke="'+c+'" stroke-width="1.5"/><circle cx="12" cy="10" r="1.5" fill="'+c+'"/><line x1="3" y1="5" x2="3" y2="15" stroke="'+c+'" stroke-width="3" stroke-linecap="round"/><polygon points="17,10 22,10 22,4" fill="'+c+'"/></svg>'; }},
      drone: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><line x1="7" y1="7" x2="17" y2="17" stroke="'+c+'" stroke-width="2"/><line x1="17" y1="7" x2="7" y2="17" stroke="'+c+'" stroke-width="2"/><rect x="10" y="10" width="4" height="4" rx="1" fill="'+c+'"/><circle cx="5.5" cy="5.5" r="2.5" fill="'+c+'" opacity="0.85"/><circle cx="18.5" cy="5.5" r="2.5" fill="'+c+'" opacity="0.85"/><circle cx="5.5" cy="18.5" r="2.5" fill="'+c+'" opacity="0.85"/><circle cx="18.5" cy="18.5" r="2.5" fill="'+c+'" opacity="0.85"/></svg>'; }},
      hailcam: function(c) {{ return '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M21.5,12 L18.1,15.5 L16.8,20.2 L12,19 L7.2,20.2 L5.9,15.5 L2.5,12 L5.9,8.5 L7.2,3.8 L12,5 L16.8,3.8 L18.1,8.5 Z" fill="'+c+'"/></svg>'; }},
    }};

    window.stormAddVehicle = function(id, lat, lon, color, iconType) {{
      const existing = document.getElementById("vehicle-" + id);
      if (existing) existing.remove();

      var _iconFn = _vIcons[iconType] || _vIcons.car;
      var _c = color || "{ACCENT_COLOR}";
      const el = document.createElement("div");
      el.id = "vehicle-" + id;
      el.style.cssText = "width:28px;height:28px;cursor:pointer;";
      el.style.filter = "drop-shadow(0 0 5px " + _c + ")";
      el.innerHTML = _iconFn(_c);

      el.addEventListener("mouseenter", function(e) {{
        var tip = document.getElementById("hazard-tooltip");
        if (tip) {{
          tip.textContent = id;
          tip.style.display = "block";
          tip.style.left = (e.clientX + 14) + "px";
          tip.style.top  = (e.clientY - 36) + "px";
        }}
      }});
      el.addEventListener("mousemove", function(e) {{
        var tip = document.getElementById("hazard-tooltip");
        if (tip && tip.style.display !== "none") {{
          tip.style.left = (e.clientX + 14) + "px";
          tip.style.top  = (e.clientY - 36) + "px";
        }}
      }});
      el.addEventListener("mouseleave", function() {{
        var tip = document.getElementById("hazard-tooltip");
        if (tip) tip.style.display = "none";
      }});

      new maplibregl.Marker({{ element: el, anchor: "center" }})
        .setLngLat([lon, lat])
        .addTo(map);
    }};

    window.stormRemoveVehicle = function(id) {{
      const el = document.getElementById("vehicle-" + id);
      if (el) el.remove();
    }};

    window.stormFlyTo = function(lat, lon, zoom) {{
      map.flyTo({{
        center: [lon, lat],
        zoom: zoom || map.getZoom(),
        duration: 800
      }});
    }};

    // ── Follow mode ────────────────────────────────────────────────────────
    var _followActive = false;

    window.stormSetFollow = function(enabled) {{
      _followActive = !!enabled;
    }};

    window.stormFollowMove = function(lat, lon) {{
      if (!_followActive) return;
      map.easeTo({{ center: [lon, lat], duration: 300 }});
    }};

    map.on('dragstart', function() {{
      if (_followActive && bridge) {{
        _followActive = false;
        bridge.on_user_drag();
      }}
    }});

    // ── Annotations ───────────────────────────────────────────────────────
    const _ANNO_TYPES = {{
      road_closure: {{symbol:'\u2715', color:'#E53935'}},
      construction: {{symbol:'\u25B2', color:'#FFD166'}},
      flooded:      {{symbol:'~',       color:'#4A9EFF'}},
      downed_lines: {{symbol:'\u26A1',  color:'#FFD166'}},
      debris:       {{symbol:'!',       color:'#FF6B35'}},
    }};

    window._stormAnnotations = {{}};

    window.stormAddAnnotation = function(id, lat, lon, typeKey, label) {{
      // storm motion is rendered as a cone, never as an annotation marker
      if (typeKey === 'storm_motion') {{
        if (window._stormAnnotations[id]) {{
          window._stormAnnotations[id].remove();
          delete window._stormAnnotations[id];
        }}
        return;
      }}
      if (window._stormAnnotations[id]) {{
        window._stormAnnotations[id].remove();
        delete window._stormAnnotations[id];
      }}
      // fork: fixed screen overlay — always centered on the map, ignores pan/zoom
      if (typeKey === 'fork') {{
        var forkEl = document.createElement('div');
        forkEl.id = 'storm-fork-overlay-' + id;
        forkEl.style.cssText = (
          'position:absolute;top:50%;left:50%;'
          + 'transform:translate(-50%,-50%);'
          + 'display:flex;flex-direction:column;align-items:center;'
          + 'pointer-events:auto;cursor:pointer;user-select:none;z-index:50;'
        );
        forkEl.innerHTML =
          '<svg viewBox="0 0 80 245" xmlns="http://www.w3.org/2000/svg"'
          + ' style="width:130px;height:400px;fill:#C0C0C0;'
          + '        filter:drop-shadow(0 0 20px rgba(192,192,192,0.6));">'
          + '  <rect x="4"  y="2" width="11" height="72" rx="5.5"/>'
          + '  <rect x="23" y="2" width="11" height="72" rx="5.5"/>'
          + '  <rect x="43" y="2" width="11" height="72" rx="5.5"/>'
          + '  <rect x="62" y="2" width="11" height="72" rx="5.5"/>'
          + '  <path d="M4,56 C4,82 32,100 32,100 L48,100 C48,100 73,82 73,56 Z"/>'
          + '  <rect x="32" y="98" width="16" height="145" rx="7"/>'
          + '</svg>'
          + '<div style="color:#C0C0C0;font-size:22px;font-weight:700;letter-spacing:6px;'
          + '  font-family:monospace;text-shadow:0 0 12px rgba(192,192,192,0.8);'
          + '  white-space:nowrap;margin-top:6px;">'
          + (label || 'FORK').toUpperCase() + '</div>';
        forkEl.addEventListener('click', function(e) {{
          e.stopPropagation();
          if (bridge) bridge.on_annotation_click(id);
        }});
        map.getContainer().appendChild(forkEl);
        // Store as a plain object (not a Marker) so stormRemoveAnnotation can clean it up
        window._stormAnnotations[id] = {{
          _isFork: true,
          _el: forkEl,
          remove: function() {{ if (forkEl.parentNode) forkEl.parentNode.removeChild(forkEl); }},
          _cleanupZoom: function() {{}}
        }};
        return;
      }}
      const cfg = _ANNO_TYPES[typeKey] || {{symbol:'?', color:'#FF6B35'}};
      const el = document.createElement('div');
      if (cfg.supercell) {{
        el.style.cssText = [
          'width:34px', 'height:34px',
          'display:flex', 'align-items:center', 'justify-content:center',
          'cursor:pointer', 'user-select:none',
          'filter: drop-shadow(0 0 6px ' + cfg.color + '88)',
        ].join(';');
        el.innerHTML = `
          <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
            <path d="M4 8 C7 4.7 12.2 3.8 16.4 5.4 C19.9 6.7 20.8 9.9 19.1 12.4
                     C17.5 14.8 13.8 14.5 11.8 15.9 C10.1 17.2 9.8 19.9 12 20.8
                     C8.5 20.9 6.1 18.6 6.5 15.4 C6.8 13.3 8.4 12 10 11
                     C7.1 10.7 4.9 9.5 4 8 Z"
                  fill="none" stroke="${{cfg.color}}" stroke-width="2.2"
                  stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        `;
      }} else {{
        el.style.cssText = [
          'width:32px', 'height:32px', 'border-radius:50%',
          'background-color:' + cfg.color + '33',
          'border:2px solid ' + cfg.color,
          'display:flex', 'align-items:center', 'justify-content:center',
          'font-size:16px', 'line-height:1', 'cursor:pointer', 'user-select:none',
          'box-shadow:0 0 8px ' + cfg.color + '88',
        ].join(';');
        el.textContent = cfg.symbol;
      }}
      el.title = label;
      el.addEventListener('click', function(e) {{
        e.stopPropagation();
        // suppress popup when routing pick or measure tool is active
        if (window._routePickMode) {{
          window._routePickMode = false;
          map.getCanvas().style.cursor = '';
          var ll = window._stormAnnotations[id].getLngLat();
          if (bridge) bridge.on_map_pick_for_route(ll.lat, ll.lng);
          return;
        }}
        var mapEl = document.getElementById('map');
        if (mapEl && mapEl.classList.contains('measuring')) {{
          var ll2 = window._stormAnnotations[id].getLngLat();
          if (bridge) bridge.on_map_click(ll2.lat, ll2.lng);
          return;
        }}
        if (bridge) bridge.on_annotation_click(id);
      }});
      const marker = new maplibregl.Marker({{element: el}})
        .setLngLat([lon, lat])
        .addTo(map);
      window._stormAnnotations[id] = marker;
    }};

    window.stormRemoveAnnotation = function(id) {{
      var m = window._stormAnnotations[id];
      if (m) {{
        if (m._cleanupZoom) m._cleanupZoom();
        m.remove();
        delete window._stormAnnotations[id];
      }}
    }};

    window.stormSetAnnotationDraggable = function(id, on) {{
      var m = window._stormAnnotations[id];
      if (!m) return;
      m.setDraggable(!!on);
      if (on) {{
        m._stormDragEnd = function() {{
          var ll = m.getLngLat();
          if (bridge) bridge.on_annotation_drag_end(id, ll.lat, ll.lng);
        }};
        m.on('dragend', m._stormDragEnd);
        m.getElement().style.cursor = 'grab';
      }} else {{
        if (m._stormDragEnd) {{
          m.off('dragend', m._stormDragEnd);
          delete m._stormDragEnd;
        }}
        m.getElement().style.cursor = 'pointer';
      }}
    }};

    window.stormMoveAnnotation = function(id, lat, lon) {{
      var m = window._stormAnnotations[id];
      if (m) m.setLngLat([lon, lat]);
    }};

    // ── Drawing Annotations (Fronts & Custom Shapes) ──────────────────────
    window._stormDrawings = {{}};
    window._stormDrawingDrag = {{id: null, dragging: false, startLngLat: null, originalCoords: null}};
    window._stormSuppressNextClick = false;

    function _computeCentroid(coords) {{
      var sumLon = 0, sumLat = 0;
      coords.forEach(function(c) {{ sumLon += c[0]; sumLat += c[1]; }});
      return [sumLon / coords.length, sumLat / coords.length];
    }}

    function _drawingLngLatCoords(coords) {{
      return coords.map(function(c) {{ return [c[1], c[0]]; }});
    }}

    function _drawingGeometryForData(d) {{
      var coords = _drawingLngLatCoords(d.coordinates || []);
      if (d.drawing_type === 'polygon' && coords.length >= 3) {{
        return {{type:'Polygon', coordinates:[[...coords, coords[0]]]}};
      }}
      return {{type:'LineString', coordinates:coords}};
    }}

    function _updateDrawingData(id, coordinates) {{
      var d = window._stormDrawings[id];
      if (!d) return;
      d.coordinates = coordinates;
      var src = map.getSource('drawing-' + id);
      if (src) {{
        src.setData({{
          type: 'FeatureCollection',
          features: [{{
            type: 'Feature',
            geometry: _drawingGeometryForData(d),
            properties: {{drawing_id: id, drawing_type: d.drawing_type, title: d.title}}
          }}]
        }});
      }}
      var lblSrc = map.getSource('drawing-lbl-' + id);
      if (lblSrc && d.title && coordinates.length > 0) {{
        lblSrc.setData({{
          type: 'FeatureCollection',
          features: [{{
            type: 'Feature',
            geometry: {{type:'Point', coordinates:_computeCentroid(_drawingLngLatCoords(coordinates))}},
            properties: {{drawing_id: id, title: d.title}}
          }}]
        }});
      }}
    }}

    window.stormAddDrawing = function(id, jsonStr) {{
      stormRemoveDrawing(id);
      var d = JSON.parse(jsonStr);
      window._stormDrawings[id] = d;

      map.addSource('drawing-' + id, {{
        type: 'geojson',
        data: {{
          type: 'FeatureCollection',
          features: [{{
            type: 'Feature',
            geometry: _drawingGeometryForData(d),
            properties: {{drawing_id: id, drawing_type: d.drawing_type, title: d.title}}
          }}]
        }}
      }});

      // Wide transparent line for hit detection (works for fronts and polyline edges)
      map.addLayer({{
        id: 'drawing-hit-' + id, type: 'line', source: 'drawing-' + id,
        paint: {{'line-color': 'rgba(0,0,0,0)', 'line-width': 16, 'line-opacity': 0.001}}
      }});

      if (d.drawing_type === 'polyline' || d.drawing_type === 'polygon') {{
        if (d.drawing_type === 'polygon') {{
          // Invisible fill layer so clicking polygon interior selects the drawing.
          map.addLayer({{
            id: 'drawing-hit-fill-' + id, type: 'fill', source: 'drawing-' + id,
            paint: {{'fill-color': '#000000', 'fill-opacity': 0.001}}
          }});
          // Visible polygon fill.
          map.addLayer({{
            id: 'drawing-fill-' + id, type: 'fill', source: 'drawing-' + id,
            paint: {{'fill-color': '#E8EAF0', 'fill-opacity': 0.12}}
          }});
        }}
        map.addLayer({{
          id: 'drawing-line-' + id, type: 'line', source: 'drawing-' + id,
          layout: {{
            'line-join': 'round',
            'line-cap': 'round'
          }},
          paint: {{
            'line-color': '#E8EAF0',
            'line-width': 2,
            'line-opacity': 0.9
          }}
        }});
        if (d.title) {{
          var centroid = _computeCentroid(_drawingLngLatCoords(d.coordinates || []));
          map.addSource('drawing-lbl-' + id, {{
            type: 'geojson',
            data: {{
              type: 'FeatureCollection',
              features: [{{
                type: 'Feature',
                geometry: {{type:'Point', coordinates:centroid}},
                properties: {{drawing_id: id, title: d.title}}
              }}]
            }}
          }});
          map.addLayer({{
            id: 'drawing-lbl-' + id, type: 'symbol', source: 'drawing-lbl-' + id,
            layout: {{
              'text-field': ['get', 'title'],
              'text-font': ['Noto Sans Bold'],
              'text-size': 12,
              'text-anchor': 'center',
              'text-offset': [0, -1.2],
              'text-allow-overlap': false,
              'text-ignore-placement': false
            }},
            paint: {{
              'text-color': '#E8EAF0',
              'text-halo-color': 'rgba(10,10,15,0.9)',
              'text-halo-width': 2
            }}
          }});
        }}
      }}
      // Fronts: canvas handles visual rendering; only the hit layer above is needed

      ['drawing-hit-' + id, 'drawing-hit-fill-' + id, 'drawing-lbl-' + id]
        .filter(function(layerId) {{ return map.getLayer(layerId); }})
        .forEach(function(layerId) {{
          map.on('mouseenter', layerId, function() {{
            map.getCanvas().style.cursor = (window._stormDrawingDrag && window._stormDrawingDrag.id === id) ? 'grab' : 'pointer';
          }});
          map.on('mouseleave', layerId, function() {{
            map.getCanvas().style.cursor = (window._stormDrawingDrag && window._stormDrawingDrag.id === id) ? 'grab' : '';
          }});
        }});
    }};

    window.stormSetDrawingDraggable = function(id, on) {{
      if (!on) {{
        if (window._stormDrawingDrag && window._stormDrawingDrag.id === id) {{
          window._stormDrawingDrag = {{id: null, dragging: false, startLngLat: null, originalCoords: null}};
        }}
        map.getCanvas().style.cursor = '';
        return;
      }}
      window._stormDrawingDrag = {{id: id, dragging: false, startLngLat: null, originalCoords: null}};
      map.getCanvas().style.cursor = 'grab';
    }};

    window.stormRemoveDrawing = function(id) {{
      ['drawing-hit-', 'drawing-hit-fill-', 'drawing-fill-', 'drawing-line-', 'drawing-lbl-'].forEach(function(pfx) {{
        if (map.getLayer(pfx + id)) map.removeLayer(pfx + id);
      }});
      if (map.getSource('drawing-' + id)) map.removeSource('drawing-' + id);
      if (map.getSource('drawing-lbl-' + id)) map.removeSource('drawing-lbl-' + id);
      delete window._stormDrawings[id];
    }};

    window.stormDrawingModeSet = function(active, type) {{
      window._stormDrawingActive = active;
      window._stormDrawingType = type || '';
      var mapEl = document.getElementById('map');
      if (active) {{
        map.doubleClickZoom.disable();
        if (mapEl) {{ mapEl.classList.add('drawing'); mapEl.classList.remove('annotating', 'measuring'); }}
        var FRONT_COLORS = {{
          cold_front:'#4A9EFF', warm_front:'#E53935',
          stationary_front:'#4A9EFF', occluded_front:'#9C27B0', dryline:'#D4872E'
        }};
        var color = FRONT_COLORS[type] || '#E8EAF0';
        if (map.getLayer('drawing-preview-line')) {{
          map.setPaintProperty('drawing-preview-line', 'line-color', color);
        }}
        window._drawingConfirmedPts = [];
        window._drawingRubberPt = null;
        _updateDrawingPreviewGeoJSON();
      }} else {{
        map.doubleClickZoom.enable();
        if (mapEl) mapEl.classList.remove('drawing');
        _clearDrawingPreview();
      }}
    }};

    window.stormDrawingUpdatePreview = function(ptsJson) {{
      window._drawingConfirmedPts = JSON.parse(ptsJson);
      _updateDrawingPreviewGeoJSON();
    }};

    function _updateDrawingPreviewGeoJSON() {{
      if (!map.getSource('drawing-preview-line')) return;
      var pts = window._drawingConfirmedPts || [];
      if (pts.length === 0) {{
        map.getSource('drawing-preview-line').setData({{type:'FeatureCollection',features:[]}});
        map.getSource('drawing-preview-dots').setData({{type:'FeatureCollection',features:[]}});
        return;
      }}
      var coords = pts.map(function(p) {{ return [p[1], p[0]]; }});
      var lineCoords = window._drawingRubberPt ? coords.concat([window._drawingRubberPt]) : coords;
      if (lineCoords.length >= 2) {{
        map.getSource('drawing-preview-line').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',geometry:{{type:'LineString',coordinates:lineCoords}}}}
        ]}});
      }} else {{
        map.getSource('drawing-preview-line').setData({{type:'FeatureCollection',features:[]}});
      }}
      map.getSource('drawing-preview-dots').setData({{type:'FeatureCollection',features:
        coords.map(function(c) {{ return {{type:'Feature',geometry:{{type:'Point',coordinates:c}}}}; }})
      }});
    }}

    function _clearDrawingPreview() {{
      window._drawingConfirmedPts = [];
      window._drawingRubberPt = null;
      _updateDrawingPreviewGeoJSON();
    }}

    // ── Storm Motion Cones ────────────────────────────────────────────────
    window._stormCones = {{}};
    window._stormConeDrag = {{id: null, dragging: false, startLngLat: null, originalAnchor: null, originalData: null}};
    window._stormConePlacementActive = false;

    function _translateCoordinates(coords, deltaLng, deltaLat) {{
      if (!Array.isArray(coords)) return coords;
      if (coords.length === 0) return [];
      if (typeof coords[0] === 'number') {{
        return [coords[0] + deltaLng, coords[1] + deltaLat];
      }}
      return coords.map(function(part) {{
        return _translateCoordinates(part, deltaLng, deltaLat);
      }});
    }}

    function _translateGeoJSON(data, deltaLng, deltaLat) {{
      var next = JSON.parse(JSON.stringify(data));
      (next.features || []).forEach(function(feature) {{
        if (feature.geometry && feature.geometry.coordinates) {{
          feature.geometry.coordinates = _translateCoordinates(feature.geometry.coordinates, deltaLng, deltaLat);
        }}
      }});
      return next;
    }}

    function _updateStormConeData(id, data, anchorLng, anchorLat) {{
      var cone = window._stormCones[id];
      if (!cone) return;
      cone.data = data;
      cone.anchor = [anchorLng, anchorLat];
      var src = map.getSource('storm-cone-' + id);
      if (src) src.setData(data);
    }}

    window.stormAddStormCone = function(id, geojsonStr, lat, lon) {{
      stormRemoveStormCone(id);
      var data = JSON.parse(geojsonStr);
      map.addSource('storm-cone-' + id, {{type: 'geojson', data: data}});

      // filled polygon
      map.addLayer({{
        id: 'storm-cone-fill-' + id,
        type: 'fill',
        source: 'storm-cone-' + id,
        filter: ['==', ['get', 'ft'], 'cone'],
        paint: {{
          'fill-color': '{ACCENT_COLOR}',
          'fill-opacity': 0.15
        }}
      }});

      // outline
      map.addLayer({{
        id: 'storm-cone-outline-' + id,
        type: 'line',
        source: 'storm-cone-' + id,
        filter: ['==', ['get', 'ft'], 'cone'],
        paint: {{
          'line-color': '{ACCENT_COLOR}',
          'line-width': 1.5,
          'line-opacity': 0.7
        }}
      }});

      // time-step ribs
      map.addLayer({{
        id: 'storm-cone-ribs-' + id,
        type: 'line',
        source: 'storm-cone-' + id,
        filter: ['==', ['get', 'ft'], 'rib'],
        paint: {{
          'line-color': '{ACCENT_COLOR}',
          'line-width': 1,
          'line-opacity': 0.5,
          'line-dasharray': [5, 3]
        }}
      }});

      // time labels at centerline of each rib + 60 min tip
      map.addLayer({{
        id: 'storm-cone-labels-' + id,
        type: 'symbol',
        source: 'storm-cone-' + id,
        filter: ['==', ['get', 'ft'], 'label'],
        layout: {{
          'text-field': ['get', 'text'],
          'text-font': ['Noto Sans Regular'],
          'text-size': 10,
          'text-anchor': 'center',
          'text-allow-overlap': true,
          'text-ignore-placement': true
        }},
        paint: {{
          'text-color': '{ACCENT_COLOR}',
          'text-halo-color': 'rgba(10, 10, 15, 0.85)',
          'text-halo-width': 1.5
        }}
      }});

      // pointer cursor on hover
      map.on('mouseenter', 'storm-cone-fill-' + id, function() {{
        map.getCanvas().style.cursor = (window._stormConeDrag && window._stormConeDrag.id === id) ? 'grab' : 'pointer';
      }});
      map.on('mouseleave', 'storm-cone-fill-' + id, function() {{
        map.getCanvas().style.cursor = (window._stormConeDrag && window._stormConeDrag.id === id) ? 'grab' : (window._stormConePlacementActive ? 'grab' : '');
      }});

      window._stormCones[id] = {{data: data, anchor: [lon, lat]}};
    }};

    window.stormRemoveStormCone = function(id) {{
      var layers = ['storm-cone-fill-' + id, 'storm-cone-outline-' + id, 'storm-cone-ribs-' + id, 'storm-cone-labels-' + id];
      layers.forEach(function(l) {{
        if (map.getLayer(l)) map.removeLayer(l);
      }});
      if (map.getSource('storm-cone-' + id)) map.removeSource('storm-cone-' + id);
      delete window._stormCones[id];
    }};

    window.stormSetStormConeDraggable = function(id, on) {{
      if (!on) {{
        if (window._stormConeDrag && window._stormConeDrag.id === id) {{
          window._stormConeDrag = {{id: null, dragging: false, startLngLat: null, originalAnchor: null, originalData: null}};
        }}
        map.getCanvas().style.cursor = window._stormConePlacementActive ? 'grab' : '';
        return;
      }}
      window._stormConeDrag = {{id: id, dragging: false, startLngLat: null, originalAnchor: null, originalData: null}};
      map.getCanvas().style.cursor = 'grab';
    }};

    window.stormSetStormConePlacementMode = function(active) {{
      window._stormConePlacementActive = !!active;
      if (!active && window._stormConeDrag && !window._stormConeDrag.id) {{
        window._stormConeDrag = {{id: null, dragging: false, startLngLat: null, originalAnchor: null, originalData: null}};
      }}
      map.getCanvas().style.cursor = active ? 'grab' : '';
    }};

    // ── Station Plots ─────────────────────────────────────────────────────
    window._stormStationPlots = {{}};
    window._stormStationPlotsVisible = true;
    window._stormSurfacePlots = {{}};
    window._stormSurfacePlotsVisible = true;
    window._stormSurfaceRegistry = {{}};  // id → {{lon, lat, label}}

    window.stormAddStationPlot = function(id, lat, lon, pngB64) {{
      if (window._stormStationPlots[id]) {{
        window._stormStationPlots[id].remove();
        delete window._stormStationPlots[id];
      }}
      const el = document.createElement('div');
      el.style.cssText = 'width:135px;height:135px;pointer-events:none;';
      if (!window._stormStationPlotsVisible) el.style.display = 'none';
      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + pngB64;
      img.style.cssText = 'width:100%;height:100%;';
      el.appendChild(img);
      const marker = new maplibregl.Marker({{element: el, anchor: 'center'}})
        .setLngLat([lon, lat]).addTo(map);
      window._stormStationPlots[id] = marker;
    }};

    window.stormRemoveStationPlot = function(id) {{
      if (window._stormStationPlots[id]) {{
        window._stormStationPlots[id].remove();
        delete window._stormStationPlots[id];
      }}
    }};

    window.stormSetStationPlotsVisible = function(visible) {{
      window._stormStationPlotsVisible = visible;
      Object.values(window._stormStationPlots).forEach(function(m) {{
        m.getElement().style.display = visible ? '' : 'none';
      }});
    }};

    window.stormAddSurfaceStationPlot = function(id, lat, lon, pngB64, name) {{
      if (window._stormSurfacePlots[id]) {{
        window._stormSurfacePlots[id].remove();
        delete window._stormSurfacePlots[id];
      }}
      const label = name || id;

      // Outer div: pointer-events:none so the 135px image never blocks
      // neighbouring station hit-targets underneath it.
      const el = document.createElement('div');
      el.style.cssText = 'width:135px;height:135px;pointer-events:none;';
      if (!window._stormSurfacePlotsVisible) el.style.display = 'none';

      const img = document.createElement('img');
      img.src = 'data:image/png;base64,' + pngB64;
      // display:block removes inline baseline gap so the hit-target margin
      // math below is exact: img occupies exactly 0..135px vertically.
      img.style.cssText = 'display:block;width:135px;height:135px;pointer-events:none;';
      img.alt = label;
      el.appendChild(img);

      // 20px hit-target centered on the station point.  Negative margin-top
      // pulls it back up from below the image into the center of the 135px
      // square.  No position:relative needed on the parent, so MapLibre's
      // transform-based marker placement is unaffected.
      //   Natural top of hit div = 135px (after block img)
      //   Desired top            = (135 - 20) / 2 = 57.5px
      //   margin-top             = 57.5 - 135 = -77.5px
      const hit = document.createElement('div');
      hit.style.cssText = (
        'width:20px;height:20px;' +
        'margin-top:-77.5px;' +
        'margin-left:57.5px;' +
        'pointer-events:auto;cursor:pointer;'
      );
      el.appendChild(hit);

      // Tooltip is driven by map.on('mousemove') — same as SPC layers —
      // so no DOM mouseenter/mouseleave needed here.
      window._stormSurfaceRegistry[id] = {{lon: lon, lat: lat, label: label}};

      const marker = new maplibregl.Marker({{element: el, anchor: 'center'}})
        .setLngLat([lon, lat]).addTo(map);
      window._stormSurfacePlots[id] = marker;
    }};

    window.stormRemoveSurfaceStationPlot = function(id) {{
      if (window._stormSurfacePlots[id]) {{
        window._stormSurfacePlots[id].remove();
        delete window._stormSurfacePlots[id];
      }}
      delete window._stormSurfaceRegistry[id];
    }};

    window.stormSetSurfaceStationPlotsVisible = function(visible) {{
      window._stormSurfacePlotsVisible = visible;
      Object.values(window._stormSurfacePlots).forEach(function(m) {{
        m.getElement().style.display = visible ? '' : 'none';
      }});
      if (!visible) {{
        var tip = document.getElementById('hazard-tooltip');
        if (tip) tip.style.display = 'none';
      }}
    }};

    // ── Radar Station Picker ─────────────────────────────────────────────
    window.stormSetRadarStations = function(stationsJson) {{
      var src = map.getSource('radar-stations');
      if (!src) {{
        window._radarStationsData = stationsJson;
        return;
      }}
      src.setData(JSON.parse(stationsJson));
    }};

    window.stormSetRadarStationsVisible = function(visible) {{
      window._radarStationsVisible = !!visible;
      ['radar-stations-circle', 'radar-stations-label'].forEach(function(layerId) {{
        if (!map.getLayer(layerId)) return;
        map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
      }});
      if (!visible) map.getCanvas().style.cursor = '';
    }};

    // ── Sounding Station Layer ────────────────────────────────────────────
    window.stormSetSoundingStations = function(geojsonStr) {{
      var src = map.getSource('sounding-stations-src');
      if (!src) return;
      src.setData(JSON.parse(geojsonStr));
      ['sounding-stations', 'sounding-stations-label'].forEach(function(lid) {{
        if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'visible');
      }});
    }};

    window.stormClearSoundingStations = function() {{
      var src = map.getSource('sounding-stations-src');
      if (src) src.setData({{type:'FeatureCollection',features:[]}});
      ['sounding-stations', 'sounding-stations-label'].forEach(function(lid) {{
        if (map.getLayer(lid)) map.setLayoutProperty(lid, 'visibility', 'none');
      }});
    }};

    // ── Route Overlay ─────────────────────────────────────────────────────
    var _destMarker = null;

    window.stormSetRoute = function(geojsonStr) {{
      var src = map.getSource('route');
      if (!src) return;
      src.setData(JSON.parse(geojsonStr));
      // Fit map to route bounds
      try {{
        var coords = JSON.parse(geojsonStr).coordinates;
        if (coords && coords.length > 1) {{
          var lons = coords.map(function(c){{return c[0];}});
          var lats = coords.map(function(c){{return c[1];}});
          var sw = [Math.min.apply(null,lons), Math.min.apply(null,lats)];
          var ne = [Math.max.apply(null,lons), Math.max.apply(null,lats)];
          map.fitBounds([sw, ne], {{padding: 60, duration: 800}});
        }}
      }} catch(e) {{}}
    }};

    window.stormClearRoute = function() {{
      var src = map.getSource('route');
      if (src) src.setData({{type:'Feature',geometry:{{type:'LineString',coordinates:[]}}}});
      if (_destMarker) {{ _destMarker.remove(); _destMarker = null; }}
    }};

    window.stormSetDestinationMarker = function(lon, lat) {{
      if (_destMarker) {{ _destMarker.remove(); _destMarker = null; }}
      var el = document.createElement('div');
      el.style.cssText = 'width:18px;height:18px;border-radius:50%;'
        + 'background:#E53935;border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,0.6);';
      _destMarker = new maplibregl.Marker({{element:el,anchor:'center'}})
        .setLngLat([lon, lat])
        .addTo(map);
    }};

    // ── Measure Tool ─────────────────────────────────────────────────────
    window._measureAnchor = null;

    function _haversineM(lat1,lon1,lat2,lon2) {{
      var R=3958.8, dLat=(lat2-lat1)*Math.PI/180, dLon=(lon2-lon1)*Math.PI/180;
      var a=Math.sin(dLat/2)*Math.sin(dLat/2)+
            Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*
            Math.sin(dLon/2)*Math.sin(dLon/2);
      return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
    }}

    window.stormMeasureActivate = function(active) {{
      if (active) window.stormMeasureClear();
    }};

    window.stormMeasureClear = function() {{
      window._measureAnchor = null;
      ['measure-points','measure-line','measure-label','measure-rubber'].forEach(function(s) {{
        if (map.getSource(s)) map.getSource(s).setData({{type:'FeatureCollection',features:[]}});
      }});
    }};

    window.stormMeasureClick = function(lat, lon) {{
      if (window._measureAnchor === null) {{
        window._measureAnchor = [lon, lat];
        map.getSource('measure-points').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',geometry:{{type:'Point',coordinates:[lon,lat]}}}}
        ]}});
        map.getSource('measure-line').setData({{type:'FeatureCollection',features:[]}});
        map.getSource('measure-label').setData({{type:'FeatureCollection',features:[]}});
      }} else {{
        var anchor = window._measureAnchor;
        var dist  = _haversineM(anchor[1],anchor[0],lat,lon);
        var label = dist.toFixed(1)+' mi  /  '+(dist*1.60934).toFixed(1)+' km';
        var midLon = (anchor[0] + lon) / 2.0;
        var midLat = (anchor[1] + lat) / 2.0;
        map.getSource('measure-line').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',
            geometry:{{type:'LineString',coordinates:[anchor,[lon,lat]]}},
            properties:{{label:label}}}}
        ]}});
        map.getSource('measure-label').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',
            geometry:{{type:'Point',coordinates:[midLon,midLat]}},
            properties:{{label:label}}}}
        ]}});
        window._measureAnchor = null;
        map.getSource('measure-rubber').setData({{type:'FeatureCollection',features:[]}});
        map.getSource('measure-points').setData({{type:'FeatureCollection',features:[
          {{type:'Feature',geometry:{{type:'Point',coordinates:[anchor[0],anchor[1]]}}}},
          {{type:'Feature',geometry:{{type:'Point',coordinates:[lon,lat]}}}}
        ]}});
      }}
    }};

    // ── Deployment Locations ──────────────────────────────────────────────
    window.stormLoadDeployLocs = function(geojsonStr) {{
      var src = map.getSource('deploy-locs');
      if (!src) {{ window._deployLocsData = geojsonStr; return; }}
      src.setData(JSON.parse(geojsonStr));
    }};
    window.stormSetDeployLocsVisible = function(visible) {{
      map.setLayoutProperty('deploy-locs-circles', 'visibility', visible ? 'visible' : 'none');
    }};
    window.stormSetDeployLocsFilter = function(metric, threshold) {{
      var filter;
      if (metric === 'rank_abi' || metric === 'rank_aoi') {{
        // coalesce null → 999 so N/A points are excluded (999 > any threshold)
        filter = ['<=', ['coalesce', ['get', metric], 999], threshold];
      }} else if (metric === 'rqi') {{
        // coalesce null → -1 so N/A points are excluded (-1 < any threshold ≥ 0)
        filter = ['>=', ['coalesce', ['get', 'rqi'], -1], threshold];
      }} else {{
        filter = null;
      }}
      map.setFilter('deploy-locs-circles', filter);
    }};
    window.stormSetDeployLocsSize = function(radius) {{
      map.setPaintProperty('deploy-locs-circles', 'circle-radius', radius);
    }};
    window.stormSetDeployLocsMetric = function(metric) {{
      var expr;
      if (metric === 'rank_abi' || metric === 'rank_aoi') {{
        expr = ['match', ['coalesce', ['get', metric], 0],
          1, '#2DC653', 2, '#A8C538', 3, '#FFD166', 4, '#FF8C42', 5, '#EF233C',
          '#888888'];
      }} else if (metric === 'rqi') {{
        expr = ['case',
          ['<', ['coalesce', ['get', 'rqi'], -1], 0], '#888888',
          ['step', ['get', 'rqi'],
            '#EF233C', 0.2, '#FF8C42', 0.4, '#FFD166', 0.6, '#A8C538', 0.8, '#2DC653'
          ]
        ];
      }} else {{
        expr = '#888888';
      }}
      map.setPaintProperty('deploy-locs-circles', 'circle-color', expr);
    }};

    // ── SPC + NWS Hazard Layers ───────────────────────────────────────────
    window._spcCatVisible = {{MRGL:false, SLGHT:false, ENH:false, MDT:false, HIGH:false}};

    function _setLayerVisibility(layerId, visible) {{
      if (!map.getLayer(layerId)) return;
      map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none');
    }}

    function _applySpcCategoryFilter() {{
      var cats = Object.keys(window._spcCatVisible).filter(function(k) {{ return window._spcCatVisible[k]; }});
      if (!map.getLayer('spc-cat-fill') || !map.getLayer('spc-cat-line')) return;
      if (cats.length === 0) {{
        _setLayerVisibility('spc-cat-fill', false);
        _setLayerVisibility('spc-cat-line', false);
        return;
      }}
      _setLayerVisibility('spc-cat-fill', true);
      _setLayerVisibility('spc-cat-line', true);
      var filt = ['in', ['get', 'cat'], ['literal', cats]];
      map.setFilter('spc-cat-fill', filt);
      map.setFilter('spc-cat-line', filt);
    }}

    // ── Satellite overlay API ─────────────────────────────────────────────
    // Two rendering paths depending on mode:
    //   CONUS  — nowCOAST WMS raster tile source (SAT_WMS_SRC/SAT_WMS_LYR).
    //            Python provides a TIME string; MapLibre fetches tiles for the
    //            current viewport automatically.  stormSetSatelliteTime() creates
    //            or updates the source via setTiles().
    //   MESO   — IEM pre-fetched PNG injected as an image source (SAT_SRC/SAT_LYR).
    //            stormSetSatelliteFrame() handles this path unchanged.
    var _satVisible = false;
    var _satMode    = '';
    var _satOpacity = 0.7;
    var _mesoPreviewLabel = '';
    var SAT_SRC     = 'sat-image';
    var SAT_LYR     = 'sat-layer';
    var SAT_WMS_SRC = 'sat-wms';
    var SAT_WMS_LYR = 'sat-wms-layer';
    var _satLastUrl = '';
    var _NOWCOAST_WMS = 'https://nowcoast.noaa.gov/geoserver/satellite/wms';

    function _conusTileUrl(timeStr) {{
      return _NOWCOAST_WMS +
        '?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap' +
        '&LAYERS=goes_visible_imagery' +
        '&CRS=EPSG:3857&BBOX={{bbox-epsg-3857}}' +
        '&WIDTH=256&HEIGHT=256' +
        '&FORMAT=image/png&TRANSPARENT=TRUE&STYLES=' +
        '&TIME=' + timeStr;
    }}

    function _updateSatSource(dataUrl, coords) {{
      if (map.getSource(SAT_SRC)) {{
        map.getSource(SAT_SRC).updateImage({{ url: dataUrl, coordinates: coords }});
      }} else {{
        map.addSource(SAT_SRC, {{ type: 'image', url: dataUrl, coordinates: coords }});
        try {{
          map.addLayer({{
            id: SAT_LYR, type: 'raster', source: SAT_SRC,
            paint: {{ 'raster-opacity': _satOpacity, 'raster-fade-duration': 0 }}
          }}, 'road-unpaved');
        }} catch(_) {{
          map.addLayer({{
            id: SAT_LYR, type: 'raster', source: SAT_SRC,
            paint: {{ 'raster-opacity': _satOpacity, 'raster-fade-duration': 0 }}
          }});
        }}
      }}
      if (map.getLayer(SAT_LYR)) {{
        map.setLayoutProperty(SAT_LYR, 'visibility', _satVisible ? 'visible' : 'none');
      }}
    }}

    function _applySatMesoBoxes() {{
      // Only show MESO polygons while hovering (preview); hide otherwise.
      if (_mesoPreviewLabel) {{
        _applyMesoPreview();
        return;
      }}
      ['meso-sectors-fill', 'meso-sectors-line', 'meso-sectors-label'].forEach(function(lid) {{
        if (!map.getLayer(lid)) return;
        map.setLayoutProperty(lid, 'visibility', 'none');
        map.setFilter(lid, null);
      }});
    }}

    function _applyMesoPreview() {{
      if (!_mesoPreviewLabel) {{
        _applySatMesoBoxes();
        return;
      }}
      ['meso-sectors-fill', 'meso-sectors-line', 'meso-sectors-label'].forEach(function(lid) {{
        if (!map.getLayer(lid)) return;
        map.setLayoutProperty(lid, 'visibility', 'visible');
        map.setFilter(lid, ['==', ['get', 'label'], _mesoPreviewLabel]);
      }});
      if (map.getLayer('meso-sectors-fill'))
        map.setPaintProperty('meso-sectors-fill', 'fill-opacity', 0.12);
      if (map.getLayer('meso-sectors-line'))
        map.setPaintProperty('meso-sectors-line', 'line-opacity', 1.0);
    }}

    window.stormPreviewMesoSector = function(label) {{
      _mesoPreviewLabel = label || '';
      _applyMesoPreview();
    }};

    window.stormClearMesoPreview = function() {{
      _mesoPreviewLabel = '';
      _applySatMesoBoxes();
    }};

    window.stormSetSatelliteTime = function(timeStr) {{
      var tileUrl = _conusTileUrl(timeStr);
      if (map.getSource(SAT_WMS_SRC)) {{
        map.getSource(SAT_WMS_SRC).setTiles([tileUrl]);
      }} else {{
        map.addSource(SAT_WMS_SRC, {{ type: 'raster', tiles: [tileUrl], tileSize: 256, bounds: [-116, 28, -82, 49] }});
        try {{
          map.addLayer({{
            id: SAT_WMS_LYR, type: 'raster', source: SAT_WMS_SRC,
            paint: {{ 'raster-opacity': _satOpacity, 'raster-fade-duration': 0 }}
          }}, 'road-unpaved');
        }} catch(_) {{
          map.addLayer({{
            id: SAT_WMS_LYR, type: 'raster', source: SAT_WMS_SRC,
            paint: {{ 'raster-opacity': _satOpacity, 'raster-fade-duration': 0 }}
          }});
        }}
      }}
      if (map.getLayer(SAT_WMS_LYR)) {{
        map.setLayoutProperty(SAT_WMS_LYR, 'visibility', _satVisible ? 'visible' : 'none');
      }}
    }};

    window.stormSetSatelliteFrame = function(b64, west, south, east, north) {{
      // MapLibre image source coordinates: NW, NE, SE, SW corners
      var coords = [[west, north], [east, north], [east, south], [west, south]];
      var dataUrl = 'data:image/png;base64,' + b64;
      try {{
        if (dataUrl === _satLastUrl) return;
        _satLastUrl = dataUrl;
        var img = new Image();
        img.onload = function() {{
          _updateSatSource(dataUrl, coords);
        }};
        img.onerror = function() {{
          _updateSatSource(dataUrl, coords);
        }};
        img.src = dataUrl;
      }} catch(e) {{
        console.error('[STORM] satellite frame inject error:', e.message || e);
      }}
    }};

    window.stormSetSatelliteVisible = function(visible) {{
      _satVisible = !!visible;
      if (map.getLayer(SAT_LYR))     map.setLayoutProperty(SAT_LYR,     'visibility', _satVisible ? 'visible' : 'none');
      if (map.getLayer(SAT_WMS_LYR)) map.setLayoutProperty(SAT_WMS_LYR, 'visibility', _satVisible ? 'visible' : 'none');
      _applySatMesoBoxes();
    }};

    window.stormClearSatelliteFrame = function() {{
      try {{
        if (map.getLayer(SAT_LYR))     map.removeLayer(SAT_LYR);
        if (map.getSource(SAT_SRC))    map.removeSource(SAT_SRC);
        if (map.getLayer(SAT_WMS_LYR)) map.removeLayer(SAT_WMS_LYR);
        if (map.getSource(SAT_WMS_SRC)) map.removeSource(SAT_WMS_SRC);
      }} catch(_) {{}}
      _satLastUrl = '';
    }};

    window.stormSetSatelliteMode = function(mode) {{
      _satMode = mode || '';
      // Remove the source type that is no longer active to avoid stale overlays.
      if (mode === 'conus') {{
        try {{
          if (map.getLayer(SAT_LYR))  map.removeLayer(SAT_LYR);
          if (map.getSource(SAT_SRC)) map.removeSource(SAT_SRC);
        }} catch(_) {{}}
        _satLastUrl = '';
      }} else if (mode === 'meso1' || mode === 'meso2') {{
        try {{
          if (map.getLayer(SAT_WMS_LYR))  map.removeLayer(SAT_WMS_LYR);
          if (map.getSource(SAT_WMS_SRC)) map.removeSource(SAT_WMS_SRC);
        }} catch(_) {{}}
      }}
      _applySatMesoBoxes();
    }};

    window.stormSetSatelliteOpacity = function(opacity) {{
      _satOpacity = Math.max(0, Math.min(1, parseFloat(opacity) || 0));
      if (map.getLayer(SAT_LYR))     map.setPaintProperty(SAT_LYR,     'raster-opacity', _satOpacity);
      if (map.getLayer(SAT_WMS_LYR)) map.setPaintProperty(SAT_WMS_LYR, 'raster-opacity', _satOpacity);
    }};

    window.stormSetMesoSectors = function(sectorsJson) {{
      var sectors = JSON.parse(sectorsJson);
      var features = sectors.map(function(s) {{
        return {{
          type: 'Feature',
          properties: {{ label: s.label }},
          geometry: {{
            type: 'Polygon',
            coordinates: [[
              [s.west, s.north], [s.east, s.north],
              [s.east, s.south], [s.west, s.south],
              [s.west, s.north]
            ]]
          }}
        }};
      }});
      if (map.getSource('meso-sectors')) {{
        map.getSource('meso-sectors').setData({{type:'FeatureCollection', features: features}});
      }}
      _applySatMesoBoxes();
    }};

    // ── SfcOA (mesoanalysis) overlay API ──────────────────────────────────
    // Raster image overlay from pre-warped PNG (Python reprojects LCC→4326
    // so MapLibre's ImageSource can place it as a plain lat/lon rectangle).
    var SFC_SRC       = 'sfcoa-image';
    var SFC_LYR       = 'sfcoa-layer';
    var SFC_SECT_SRC  = 'sfcoa-sectors';
    var SFC_SECT_FILL = 'sfcoa-sectors-fill';
    var SFC_SECT_LINE = 'sfcoa-sectors-line';
    var _sfcoaVisible = false;
    var _sfcoaOpacity = 0.75;
    var _sfcoaPreview = 0;           // sector id currently previewed, 0 = none
    var _sfcoaLastUrl = '';

    function _placeBelowRadar(layerSpec) {{
      try {{
        if (map.getLayer('radar-overlay')) {{
          map.addLayer(layerSpec, 'radar-overlay');
          return;
        }}
      }} catch(_) {{}}
      try {{
        map.addLayer(layerSpec, 'road-unpaved');
      }} catch(_) {{
        map.addLayer(layerSpec);
      }}
    }}

    function _updateSfcoaSource(dataUrl, coords) {{
      if (map.getSource(SFC_SRC)) {{
        map.getSource(SFC_SRC).updateImage({{ url: dataUrl, coordinates: coords }});
      }} else {{
        map.addSource(SFC_SRC, {{ type: 'image', url: dataUrl, coordinates: coords }});
        _placeBelowRadar({{
          id: SFC_LYR, type: 'raster', source: SFC_SRC,
          paint: {{ 'raster-opacity': _sfcoaOpacity, 'raster-fade-duration': 0 }}
        }});
      }}
      if (map.getLayer(SFC_LYR)) {{
        map.setLayoutProperty(SFC_LYR, 'visibility', _sfcoaVisible ? 'visible' : 'none');
      }}
    }}

    window.stormSetMesoanalysisFrame = function(b64, west, south, east, north) {{
      var coords = [[west, north], [east, north], [east, south], [west, south]];
      var dataUrl = 'data:image/png;base64,' + b64;
      try {{
        if (dataUrl === _sfcoaLastUrl) return;
        _sfcoaLastUrl = dataUrl;
        var img = new Image();
        img.onload  = function() {{ _updateSfcoaSource(dataUrl, coords); }};
        img.onerror = function() {{ _updateSfcoaSource(dataUrl, coords); }};
        img.src = dataUrl;
      }} catch(e) {{
        console.error('[STORM] sfcoa frame inject error:', e.message || e);
      }}
    }};

    window.stormSetMesoanalysisOpacity = function(opacity) {{
      _sfcoaOpacity = Math.max(0, Math.min(1, parseFloat(opacity) || 0));
      if (map.getLayer(SFC_LYR))
        map.setPaintProperty(SFC_LYR, 'raster-opacity', _sfcoaOpacity);
    }};

    window.stormSetMesoanalysisVisible = function(visible) {{
      _sfcoaVisible = !!visible;
      if (map.getLayer(SFC_LYR))
        map.setLayoutProperty(SFC_LYR, 'visibility', _sfcoaVisible ? 'visible' : 'none');
    }};

    window.stormClearMesoanalysisFrame = function() {{
      try {{
        if (map.getLayer(SFC_LYR))  map.removeLayer(SFC_LYR);
        if (map.getSource(SFC_SRC)) map.removeSource(SFC_SRC);
      }} catch(_) {{}}
      _sfcoaLastUrl = '';
    }};

    function _ensureSfcoaSectorLayers() {{
      if (map.getSource(SFC_SECT_SRC)) return;
      map.addSource(SFC_SECT_SRC, {{
        type: 'geojson',
        data: {{ type: 'FeatureCollection', features: [] }},
      }});
      map.addLayer({{
        id: SFC_SECT_FILL, type: 'fill', source: SFC_SECT_SRC,
        paint: {{ 'fill-color': '#4A9EFF', 'fill-opacity': 0.10 }},
        layout: {{ visibility: 'none' }},
      }});
      map.addLayer({{
        id: SFC_SECT_LINE, type: 'line', source: SFC_SECT_SRC,
        paint: {{ 'line-color': '#4A9EFF', 'line-width': 2, 'line-dasharray': [2, 2] }},
        layout: {{ visibility: 'none' }},
      }});
    }}

    window.stormSetSfcOASectors = function(sectorsJson) {{
      _ensureSfcoaSectorLayers();
      var sectors = JSON.parse(sectorsJson);
      var features = sectors.map(function(s) {{
        return {{
          type: 'Feature',
          properties: {{ sector: s.sector }},
          geometry: {{
            type: 'Polygon',
            coordinates: [[
              [s.west, s.north], [s.east, s.north],
              [s.east, s.south], [s.west, s.south],
              [s.west, s.north]
            ]]
          }}
        }};
      }});
      if (map.getSource(SFC_SECT_SRC)) {{
        map.getSource(SFC_SECT_SRC).setData(
          {{type:'FeatureCollection', features: features}}
        );
      }}
    }};

    function _applySfcoaPreview() {{
      if (!map.getLayer(SFC_SECT_FILL)) return;
      if (_sfcoaPreview > 0) {{
        var filt = ['==', ['get', 'sector'], _sfcoaPreview];
        map.setFilter(SFC_SECT_FILL, filt);
        map.setFilter(SFC_SECT_LINE, filt);
        map.setLayoutProperty(SFC_SECT_FILL, 'visibility', 'visible');
        map.setLayoutProperty(SFC_SECT_LINE, 'visibility', 'visible');
      }} else {{
        map.setLayoutProperty(SFC_SECT_FILL, 'visibility', 'none');
        map.setLayoutProperty(SFC_SECT_LINE, 'visibility', 'none');
      }}
    }}

    window.stormPreviewSfcOASector = function(sectorId) {{
      _sfcoaPreview = parseInt(sectorId, 10) || 0;
      _ensureSfcoaSectorLayers();
      _applySfcoaPreview();
    }};

    window.stormClearSfcOAPreview = function() {{
      _sfcoaPreview = 0;
      _applySfcoaPreview();
    }};

    window.stormSetSpcGeoJSON = function(catJson, windJson, hailJson, torJson) {{
      if (map.getSource('spc-cat')) map.getSource('spc-cat').setData(JSON.parse(catJson));
      if (map.getSource('spc-wind')) map.getSource('spc-wind').setData(JSON.parse(windJson));
      if (map.getSource('spc-hail')) map.getSource('spc-hail').setData(JSON.parse(hailJson));
      if (map.getSource('spc-tor')) map.getSource('spc-tor').setData(JSON.parse(torJson));
      _applySpcCategoryFilter();
      // Debug: log LABEL values present in each probabilistic product so we
      // can confirm whether any SIGN features exist in today's outlook.
      ['wind','hail','tor'].forEach(function(name) {{
        var src = map.getSource('spc-' + name);
        if (!src) return;
        var fc = src._data || src.serialize().data;
        if (!fc || !fc.features) return;
        var labels = fc.features.map(function(f) {{ return (f.properties||{{}}).LABEL || (f.properties||{{}}).label || '?'; }});
        console.log('spc-' + name + ' LABEL values:', labels);
      }});
    }};

    window.stormSetSpcCategoryVisible = function(key, visible) {{
      var k = String(key || '').toUpperCase();
      if (!window._spcCatVisible.hasOwnProperty(k)) return;
      window._spcCatVisible[k] = !!visible;
      _applySpcCategoryFilter();
    }};

    window.stormSetSpcProductVisible = function(key, visible) {{
      var k = String(key || '').toLowerCase();
      if (['wind','hail','tor'].indexOf(k) === -1) return;
      ['fill','line','sig-base','sign','cig1','cig2','cig3','sig-line'].forEach(function(s) {{
        _setLayerVisibility('spc-' + k + '-' + s, !!visible);
      }});
    }};

    window.stormSetNwsWarningsGeoJSON = function(warnJson) {{
      if (map.getSource('nws-warnings')) map.getSource('nws-warnings').setData(JSON.parse(warnJson));
    }};

    window.stormSetNwsWarningsVisible = function(visible) {{
      _setLayerVisibility('nws-warnings-fill', !!visible);
      _setLayerVisibility('nws-warnings-line', !!visible);
    }};

    window.stormSetSpcWatchesGeoJSON = function(watchJson) {{
      if (map.getSource('spc-watches')) map.getSource('spc-watches').setData(JSON.parse(watchJson));
    }};

    window.stormSetSpcWatchesVisible = function(visible) {{
      _setLayerVisibility('spc-watches-fill', !!visible);
      _setLayerVisibility('spc-watches-line', !!visible);
    }};

    window.stormSetSpcMdsGeoJSON = function(mdJson) {{
      if (map.getSource('spc-mds')) map.getSource('spc-mds').setData(JSON.parse(mdJson));
    }};

    window.stormSetSpcMdsVisible = function(visible) {{
      _setLayerVisibility('spc-mds-fill', !!visible);
      _setLayerVisibility('spc-mds-line', !!visible);
    }};


    // Notify Python that MapLibre is fully loaded and all storm* functions
    // are defined. If bridge isn't ready yet the QWebChannel init callback
    // above will call on_map_loaded() once it sees _stormMapLoaded = true.
    window._stormMapLoaded = true;
    if (bridge) bridge.on_map_loaded();

    // ── Front Canvas Rendering ────────────────────────────────────────────
    (function() {{
      var frontCanvas = document.getElementById('front-canvas');
      if (!frontCanvas) return;
      var frontCtx = frontCanvas.getContext('2d');

      function _resizeFrontCanvas() {{
        var mc = map.getCanvas();
        var dpr = window.devicePixelRatio || 1;
        var w = Math.round(mc.clientWidth * dpr);
        var h = Math.round(mc.clientHeight * dpr);
        if (frontCanvas.width !== w || frontCanvas.height !== h) {{
          frontCanvas.width = w;
          frontCanvas.height = h;
        }}
      }}

      function _projectPts(coords, dpr) {{
        return coords.map(function(c) {{
          var p = map.project([c[1], c[0]]);
          return {{x: p.x * dpr, y: p.y * dpr}};
        }});
      }}

      function _drawFrontLine(ctx, pts, color, dpr) {{
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.5 * dpr;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.stroke();
      }}

      function _drawAlternatingFrontLine(ctx, pts, colorA, colorB, segmentLen, dpr) {{
        if (!pts || pts.length < 2) return;
        var drawA = true;
        var carried = 0;
        var segLen = Math.max(8 * dpr, segmentLen);
        for (var i = 0; i < pts.length - 1; i++) {{
          var p1 = pts[i], p2 = pts[i+1];
          var dx = p2.x - p1.x, dy = p2.y - p1.y;
          var len = Math.sqrt(dx*dx + dy*dy);
          if (len < 0.5) continue;

          var used = 0;
          while (used < len) {{
            var run = Math.min(segLen - carried, len - used);
            var t1 = used / len;
            var t2 = (used + run) / len;
            var x1 = p1.x + dx * t1, y1 = p1.y + dy * t1;
            var x2 = p1.x + dx * t2, y2 = p1.y + dy * t2;

            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.strokeStyle = drawA ? colorA : colorB;
            ctx.lineWidth = 2.5 * dpr;
            ctx.lineJoin = 'round';
            ctx.lineCap = 'round';
            ctx.stroke();

            used += run;
            carried += run;
            if (carried >= segLen - 0.001) {{
              carried = 0;
              drawA = !drawA;
            }}
          }}
        }}
      }}

      function _walkLine(pts, spacing, cb) {{
        var acc = 0, nextAt = spacing * 0.5, idx = 0;
        for (var i = 0; i < pts.length - 1; i++) {{
          var p1 = pts[i], p2 = pts[i+1];
          var dx = p2.x - p1.x, dy = p2.y - p1.y;
          var len = Math.sqrt(dx*dx + dy*dy);
          if (len < 0.5) continue;
          var ux = dx/len, uy = dy/len;
          while (acc + len >= nextAt) {{
            var t = (nextAt - acc) / len;
            cb(p1.x + t*dx, p1.y + t*dy, ux, uy, idx++);
            nextAt += spacing;
          }}
          acc += len;
        }}
      }}

      function _triSym(ctx, sx, sy, tx, ty, rx, ry, size, color) {{
        ctx.beginPath();
        ctx.moveTo(sx + rx*size, sy + ry*size);
        ctx.lineTo(sx - tx*size*0.65 - rx*size*0.15, sy - ty*size*0.65 - ry*size*0.15);
        ctx.lineTo(sx + tx*size*0.65 - rx*size*0.15, sy + ty*size*0.65 - ry*size*0.15);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();
      }}

      function _semiSym(ctx, sx, sy, rx, ry, size, color, strokeOnly) {{
        var ang = Math.atan2(ry, rx);
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.arc(sx, sy, size * 0.85, ang - Math.PI/2, ang + Math.PI/2);
        ctx.closePath();
        if (strokeOnly) {{ ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke(); }}
        else {{ ctx.fillStyle = color; ctx.fill(); }}
      }}

      function _drawFront(ctx, drawing, dpr) {{
        var coords = drawing.coordinates;
        if (!coords || coords.length < 2) return;
        var COLORS = {{
          cold_front:'#4A9EFF', warm_front:'#E53935',
          stationary_front:'#4A9EFF', occluded_front:'#9C27B0', dryline:'#D4872E'
        }};
        var color = COLORS[drawing.drawing_type] || '#FFFFFF';
        var side = drawing.flipped ? -1 : 1;
        var pts = _projectPts(coords, dpr);
        var SPACING = 40 * dpr, SIZE = 9 * dpr;
        var type = drawing.drawing_type;

        if (type === 'stationary_front') {{
          _drawAlternatingFrontLine(ctx, pts, '#E53935', '#4A9EFF', SPACING * 0.5, dpr);
        }} else {{
          _drawFrontLine(ctx, pts, color, dpr);
        }}
        _walkLine(pts, SPACING, function(sx, sy, tx, ty, idx) {{
          var rx = -ty * side, ry = tx * side;
          if (type === 'cold_front') {{
            _triSym(ctx, sx, sy, tx, ty, rx, ry, SIZE, '#4A9EFF');
          }} else if (type === 'warm_front') {{
            _semiSym(ctx, sx, sy, rx, ry, SIZE, '#E53935', false);
          }} else if (type === 'stationary_front') {{
            // Stationary front pattern: red semicircle, then blue triangle.
            if (idx % 2 === 0) _semiSym(ctx, sx, sy, -rx, -ry, SIZE, '#E53935', false);
            else _triSym(ctx, sx, sy, tx, ty, rx, ry, SIZE, '#4A9EFF');
          }} else if (type === 'occluded_front') {{
            if (idx % 2 === 0) _triSym(ctx, sx, sy, tx, ty, rx, ry, SIZE, '#9C27B0');
            else _semiSym(ctx, sx, sy, rx, ry, SIZE, '#9C27B0', false);
          }} else if (type === 'dryline') {{
            _semiSym(ctx, sx, sy, rx, ry, SIZE, '#D4872E', true);
          }}
        }});
      }}

      function _drawPreviewFront(ctx, points, type, dpr) {{
        if (!points || points.length < 2) return;
        var COLORS = {{
          cold_front:'#4A9EFF', warm_front:'#E53935',
          stationary_front:'#4A9EFF', occluded_front:'#9C27B0', dryline:'#D4872E'
        }};
        var color = COLORS[type] || '#E8EAF0';
        var pts = _projectPts(points, dpr);
        ctx.save();
        ctx.globalAlpha = 0.55;
        ctx.setLineDash([8*dpr, 5*dpr]);
        _drawFrontLine(ctx, pts, color, dpr);
        ctx.restore();
      }}

      map.on('render', function() {{
        _resizeFrontCanvas();
        frontCtx.clearRect(0, 0, frontCanvas.width, frontCanvas.height);
        var dpr = window.devicePixelRatio || 1;

        Object.values(window._stormDrawings || {{}}).forEach(function(d) {{
          if (d.drawing_type !== 'polyline' && d.drawing_type !== 'polygon') {{
            _drawFront(frontCtx, d, dpr);
          }}
        }});

        if (window._stormDrawingActive && window._stormDrawingType &&
            window._stormDrawingType !== 'polyline' && window._stormDrawingType !== 'polygon') {{
          var previewPts = (window._drawingConfirmedPts || []).slice();
          if (window._drawingRubberPt) {{
            previewPts.push([window._drawingRubberPt[1], window._drawingRubberPt[0]]);
          }}
          _drawPreviewFront(frontCtx, previewPts, window._stormDrawingType, dpr);
        }}
      }});
    }})();

    // ── Legend Toggle ─────────────────────────────────────────────────────
    (function() {{
      const toggle = document.getElementById("legend-toggle");
      const body   = document.getElementById("legend-body");

      toggle.addEventListener("click", function() {{
        const isOpen = body.classList.contains("visible");
        body.classList.toggle("visible", !isOpen);
        toggle.classList.toggle("open", !isOpen);
      }});
    }})();
  </script>
</body>
</html>"""




# ── Qt Bridge ─────────────────────────────────────────────────────────────────

class MapBridge(QObject):
    map_clicked        = pyqtSignal(float, float)
    map_moved          = pyqtSignal(float, float, float)
    feature_clicked    = pyqtSignal(str)
    annotation_clicked = pyqtSignal(str)
    storm_cone_clicked = pyqtSignal(str)
    map_double_clicked    = pyqtSignal(float, float)
    drawing_clicked       = pyqtSignal(str)
    radar_station_clicked = pyqtSignal(str)
    sounding_clicked             = pyqtSignal(float, float)
    obs_sounding_station_clicked = pyqtSignal(str, str, float, float, float)  # id, name, lat, lon, elev
    user_dragged          = pyqtSignal()
    map_pick_for_route    = pyqtSignal(float, float)
    annotation_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    drawing_drag_ended    = pyqtSignal(str, str)           # id, coords json
    storm_cone_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    storm_cone_place_drag_ended = pyqtSignal(float, float)

    @pyqtSlot(float, float)
    def on_map_click(self, lat: float, lon: float):
        self.map_clicked.emit(lat, lon)

    @pyqtSlot(float, float, float)
    def on_map_move(self, lat: float, lon: float, zoom: float):
        self.map_moved.emit(lat, lon, zoom)

    @pyqtSlot(str)
    def on_feature_click(self, feature_json: str):
        self.feature_clicked.emit(feature_json)

    @pyqtSlot(str)
    def on_annotation_click(self, annotation_id: str):
        self.annotation_clicked.emit(annotation_id)

    @pyqtSlot(str, float, float)
    def on_annotation_drag_end(self, annotation_id: str, lat: float, lon: float):
        self.annotation_drag_ended.emit(annotation_id, lat, lon)

    @pyqtSlot(str, str)
    def on_drawing_drag_end(self, drawing_id: str, coordinates_json: str):
        self.drawing_drag_ended.emit(drawing_id, coordinates_json)

    @pyqtSlot(str, float, float)
    def on_storm_cone_drag_end(self, cone_id: str, lat: float, lon: float):
        self.storm_cone_drag_ended.emit(cone_id, lat, lon)

    @pyqtSlot(float, float)
    def on_storm_cone_place_drag_end(self, lat: float, lon: float):
        self.storm_cone_place_drag_ended.emit(lat, lon)

    @pyqtSlot(str)
    def on_storm_cone_click(self, cone_id: str):
        self.storm_cone_clicked.emit(cone_id)

    @pyqtSlot(float, float)
    def on_map_dblclick(self, lat: float, lon: float):
        self.map_double_clicked.emit(lat, lon)

    @pyqtSlot(str)
    def on_drawing_click(self, drawing_id: str):
        self.drawing_clicked.emit(drawing_id)

    @pyqtSlot(str)
    def on_radar_station_click(self, site_id: str):
        self.radar_station_clicked.emit(site_id)

    @pyqtSlot(str)
    def on_js_console(self, msg: str):
        """Receive forwarded JS console messages from the page via QWebChannel.

        Logged at INFO level and printed to stdout for easier capture when the
        application is run from a terminal.
        """
        try:
            # Print to stdout so users running the app in a terminal see messages
            print(f"JS-FWD {msg}", flush=True)
        except Exception:
            pass
        try:
            log.info("JS-FWD %s", msg)
        except Exception:
            pass

    @pyqtSlot(float, float)
    def on_sounding_click(self, lat: float, lon: float):
        self.sounding_clicked.emit(lat, lon)

    @pyqtSlot(str, str, float, float, float)
    def on_obs_station_click(self, station_id: str, name: str, lat: float, lon: float, elev: float):
        self.obs_sounding_station_clicked.emit(station_id, name, lat, lon, elev)

    @pyqtSlot()
    def on_user_drag(self):
        self.user_dragged.emit()

    map_loaded = pyqtSignal()

    @pyqtSlot()
    def on_map_loaded(self):
        self.map_loaded.emit()

    @pyqtSlot(float, float)
    def on_map_pick_for_route(self, lat: float, lon: float):
        self.map_pick_for_route.emit(lat, lon)



# ── Map Widget ────────────────────────────────────────────────────────────────

class MapWidget(QWidget if SAFE_MAP_MODE else QWebEngineView):
    map_ready             = pyqtSignal()
    map_clicked           = pyqtSignal(float, float)
    map_moved             = pyqtSignal(float, float, float)
    feature_clicked       = pyqtSignal(str)
    annotation_clicked    = pyqtSignal(str)
    annotation_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    drawing_drag_ended    = pyqtSignal(str, str)           # id, coords json
    storm_cone_clicked    = pyqtSignal(str)
    storm_cone_drag_ended = pyqtSignal(str, float, float)  # id, lat, lon
    storm_cone_place_drag_ended = pyqtSignal(float, float)
    map_double_clicked    = pyqtSignal(float, float)
    drawing_clicked       = pyqtSignal(str)
    radar_station_clicked = pyqtSignal(str)
    sounding_clicked             = pyqtSignal(float, float)
    obs_sounding_station_clicked = pyqtSignal(str, str, float, float, float)  # id, name, lat, lon, elev
    user_dragged          = pyqtSignal()
    map_pick_for_route    = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        if SAFE_MAP_MODE:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 24, 24, 24)
            msg = QLabel(
                "Safe Map Mode: WebEngine disabled on this Windows device to avoid GPU crashes."
            )
            msg.setWordWrap(True)
            msg.setStyleSheet("color: #B5BDCC; font-size: 13px;")
            layout.addWidget(msg)
            self._map_ready = True
            self._js_queue = []
            QTimer.singleShot(0, self.map_ready.emit)
            return

        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from ui.tile_scheme_handler import StormSchemeHandler
        self._scheme_handler = StormSchemeHandler(
            TILES_PATH, STATIC_PATH, build_map_html()
        )
        QWebEngineProfile.defaultProfile().installUrlSchemeHandler(
            b"storm", self._scheme_handler
        )
        # Public accessor so RadarOverlay can push PNG bytes for URL-based serving
        self.scheme_handler = self._scheme_handler

        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, not SAFE_MAP_MODE)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, not SAFE_MAP_MODE)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        self.bridge = MapBridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)

        self.bridge.map_clicked.connect(self.map_clicked)
        self.bridge.map_moved.connect(self.map_moved)
        self.bridge.feature_clicked.connect(self.feature_clicked)
        self.bridge.annotation_clicked.connect(self.annotation_clicked)
        self.bridge.annotation_drag_ended.connect(self.annotation_drag_ended)
        self.bridge.drawing_drag_ended.connect(self.drawing_drag_ended)
        self.bridge.storm_cone_clicked.connect(self.storm_cone_clicked)
        self.bridge.storm_cone_drag_ended.connect(self.storm_cone_drag_ended)
        self.bridge.storm_cone_place_drag_ended.connect(self.storm_cone_place_drag_ended)
        self.bridge.map_double_clicked.connect(self.map_double_clicked)
        self.bridge.drawing_clicked.connect(self.drawing_clicked)
        self.bridge.radar_station_clicked.connect(self.radar_station_clicked)
        self.bridge.sounding_clicked.connect(self.sounding_clicked)
        self.bridge.obs_sounding_station_clicked.connect(self.obs_sounding_station_clicked)
        self.bridge.user_dragged.connect(self.user_dragged)
        self.bridge.map_pick_for_route.connect(self.map_pick_for_route)

        # Queue for JS calls that arrive before MapLibre has fully loaded.
        # _map_ready is set by the bridge.on_map_loaded() signal fired from
        # inside map.on("load", ...) — NOT from loadFinished — so that
        # stormAddAnnotation etc. are guaranteed to be the real functions.
        self._map_ready = False
        self._js_queue: list[str] = []
        self.bridge.map_loaded.connect(self._on_map_loaded_from_js)

        QTimer.singleShot(0, self._load_map)

    def javaScriptConsoleMessage(self, level, message, line, source):
        # Emit all JS console messages to stdout for debugging (includes errors/warnings/info)
        try:
            lvl_name = getattr(level, 'name', str(level))
        except Exception:
            lvl_name = str(level)
        print(f"JS [{lvl_name}] {message} ({source}:{line})", flush=True)
        from PyQt6.QtWebEngineCore import QWebEnginePage
        if level in (QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel,
                     QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel):
            log.warning("JS %s [%s:%s]: %s", lvl_name, source, line, message)

    def _load_map(self):
        self.load(QUrl("storm://app/"))

    def _on_map_loaded_from_js(self):
        if self._map_ready:
            return
        self._map_ready = True
        for script in self._js_queue:
            self.page().runJavaScript(script)
        self._js_queue.clear()
        self.map_ready.emit()

    def run_js(self, script: str):
        if SAFE_MAP_MODE:
            return
        if self._map_ready:
            self.page().runJavaScript(script)
        else:
            self._js_queue.append(script)

    def add_vehicle(self, vehicle_id: str, lat: float, lon: float,
                    color: str = ACCENT_COLOR, icon_type: str = "car"):
        self.run_js(
            f"stormAddVehicle('{vehicle_id}', {lat}, {lon}, '{color}', '{icon_type}');"
        )

    def remove_vehicle(self, vehicle_id: str):
        self.run_js(f"stormRemoveVehicle('{vehicle_id}');")

    def set_satellite_frame(self, b64: str, west: float, south: float,
                            east: float, north: float):
        self.run_js(
            f"if(window.stormSetSatelliteFrame) "
            f"stormSetSatelliteFrame({repr(b64)},{west},{south},{east},{north});"
        )

    def set_satellite_time(self, time_iso: str):
        self.run_js(f"if(window.stormSetSatelliteTime) stormSetSatelliteTime('{time_iso}');")

    def set_satellite_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(f"if(window.stormSetSatelliteVisible) stormSetSatelliteVisible({flag});")

    def set_satellite_mode(self, mode: str):
        self.run_js(f"if(window.stormSetSatelliteMode) stormSetSatelliteMode('{mode}');")

    def set_satellite_opacity(self, opacity: float):
        self.run_js(f"if(window.stormSetSatelliteOpacity) stormSetSatelliteOpacity({opacity:.3f});")

    def clear_satellite_frame(self) -> None:
        self.run_js("if(window.stormClearSatelliteFrame) stormClearSatelliteFrame();")

    # ── SfcOA (mesoanalysis) overlay ───────────────────────────────────────
    def set_mesoanalysis_frame(self, b64: str, west: float, south: float,
                                east: float, north: float):
        self.run_js(
            f"if(window.stormSetMesoanalysisFrame) "
            f"stormSetMesoanalysisFrame({repr(b64)},{west},{south},{east},{north});"
        )

    def set_mesoanalysis_opacity(self, opacity: float):
        self.run_js(
            f"if(window.stormSetMesoanalysisOpacity) "
            f"stormSetMesoanalysisOpacity({opacity:.3f});"
        )

    def set_mesoanalysis_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetMesoanalysisVisible) "
            f"stormSetMesoanalysisVisible({flag});"
        )

    def clear_mesoanalysis_frame(self) -> None:
        self.run_js(
            "if(window.stormClearMesoanalysisFrame) stormClearMesoanalysisFrame();"
        )

    def set_sfcoa_sectors(self, sectors: list[dict]):
        self.run_js(
            f"if(window.stormSetSfcOASectors) "
            f"stormSetSfcOASectors({json.dumps(json.dumps(sectors))});"
        )

    def preview_sfcoa_sector(self, sector_id: int):
        self.run_js(
            f"if(window.stormPreviewSfcOASector) "
            f"stormPreviewSfcOASector({int(sector_id)});"
        )

    def clear_sfcoa_preview(self):
        self.run_js(
            "if(window.stormClearSfcOAPreview) stormClearSfcOAPreview();"
        )

    def set_meso_sectors(self, sectors: dict):
        features = []
        for idx, bbox in sectors.items():
            if bbox:
                features.append({
                    "label": f"MESO-{idx}",
                    "west":  bbox["west"],
                    "south": bbox["south"],
                    "east":  bbox["east"],
                    "north": bbox["north"],
                })
        self.run_js(
            f"if(window.stormSetMesoSectors) stormSetMesoSectors({json.dumps(json.dumps(features))});"
        )

    def set_radar_stations(self, stations: list[dict]):
        features = []
        for station in stations:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [station["lon"], station["lat"]],
                },
                "properties": {
                    "site_id": station["site_id"],
                    "name": station.get("name", ""),
                },
            })
        geojson = {"type": "FeatureCollection", "features": features}
        self.run_js(
            f"if(window.stormSetRadarStations) stormSetRadarStations({json.dumps(json.dumps(geojson))});"
        )

    def set_radar_stations_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetRadarStationsVisible) stormSetRadarStationsVisible({flag});"
        )

    # ── CWA (County Warning Areas) overlay helpers ─────────────────────────
    def set_cwa_geojson(self, geojson: dict):
        """Set the CWA GeoJSON on the map (expects a FeatureCollection dict)."""
        self.run_js(
            f"if(window.stormSetCwaGeoJSON) stormSetCwaGeoJSON({json.dumps(json.dumps(geojson))});"
        )

    def set_cwa_visible(self, visible: bool):
        """Toggle CWA overlay visibility."""
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetCwaVisible) stormSetCwaVisible({flag});"
        )

    def load_cwa_shapefile(self, shp_base: str | None = None):
        """Load a local CWA shapefile (shp + dbf) and push it to the map as GeoJSON.

        shp_base may be a basename (without extension) or a full .shp path. If
        omitted, defaults to the bundled cwa_shp/w_16ap26 shapefile.
        """
        import os, struct, json

        if shp_base is None:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'cwa_shp', 'w_16ap26'))
        else:
            base = shp_base
        shp_path = base if base.lower().endswith('.shp') else base + '.shp'
        dbf_path = shp_path[:-4] + '.dbf'

        try:
            with open(shp_path, 'rb') as f:
                shp_data = f.read()
            with open(dbf_path, 'rb') as f:
                dbf_data = f.read()
        except Exception:
            return

        # Minimal SHP parser (Polygon type 5) — adapted from archive fetcher.
        def _parse_shp(data: bytes):
            if len(data) < 100:
                return []
            pos = 100
            geometries = []
            while pos < len(data):
                if pos + 12 > len(data):
                    break
                _rec_num, content_words = struct.unpack_from('>ii', data, pos)
                pos += 8
                content_bytes = content_words * 2
                if content_bytes < 4 or pos + content_bytes > len(data):
                    break
                shape_type = struct.unpack_from('<i', data, pos)[0]
                if shape_type == 0:
                    geometries.append(None)
                    pos += content_bytes
                    continue
                if shape_type != 5:
                    geometries.append(None)
                    pos += content_bytes
                    continue
                offset = pos + 4
                if offset + 32 + 8 > len(data):
                    geometries.append(None)
                    pos += content_bytes
                    continue
                offset += 32
                num_parts, num_points = struct.unpack_from('<ii', data, offset)
                offset += 8
                if num_parts <= 0 or num_points <= 0:
                    geometries.append(None)
                    pos += content_bytes
                    continue
                part_starts = list(struct.unpack_from(f'<{num_parts}i', data, offset))
                offset += num_parts * 4
                pts_raw = struct.unpack_from(f'<{num_points * 2}d', data, offset)
                points = [(pts_raw[i * 2], pts_raw[i * 2 + 1]) for i in range(num_points)]
                rings = []
                for idx_r, start in enumerate(part_starts):
                    end = part_starts[idx_r + 1] if idx_r + 1 < num_parts else num_points
                    ring = [list(pt) for pt in points[start:end]]
                    rings.append(ring)
                geometries.append({'type': 'Polygon', 'coordinates': rings})
                pos += content_bytes
            return geometries

        # Minimal DBF parser — adapted from archive fetcher.
        def _parse_dbf(data: bytes):
            if len(data) < 32:
                return []
            num_records = struct.unpack_from('<I', data, 4)[0]
            header_bytes = struct.unpack_from('<H', data, 8)[0]
            record_bytes = struct.unpack_from('<H', data, 10)[0]
            fields = []
            pos = 32
            while pos < header_bytes - 1 and data[pos] != 0x0D:
                raw_name = data[pos:pos + 11]
                name = raw_name.split(b"\x00")[0].decode('ascii', errors='replace').strip()
                ftype = chr(data[pos + 11])
                flen = data[pos + 16]
                fields.append((name, ftype, flen))
                pos += 32
            records = []
            rec_pos = header_bytes
            for _ in range(num_records):
                if rec_pos + record_bytes > len(data):
                    break
                deletion_flag = data[rec_pos]
                if deletion_flag == 0x2A:  # '*' = deleted
                    rec_pos += record_bytes
                    continue
                field_pos = rec_pos + 1
                rec = {}
                for name, ftype, flen in fields:
                    raw = data[field_pos:field_pos + flen].decode('ascii', errors='replace').strip()
                    if ftype == 'N':
                        try:
                            rec[name] = float(raw) if raw else None
                        except ValueError:
                            rec[name] = None
                    else:
                        rec[name] = raw
                    field_pos += flen
                records.append(rec)
                rec_pos += record_bytes
            return records

        geoms = _parse_shp(shp_data)
        recs = _parse_dbf(dbf_data)
        features = []
        for geom, rec in zip(geoms, recs):
            if geom is None:
                continue
            features.append({'type': 'Feature', 'geometry': geom, 'properties': rec})
        geojson = {'type': 'FeatureCollection', 'features': features}
        self.set_cwa_geojson(geojson)

    def set_route(self, geojson_str: str, dest_lon: float, dest_lat: float):
        """Draw a route polyline on the map and place a destination marker."""
        self.run_js(
            f"if(window.stormSetRoute) stormSetRoute({json.dumps(geojson_str)});"
        )
        self.run_js(
            f"if(window.stormSetDestinationMarker) "
            f"stormSetDestinationMarker({dest_lon}, {dest_lat});"
        )

    def clear_route(self):
        """Remove the route line and destination marker."""
        self.run_js("if(window.stormClearRoute) stormClearRoute();")

    def set_route_pick_mode(self, active: bool):
        """Toggle crosshair pick mode for destination selection."""
        flag = "true" if active else "false"
        self.run_js(
            f"if(window.stormSetRoutePickMode) stormSetRoutePickMode({flag});"
        )

    def set_sounding_mode(self, active: bool):
        """Toggle HRRR sounding-click mode: map clicks emit lat/lon instead of normal actions."""
        flag = "true" if active else "false"
        self.run_js(f"window._soundingModeActive = {flag};")
        cursor = Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    def set_obs_sounding_mode(self, active: bool):
        """Toggle OBS sounding mode: station dots become clickable."""
        flag = "true" if active else "false"
        self.run_js(f"window._soundingObsModeActive = {flag};")
        if not active:
            self.setCursor(Qt.CursorShape.ArrowCursor)


    def set_sounding_stations(self, geojson_str: str):
        """Inject sounding station GeoJSON and make the layer visible."""
        escaped = geojson_str.replace("\\", "\\\\").replace("`", "\\`")
        self.run_js(f"if(window.stormSetSoundingStations) stormSetSoundingStations(`{escaped}`);")

    def clear_sounding_stations(self):
        """Hide and clear the sounding station layer."""
        self.run_js("if(window.stormClearSoundingStations) stormClearSoundingStations();")

    def preview_meso_sector(self, idx: int | None):
        if idx in (1, 2):
            self.run_js(
                f"if(window.stormPreviewMesoSector) stormPreviewMesoSector('MESO-{idx}');"
            )
        else:
            self.run_js("if(window.stormClearMesoPreview) stormClearMesoPreview();")

    def fly_to(self, lat: float, lon: float, zoom: float = None):
        zoom_str = str(zoom) if zoom is not None else "undefined"
        self.run_js(f"stormFlyTo({lat}, {lon}, {zoom_str});")

    def set_follow(self, enabled: bool):
        self.run_js(f"stormSetFollow({'true' if enabled else 'false'});")

    def follow_move(self, lat: float, lon: float):
        self.run_js(f"stormFollowMove({lat}, {lon});")

    def set_annotation_mode(self, active: bool):
        if active:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.add('annotating');el.classList.remove('drawing','measuring');}})();"
            )
        else:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.remove('annotating');}})();"
            )

    def set_measure_mode(self, active: bool):
        if active:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.add('measuring');el.classList.remove('annotating','drawing');}})();"
                "if(window.stormMeasureActivate) stormMeasureActivate(true);"
            )
        else:
            self.run_js(
                "(function(){var el=document.getElementById('map');"
                " if(el){el.classList.remove('measuring');}})();"
                "if(window.stormMeasureActivate) stormMeasureActivate(false);"
            )

    def measure_click(self, lat: float, lon: float):
        self.run_js(f"if(window.stormMeasureClick) stormMeasureClick({lat},{lon});")

    def clear_measure(self):
        self.run_js("if(window.stormMeasureClear) stormMeasureClear();")

    def set_drawing_mode(self, active: bool, type_key: str = "") -> None:
        flag = "true" if active else "false"
        self.run_js(
            f"if(window.stormDrawingModeSet) stormDrawingModeSet({flag}, '{type_key}');"
        )

    def set_drawing_draggable(self, drawing_id: str, on: bool) -> None:
        self.run_js(f"if(window.stormSetDrawingDraggable) stormSetDrawingDraggable('{drawing_id}', {'true' if on else 'false'});")

    def drawing_update_preview(self, points: list) -> None:
        import json
        self.run_js(
            f"if(window.stormDrawingUpdatePreview) stormDrawingUpdatePreview({json.dumps(json.dumps(points))});"
        )

    def add_drawing(self, drawing) -> None:
        import json
        payload = json.dumps(drawing.to_dict())
        self.run_js(f"if(window.stormAddDrawing) stormAddDrawing('{drawing.id}', {json.dumps(payload)});")

    def remove_drawing(self, drawing_id: str) -> None:
        self.run_js(f"if(window.stormRemoveDrawing) stormRemoveDrawing('{drawing_id}');")

    def add_annotation(self, annotation) -> None:
        if getattr(annotation, "type_key", "") == "storm_motion":
            return
        label = annotation.label.replace("'", "\\'")
        self.run_js(
            f"stormAddAnnotation('{annotation.id}', {annotation.lat}, "
            f"{annotation.lon}, '{annotation.type_key}', '{label}');"
        )

    def remove_annotation(self, annotation_id: str) -> None:
        self.run_js(f"stormRemoveAnnotation('{annotation_id}');")

    def set_annotation_draggable(self, annotation_id: str, on: bool) -> None:
        self.run_js(f"stormSetAnnotationDraggable('{annotation_id}', {'true' if on else 'false'});")

    def move_annotation(self, annotation_id: str, lat: float, lon: float) -> None:
        self.run_js(f"stormMoveAnnotation('{annotation_id}', {lat}, {lon});")

    def add_storm_cone(self, cone) -> None:
        import json
        geojson_str = json.dumps(cone.build_geojson())
        self.run_js(f"stormAddStormCone('{cone.id}', {json.dumps(geojson_str)}, {cone.lat}, {cone.lon});")

    def remove_storm_cone(self, cone_id: str) -> None:
        self.run_js(f"stormRemoveStormCone('{cone_id}');")

    def set_storm_cone_draggable(self, cone_id: str, on: bool) -> None:
        self.run_js(f"if(window.stormSetStormConeDraggable) stormSetStormConeDraggable('{cone_id}', {'true' if on else 'false'});")

    def set_storm_cone_placement_mode(self, active: bool) -> None:
        self.run_js(f"if(window.stormSetStormConePlacementMode) stormSetStormConePlacementMode({'true' if active else 'false'});")

    def add_station_plot(self, vehicle_id: str, lat: float, lon: float, png_bytes: bytes) -> None:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self.run_js(f"stormAddStationPlot('{vehicle_id}', {lat}, {lon}, '{b64}');")

    def remove_station_plot(self, vehicle_id: str) -> None:
        self.run_js(f"stormRemoveStationPlot('{vehicle_id}');")

    def set_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetStationPlotsVisible({v});")

    def add_surface_station_plot(self, station_id: str, lat: float, lon: float, png_bytes: bytes, name: str = "") -> None:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        import json
        self.run_js(
            f"stormAddSurfaceStationPlot({json.dumps(station_id)}, {lat}, {lon}, '{b64}', {json.dumps(name)});"
        )

    def remove_surface_station_plot(self, station_id: str) -> None:
        self.run_js(f"stormRemoveSurfaceStationPlot('{station_id}');")

    def set_surface_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetSurfaceStationPlotsVisible({v});")

    def load_deploy_locs(self, points: list) -> None:
        import json
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
             "properties": {
                 "rank_abi": p.get("rank_abi"),
                 "rank_aoi": p.get("rank_aoi"),
                 "rqi":      p.get("rqi"),
             }}
            for p in points
        ]}
        self.run_js(f"stormLoadDeployLocs({json.dumps(json.dumps(fc))});")

    def set_deploy_locs_visible(self, visible: bool) -> None:
        self.run_js(f"stormSetDeployLocsVisible({'true' if visible else 'false'});")

    def set_deploy_locs_metric(self, metric: str) -> None:
        import json
        self.run_js(f"stormSetDeployLocsMetric({json.dumps(metric)});")

    def set_deploy_locs_filter(self, metric: str, threshold: float) -> None:
        import json
        self.run_js(f"stormSetDeployLocsFilter({json.dumps(metric)}, {threshold});")

    def set_deploy_locs_size(self, radius: int) -> None:
        self.run_js(f"stormSetDeployLocsSize({radius});")

    def set_spc_geojson(self, cat_str: str, wind_str: str, hail_str: str, tor_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcGeoJSON) stormSetSpcGeoJSON("
            f"{json.dumps(cat_str)}, "
            f"{json.dumps(wind_str)}, "
            f"{json.dumps(hail_str)}, "
            f"{json.dumps(tor_str)}"
            ");"
        )

    def set_spc_category_visible(self, key: str, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcCategoryVisible) stormSetSpcCategoryVisible('{key}', {'true' if visible else 'false'});"
        )

    def set_spc_product_visible(self, key: str, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcProductVisible) stormSetSpcProductVisible('{key}', {'true' if visible else 'false'});"
        )

    def set_nws_warnings_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetNwsWarningsGeoJSON) stormSetNwsWarningsGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_nws_warnings_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetNwsWarningsVisible) stormSetNwsWarningsVisible({'true' if visible else 'false'});"
        )

    def set_spc_watches_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcWatchesGeoJSON) stormSetSpcWatchesGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_spc_watches_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcWatchesVisible) stormSetSpcWatchesVisible({'true' if visible else 'false'});"
        )

    def set_spc_mds_geojson(self, fc_str: str) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcMdsGeoJSON) stormSetSpcMdsGeoJSON("
            f"{json.dumps(fc_str)}"
            ");"
        )

    def set_spc_mds_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcMdsVisible) stormSetSpcMdsVisible({'true' if visible else 'false'});"
        )

    def move_layer_before(self, layer_id: str, before_layer_id: str | None) -> None:
        """Move a MapLibre layer before another layer (or to the top if before_layer_id is None)."""
        import json
        if before_layer_id is None:
            self.run_js(
                f"(function(){{ if(map.getLayer({json.dumps(layer_id)})) "
                f"map.moveLayer({json.dumps(layer_id)}); }})();"
            )
        else:
            self.run_js(
                f"(function(){{ if(map.getLayer({json.dumps(layer_id)}) && "
                f"map.getLayer({json.dumps(before_layer_id)})) "
                f"map.moveLayer({json.dumps(layer_id)}, {json.dumps(before_layer_id)}); }})();"
            )
