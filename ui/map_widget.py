# ui/map_widget.py
# Embeds a MapLibre GL JS map inside a QWebEngineView.
# Spins up a local Flask server serving the map HTML, static assets,
# and vector tiles from MBTiles — all from the same HTTP origin.

import os
import sqlite3
import threading
import zlib
import sys
import runtime_flags

# Optional Windows fallback: disable WebGL map rendering only when explicitly
# requested for troubleshooting unstable GPU/ANGLE setups.

SAFE_MAP_MODE = (
    sys.platform == "win32"
    and runtime_flags.FLAGS.safe_map_mode
)

from PyQt6.QtCore import QUrl, QTimer, pyqtSignal, QObject, pyqtSlot
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

if not SAFE_MAP_MODE:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel

from flask import Flask, Response, jsonify, send_from_directory

from config import ACCENT_COLOR

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_LAT      = 35.22
DEFAULT_LON      = -97.44
DEFAULT_ZOOM     = 6
TILE_SERVER_PORT = 8765

TILES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tiles", "storm.mbtiles")
)

STATIC_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "static")
)


def build_safe_map_html() -> str:
    return """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>STORM (Safe Map Mode)</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; background: #0A0A0F; color: #C1C9D8; }
    .wrap {
      width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
      font-family: "Segoe UI", sans-serif; text-align: center; padding: 24px; box-sizing: border-box;
    }
    .card {
      max-width: 720px; border: 1px solid #1E1E2E; border-radius: 10px;
      background: rgba(15, 15, 26, 0.92); padding: 22px 24px;
    }
    h2 { margin: 0 0 10px; color: #00CFFF; font-size: 20px; letter-spacing: 0.6px; }
    p { margin: 0; color: #B5BDCC; font-size: 13px; line-height: 1.45; }
  </style>
  <script>
    // No-op API stubs so Python runJavaScript calls remain safe.
    function _noop() {}
    window.stormAddVehicle = _noop;
    window.stormRemoveVehicle = _noop;
    window.stormFlyTo = _noop;
    window.stormAddAnnotation = _noop;
    window.stormRemoveAnnotation = _noop;
    window.stormAddStormCone = _noop;
    window.stormRemoveStormCone = _noop;
    window.stormAddStationPlot = _noop;
    window.stormRemoveStationPlot = _noop;
    window.stormSetStationPlotsVisible = _noop;
    window.stormLoadDeployLocs = _noop;
    window.stormSetDeployLocsVisible = _noop;
    window.stormMeasureActivate = _noop;
    window.stormMeasureClick = _noop;
    window.stormMeasureClear = _noop;
    window.stormAddDrawing = _noop;
    window.stormRemoveDrawing = _noop;
    window.stormDrawingModeSet = _noop;
    window.stormDrawingUpdatePreview = _noop;
    window.stormSetSpcGeoJSON = _noop;
    window.stormSetSpcCategoryVisible = _noop;
    window.stormSetSpcProductVisible = _noop;
    window.stormSetNwsWarningsGeoJSON = _noop;
    window.stormSetNwsWarningsVisible = _noop;
    window.stormSetSpcWatchesGeoJSON = _noop;
    window.stormSetSpcWatchesVisible = _noop;
    window.stormSetSpcMdsGeoJSON = _noop;
    window.stormSetSpcMdsVisible = _noop;
  </script>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h2>Safe Map Mode Enabled</h2>
      <p>Map rendering is running in safe mode for this session.
      Relaunch without <code>--safe-map-mode</code> to restore normal map rendering.</p>
    </div>
  </div>
</body>
</html>"""


# ── Map HTML ──────────────────────────────────────────────────────────────────

def build_map_html() -> str:
    """Build the full HTML page for the MapLibre map."""
    tile_url = f"http://localhost:{TILE_SERVER_PORT}/tiles/{{z}}/{{x}}/{{y}}.pbf"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>STORM</title>
  <script src="http://localhost:{TILE_SERVER_PORT}/static/maplibre-gl.js"></script>
  <link href="http://localhost:{TILE_SERVER_PORT}/static/maplibre-gl.css" rel="stylesheet"/>
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

    /* Lift bottom controls above Qt status pills and align left controls with legend. */
    .maplibregl-ctrl-bottom-left {{
      left: 10px !important;
      bottom: 46px !important;
      width: 172px !important;
      display: flex !important;
      justify-content: center !important;
    }}

    .maplibregl-ctrl-bottom-left .maplibregl-ctrl {{
      margin: 0 !important;
    }}

    .maplibregl-ctrl-bottom-right {{
      right: 10px !important;
      bottom: 62px !important;
    }}

    .maplibregl-ctrl-bottom-right .maplibregl-ctrl {{
      margin: 0 !important;
    }}

    /* ── Legend ── */
    #storm-legend {{
      position: absolute;
      bottom: 84px;
      left: 10px;
      width: 172px;
      display: flex;
      flex-direction: column;
      align-items: center;
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
    window.stormAddAnnotation = _stormNoop;
    window.stormRemoveAnnotation = _stormNoop;
    window.stormAddStormCone = _stormNoop;
    window.stormRemoveStormCone = _stormNoop;
    window.stormAddStationPlot = _stormNoop;
    window.stormRemoveStationPlot = _stormNoop;
    window.stormSetStationPlotsVisible = _stormNoop;
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
    window._stormDrawings = {{}};
    window._stormDrawingActive = false;
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
      }});
    }}

    // ── Map Style ─────────────────────────────────────────────────────────
    const STORM_STYLE = {{
      version: 8,
      name: "STORM Dark",
      glyphs: "http://localhost:{TILE_SERVER_PORT}/static/fonts/{{fontstack}}/{{range}}.pbf",
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
            "line-color": "#2A2A3E",
            "line-width": 1.5,
            "line-dasharray": [4, 3]
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
      const glyphCheck = "http://localhost:{TILE_SERVER_PORT}/static/fonts/Noto%20Sans%20Regular/0-255.pbf";
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
          'circle-color': '#FFD166',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#0A0A0F',
          'circle-opacity': 0.85
        }}
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
          'fill-opacity': 0.25
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

      // ── Significant-area hatch pattern (diagonal white lines) ────────────
      // Build the pixel array directly — no canvas API needed, so this works
      // reliably inside QWebEngineView where canvas readback can silently fail.
      (function() {{
        var sz = 16;
        var data = new Uint8Array(sz * sz * 4);
        for (var y = 0; y < sz; y++) {{
          for (var x = 0; x < sz; x++) {{
            var i = (y * sz + x) * 4;
            // NW→SE diagonal: pixels where (x - y) mod sz < 2 form a 2-px stripe
            var d = ((x - y) % sz + sz) % sz;
            if (d < 2) {{
              data[i]   = 255;  // R
              data[i+1] = 255;  // G
              data[i+2] = 255;  // B
              data[i+3] = 180;  // A (~70 % opacity)
            }}
            // else: alpha stays 0 → transparent
          }}
        }}
        try {{
          map.addImage('sig-hatch', {{width: sz, height: sz, data: data}});
          console.log('sig-hatch registered OK');
        }} catch(e) {{
          console.warn('sig-hatch registration failed:', e);
        }}
      }})();

      function _addSpcProductLayers(name, colorExpr) {{
        map.addLayer({{
          id: 'spc-' + name + '-fill',
          type: 'fill',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          paint: {{'fill-color': colorExpr, 'fill-opacity': 0.25}}
        }});
        map.addLayer({{
          id: 'spc-' + name + '-line',
          type: 'line',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          paint: {{'line-color': colorExpr, 'line-width': 2, 'line-opacity': 0.85}}
        }});
        // Significant-area layer: white diagonal hatching over SIGN features.
        // SIGN features are transparent in the fill layer above so only the
        // hatch pattern renders for those polygons.
        // Use fill-pattern if the image registered; fall back to a semi-opaque
        // white fill so the areas are still visible even in environments where
        // fill-pattern is unsupported.
        var _hasPat = map.hasImage('sig-hatch');
        console.log('sig-hatch available for spc-' + name + ':', _hasPat);
        var _sigPaint = _hasPat
          ? {{'fill-pattern': 'sig-hatch'}}
          : {{'fill-color': '#FFFFFF', 'fill-opacity': 0.45}};
        map.addLayer({{
          id: 'spc-' + name + '-sig',
          type: 'fill',
          source: 'spc-' + name,
          layout: {{'visibility': 'none'}},
          filter: ['any',
            ['==', ['get', 'LABEL'], 'SIGN'],
            ['==', ['get', 'label'], 'SIGN']
          ],
          paint: _sigPaint
        }});
      }}

      // Wind/hail probability color scale (matches SPC official products)
      // 5% = tan/brown, 15% = yellow, 30% = orange, 45% = red, 60% = magenta
      var windHailColor = ['match', ['get', 'LABEL'],
        'SIGN', 'rgba(0,0,0,0)',
        '5',  '#C1A353',
        '15', '#FFFF00',
        '30', '#FF6600',
        '45', '#FF0000',
        '60', '#FF00FF',
        '#C1A353'];

      // Tornado probability color scale (matches SPC official products)
      var torColor = ['match', ['get', 'LABEL'],
        'SIGN', 'rgba(0,0,0,0)',
        '2',  '#008B00',
        '5',  '#8B4726',
        '10', '#FFA500',
        '15', '#FF0000',
        '30', '#FF00FF',
        '45', '#912CEE',
        '60', '#104E8B',
        '#008B00'];

      _addSpcProductLayers('wind', windHailColor);
      _addSpcProductLayers('hail', windHailColor);
      _addSpcProductLayers('tor', torColor);

      map.addLayer({{
        id: 'spc-watches-fill',
        type: 'fill',
        source: 'spc-watches',
        layout: {{'visibility': 'none'}},
        paint: {{
          'fill-color': ['coalesce', ['get', 'watch_color'], '#FFD700'],
          'fill-opacity': 0.18
        }}
      }});
      map.addLayer({{
        id: 'spc-watches-line',
        type: 'line',
        source: 'spc-watches',
        layout: {{'visibility': 'none'}},
        paint: {{
          'line-color': ['coalesce', ['get', 'watch_color'], '#FFD700'],
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

      // ── Hazard hover tooltip ───────────────────────────────────────────
      var _htip = document.getElementById('hazard-tooltip');
      if (_htip) {{
        var _hazardLayers = [
          'spc-cat-fill','spc-tor-fill','spc-wind-fill','spc-hail-fill',
          'spc-watches-fill','spc-mds-fill','nws-warnings-fill'
        ].filter(function(l) {{
          return map.getLayer(l) &&
                 map.getLayoutProperty(l, 'visibility') === 'visible';
        }});
        var _hits = _hazardLayers.length > 0
          ? map.queryRenderedFeatures(e.point, {{layers: _hazardLayers}})
          : [];
        if (_hits.length > 0) {{
          var _f = _hits[0];
          var _p = _f.properties || {{}};
          var _src = _f.source || '';
          var _lbl = '';
          if (_src === 'spc-cat') {{
            var _catNames = {{MRGL:'Marginal',SLGHT:'Slight',ENH:'Enhanced',MDT:'Moderate',HIGH:'High'}};
            _lbl = _catNames[_p.cat] || _p.cat || 'Outlook';
          }} else if (_src === 'spc-tor') {{
            var _tv = _p.LABEL || _p.label || _p.DN || _p.dn || '—';
            _lbl = (_tv === 'SIGN') ? 'Sig Tor (Hatched)' : 'Tor: ' + _tv;
          }} else if (_src === 'spc-wind') {{
            var _wv = _p.LABEL || _p.label || _p.DN || _p.dn || '—';
            _lbl = (_wv === 'SIGN') ? 'Sig Wind (Hatched)' : 'Wind: ' + _wv;
          }} else if (_src === 'spc-hail') {{
            var _hv = _p.LABEL || _p.label || _p.DN || _p.dn || '—';
            _lbl = (_hv === 'SIGN') ? 'Sig Hail (Hatched)' : 'Hail: ' + _hv;
          }} else if (_src === 'spc-watches') {{
            _lbl = _p.event || _p.headline || 'Watch';
          }} else if (_src === 'spc-mds') {{
            _lbl = _p.name || 'Mesoscale Discussion';
          }} else if (_src === 'nws-warnings') {{
            _lbl = _p.event || _p.headline || 'Warning';
          }}
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
          _htip.style.display = 'none';
        }}
      }}
    }});

    map.on("click", function(e) {{
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
      // Check SPC hazard polygon clicks (outlook + MDs) — lower priority than drawings/cones
      var spcClickLayers = ['spc-cat-fill', 'spc-mds-fill'].filter(function(l) {{ return map.getLayer(l); }});
      if (spcClickLayers.length > 0) {{
        var spcHits = map.queryRenderedFeatures(e.point, {{layers: spcClickLayers}});
        if (spcHits.length > 0) {{
          var hit = spcHits[0];
          var payload = JSON.stringify({{source: hit.source, properties: hit.properties || {{}}}});
          if (bridge) bridge.on_feature_click(payload);
          return;
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

    // ── Python-callable Functions ─────────────────────────────────────────
    window.stormAddVehicle = function(id, lat, lon, color) {{
      const existing = document.getElementById("vehicle-" + id);
      if (existing) existing.remove();

      const el = document.createElement("div");
      el.id = "vehicle-" + id;
      el.style.cssText = `
        width: 12px; height: 12px; border-radius: 50%;
        background-color: ${{color || "{ACCENT_COLOR}"}};
        box-shadow: 0 0 8px ${{color || "{ACCENT_COLOR}"}};
        cursor: pointer;
      `;
      new maplibregl.Marker({{ element: el }})
        .setLngLat([lon, lat])
        .setPopup(new maplibregl.Popup({{ offset: 16 }}).setText(id))
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
        if (bridge) bridge.on_annotation_click(id);
      }});
      const marker = new maplibregl.Marker({{element: el}})
        .setLngLat([lon, lat])
        .addTo(map);
      window._stormAnnotations[id] = marker;
    }};

    window.stormRemoveAnnotation = function(id) {{
      if (window._stormAnnotations[id]) {{
        window._stormAnnotations[id].remove();
        delete window._stormAnnotations[id];
      }}
    }};

    // ── Drawing Annotations (Fronts & Custom Shapes) ──────────────────────
    window._stormDrawings = {{}};

    function _computeCentroid(coords) {{
      var sumLon = 0, sumLat = 0;
      coords.forEach(function(c) {{ sumLon += c[0]; sumLat += c[1]; }});
      return [sumLon / coords.length, sumLat / coords.length];
    }}

    window.stormAddDrawing = function(id, jsonStr) {{
      stormRemoveDrawing(id);
      var d = JSON.parse(jsonStr);
      window._stormDrawings[id] = d;

      var coords = d.coordinates.map(function(c) {{ return [c[1], c[0]]; }});
      var geometry;
      if (d.drawing_type === 'polygon' && coords.length >= 3) {{
        geometry = {{type:'Polygon', coordinates:[[...coords, coords[0]]]}};
      }} else {{
        geometry = {{type:'LineString', coordinates:coords}};
      }}

      map.addSource('drawing-' + id, {{
        type: 'geojson',
        data: {{
          type: 'FeatureCollection',
          features: [{{
            type: 'Feature',
            geometry: geometry,
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
          var centroid = _computeCentroid(coords);
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
            map.getCanvas().style.cursor = 'pointer';
          }});
          map.on('mouseleave', layerId, function() {{
            map.getCanvas().style.cursor = '';
          }});
        }});
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

    window.stormAddStormCone = function(id, geojsonStr) {{
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
        map.getCanvas().style.cursor = 'pointer';
      }});
      map.on('mouseleave', 'storm-cone-fill-' + id, function() {{
        map.getCanvas().style.cursor = '';
      }});

      window._stormCones[id] = true;
    }};

    window.stormRemoveStormCone = function(id) {{
      var layers = ['storm-cone-fill-' + id, 'storm-cone-outline-' + id, 'storm-cone-ribs-' + id, 'storm-cone-labels-' + id];
      layers.forEach(function(l) {{
        if (map.getLayer(l)) map.removeLayer(l);
      }});
      if (map.getSource('storm-cone-' + id)) map.removeSource('storm-cone-' + id);
      delete window._stormCones[id];
    }};

    // ── Station Plots ─────────────────────────────────────────────────────
    window._stormStationPlots = {{}};
    window._stormStationPlotsVisible = true;

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
      map.getSource('deploy-locs').setData(JSON.parse(geojsonStr));
    }};
    window.stormSetDeployLocsVisible = function(visible) {{
      map.setLayoutProperty('deploy-locs-circles', 'visibility', visible ? 'visible' : 'none');
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
      _setLayerVisibility('spc-' + k + '-fill', !!visible);
      _setLayerVisibility('spc-' + k + '-line', !!visible);
      _setLayerVisibility('spc-' + k + '-sig',  !!visible);
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


# ── Local Flask Server ────────────────────────────────────────────────────────

def create_server(mbtiles_path: str, static_path: str) -> Flask:
    """
    Flask server serving:
      GET /                        — map HTML page
      GET /static/<file>           — MapLibre JS/CSS assets
      GET /fonts/<stack>/<range>   — placeholder (fonts served via fallback)
      GET /tiles/metadata.json     — tile metadata
      GET /tiles/{z}/{x}/{y}.pbf   — vector tiles from MBTiles
    """
    app = Flask(__name__, static_folder=None)
    app.logger.disabled = True

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    html = build_map_html()
    fonts_path = os.path.join(static_path, "fonts")

    @app.route("/")
    def serve_map():
        return Response(html, mimetype="text/html")

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        try:
            return send_from_directory(static_path, filename)
        except Exception:
            # Missing glyph range (trimmed font set) — return empty PBF so
            # MapLibre skips silently instead of logging an error.
            if filename.endswith(".pbf"):
                return Response(b"", mimetype="application/x-protobuf")
            raise

    @app.route("/fonts/<path:filename>")
    def serve_fonts(filename):
        # Serve local glyph PBF files for MapLibre text labels.
        return send_from_directory(fonts_path, filename)

    @app.route("/tiles/metadata.json")
    def serve_metadata():
        try:
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name, value FROM metadata")
            meta = dict(cursor.fetchall())
            conn.close()
        except Exception:
            meta = {}

        bounds = meta.get("bounds", "-116.0,28.0,-82.0,49.0").split(",")
        center = meta.get("center", f"{DEFAULT_LON},{DEFAULT_LAT},6").split(",")

        resp = jsonify({
            "tilejson": "2.2.0",
            "tiles": [f"http://localhost:{TILE_SERVER_PORT}/tiles/{{z}}/{{x}}/{{y}}.pbf"],
            "minzoom": int(meta.get("minzoom", 0)),
            "maxzoom": int(meta.get("maxzoom", 14)),
            "bounds": [float(b) for b in bounds],
            "center": [float(c) for c in center],
            "format": "pbf",
            "name": meta.get("name", "STORM Plains")
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/tiles/<int:z>/<int:x>/<int:y>.pbf")
    def serve_tile(z, x, y):
        # Flip Y: MBTiles TMS -> XYZ
        y_tms = (1 << z) - 1 - y

        try:
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tile_data FROM tiles "
                "WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, y_tms)
            )
            row = cursor.fetchone()
            conn.close()
        except Exception as e:
            return Response(f"DB error: {e}", status=500)

        if row is None:
            return Response("", status=204)

        tile_data = bytes(row[0])

        try:
            tile_data = zlib.decompress(tile_data, 32 + zlib.MAX_WBITS)
        except Exception:
            pass

        return Response(
            tile_data,
            status=200,
            headers={
                "Content-Type": "application/x-protobuf",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "max-age=3600"
            }
        )

    return app


def start_server(mbtiles_path: str, static_path: str, port: int):
    """Start the Flask server in a background daemon thread."""
    if not os.path.exists(mbtiles_path):
        print(f"[STORM] WARNING: MBTiles not found at {mbtiles_path}")
        return

    app = create_server(mbtiles_path, static_path)

    def run():
        try:
            app.run(host="127.0.0.1", port=port, threaded=True)
        except Exception as e:
            print(f"[STORM] Server ERROR: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    print(f"[STORM] Server running at http://localhost:{port}/")


# ── Qt Bridge ─────────────────────────────────────────────────────────────────

class MapBridge(QObject):
    map_clicked        = pyqtSignal(float, float)
    map_moved          = pyqtSignal(float, float, float)
    feature_clicked    = pyqtSignal(str)
    annotation_clicked = pyqtSignal(str)
    storm_cone_clicked = pyqtSignal(str)
    map_double_clicked = pyqtSignal(float, float)
    drawing_clicked    = pyqtSignal(str)

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

    @pyqtSlot(str)
    def on_storm_cone_click(self, cone_id: str):
        self.storm_cone_clicked.emit(cone_id)

    @pyqtSlot(float, float)
    def on_map_dblclick(self, lat: float, lon: float):
        self.map_double_clicked.emit(lat, lon)

    @pyqtSlot(str)
    def on_drawing_click(self, drawing_id: str):
        self.drawing_clicked.emit(drawing_id)


# ── Map Widget ────────────────────────────────────────────────────────────────

class MapWidget(QWidget if SAFE_MAP_MODE else QWebEngineView):
    map_clicked        = pyqtSignal(float, float)
    map_moved          = pyqtSignal(float, float, float)
    feature_clicked    = pyqtSignal(str)
    annotation_clicked = pyqtSignal(str)
    storm_cone_clicked = pyqtSignal(str)
    map_double_clicked = pyqtSignal(float, float)
    drawing_clicked    = pyqtSignal(str)

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
            return

        start_server(TILES_PATH, STATIC_PATH, TILE_SERVER_PORT)

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
        self.bridge.storm_cone_clicked.connect(self.storm_cone_clicked)
        self.bridge.map_double_clicked.connect(self.map_double_clicked)
        self.bridge.drawing_clicked.connect(self.drawing_clicked)

        # Queue for JS calls that arrive before the page has loaded
        self._map_ready = False
        self._js_queue: list[str] = []
        self.loadFinished.connect(self._on_page_loaded)

        # Delay load to give Flask time to start
        QTimer.singleShot(600, self._load_map)

    def _load_map(self):
        self.load(QUrl(f"http://localhost:{TILE_SERVER_PORT}/"))

    def _on_page_loaded(self, ok: bool):
        if ok:
            self._map_ready = True
            for script in self._js_queue:
                self.page().runJavaScript(script)
            self._js_queue.clear()

    def run_js(self, script: str):
        if SAFE_MAP_MODE:
            return
        if self._map_ready:
            self.page().runJavaScript(script)
        else:
            self._js_queue.append(script)

    def add_vehicle(self, vehicle_id: str, lat: float, lon: float,
                    color: str = ACCENT_COLOR):
        self.run_js(
            f"stormAddVehicle('{vehicle_id}', {lat}, {lon}, '{color}');"
        )

    def remove_vehicle(self, vehicle_id: str):
        self.run_js(f"stormRemoveVehicle('{vehicle_id}');")

    def fly_to(self, lat: float, lon: float, zoom: float = None):
        zoom_str = str(zoom) if zoom is not None else "undefined"
        self.run_js(f"stormFlyTo({lat}, {lon}, {zoom_str});")

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

    def add_storm_cone(self, cone) -> None:
        import json
        geojson_str = json.dumps(cone.build_geojson())
        self.run_js(f"stormAddStormCone('{cone.id}', {json.dumps(geojson_str)});")

    def remove_storm_cone(self, cone_id: str) -> None:
        self.run_js(f"stormRemoveStormCone('{cone_id}');")

    def add_station_plot(self, vehicle_id: str, lat: float, lon: float, png_bytes: bytes) -> None:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self.run_js(f"stormAddStationPlot('{vehicle_id}', {lat}, {lon}, '{b64}');")

    def remove_station_plot(self, vehicle_id: str) -> None:
        self.run_js(f"stormRemoveStationPlot('{vehicle_id}');")

    def set_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetStationPlotsVisible({v});")

    def load_deploy_locs(self, points: list) -> None:
        import json
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
             "properties": {}}
            for p in points
        ]}
        self.run_js(f"stormLoadDeployLocs({json.dumps(json.dumps(fc))});")

    def set_deploy_locs_visible(self, visible: bool) -> None:
        self.run_js(f"stormSetDeployLocsVisible({'true' if visible else 'false'});")

    def set_spc_geojson(self, cat_fc: dict, wind_fc: dict, hail_fc: dict, tor_fc: dict) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcGeoJSON) stormSetSpcGeoJSON("
            f"{json.dumps(json.dumps(cat_fc))}, "
            f"{json.dumps(json.dumps(wind_fc))}, "
            f"{json.dumps(json.dumps(hail_fc))}, "
            f"{json.dumps(json.dumps(tor_fc))}"
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

    def set_nws_warnings_geojson(self, fc: dict) -> None:
        import json
        self.run_js(
            "if(window.stormSetNwsWarningsGeoJSON) stormSetNwsWarningsGeoJSON("
            f"{json.dumps(json.dumps(fc))}"
            ");"
        )

    def set_nws_warnings_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetNwsWarningsVisible) stormSetNwsWarningsVisible({'true' if visible else 'false'});"
        )

    def set_spc_watches_geojson(self, fc: dict) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcWatchesGeoJSON) stormSetSpcWatchesGeoJSON("
            f"{json.dumps(json.dumps(fc))}"
            ");"
        )

    def set_spc_watches_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcWatchesVisible) stormSetSpcWatchesVisible({'true' if visible else 'false'});"
        )

    def set_spc_mds_geojson(self, fc: dict) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcMdsGeoJSON) stormSetSpcMdsGeoJSON("
            f"{json.dumps(json.dumps(fc))}"
            ");"
        )

    def set_spc_mds_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSpcMdsVisible) stormSetSpcMdsVisible({'true' if visible else 'false'});"
        )

