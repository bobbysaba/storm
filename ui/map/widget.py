
import json
import logging
import sys
import runtime_flags

from PyQt6.QtCore import QUrl, QTimer, pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

from config import ACCENT_COLOR
from ui.map.bridge import MapBridge
from ui.map.html import STATIC_PATH, TILES_PATH, build_map_html
from ui import palette as P

# optional Windows fallback: disable WebGL map rendering only when explicitly
SAFE_MAP_MODE = (
    sys.platform == "win32"
    and runtime_flags.FLAGS.safe_map_mode
)

if not SAFE_MAP_MODE:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebChannel import QWebChannel

log = logging.getLogger(__name__)


def _no_result(script: str) -> str:
    """The script with a final `void 0`, so its completion value is undefined.

    QtWebEngine sends a script's completion value (its last expression) back
    from the page to this process even when no callback asks for it. MapLibre
    methods return the map itself (map.setLayoutProperty(...) is `this`), so a
    script ending in one made Chromium serialize the whole map: a large
    returned object cost about 0.6 s per call, against under 0.1 ms with
    `void 0` (measured 2026-11-11)."""
    return f"{script}\n;void 0;"


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
    private_pin_route_requested = pyqtSignal(float, float, str)
    context_menu_requested = pyqtSignal(float, float, str)  # lat, lon, feature json or ""
    track_point_add_requested = pyqtSignal(float, float)          # lat, lon
    track_point_selected      = pyqtSignal(int)                   # point_id
    track_point_moved         = pyqtSignal(int, float, float, bool)  # point_id, lat, lon, keep_time
    map_double_clicked    = pyqtSignal(float, float)
    drawing_clicked       = pyqtSignal(str)
    radar_station_clicked = pyqtSignal(str)
    lidar_location_clicked = pyqtSignal(str)
    sounding_clicked             = pyqtSignal(float, float)
    obs_sounding_station_clicked = pyqtSignal(str, str, float, float, float)  # id, name, lat, lon, elev
    asos_bbox_selected    = pyqtSignal(float, float, float, float)  # west, south, east, north
    asos_box_cancelled    = pyqtSignal()                            # Esc / right-click while drawing
    user_dragged          = pyqtSignal()
    satellite_idle        = pyqtSignal()
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
            msg.setStyleSheet(f"color: {P.TEXT_SECONDARY}; font-size: 13px;")
            layout.addWidget(msg)
            self._map_ready = True
            self._js_queue = []
            QTimer.singleShot(0, self.map_ready.emit)
            return

        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from ui.map.tile_scheme_handler import StormSchemeHandler
        self._scheme_handler = StormSchemeHandler(
            TILES_PATH, STATIC_PATH, build_map_html()
        )
        # the default profile is process-wide, and a second install for the
        # scheme is ignored (Qt only logs it): the window closing before a
        # new one is built (Change Day) removes its handler in shutdown()
        QWebEngineProfile.defaultProfile().installUrlSchemeHandler(
            b"storm", self._scheme_handler
        )
        # public accessor so RadarOverlay can push PNG bytes for URL-based serving
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
        self._map_page = self.page()

        self.bridge.map_clicked.connect(self.map_clicked)
        self.bridge.map_moved.connect(self.map_moved)
        self.bridge.feature_clicked.connect(self.feature_clicked)
        self.bridge.annotation_clicked.connect(self.annotation_clicked)
        self.bridge.annotation_drag_ended.connect(self.annotation_drag_ended)
        self.bridge.drawing_drag_ended.connect(self.drawing_drag_ended)
        self.bridge.storm_cone_clicked.connect(self.storm_cone_clicked)
        self.bridge.storm_cone_drag_ended.connect(self.storm_cone_drag_ended)
        self.bridge.storm_cone_place_drag_ended.connect(self.storm_cone_place_drag_ended)
        self.bridge.private_pin_route_requested.connect(self.private_pin_route_requested)
        self.bridge.context_menu_requested.connect(self.context_menu_requested)
        self.bridge.track_point_add_requested.connect(self.track_point_add_requested)
        self.bridge.track_point_selected.connect(self.track_point_selected)
        self.bridge.track_point_moved.connect(self.track_point_moved)
        self.bridge.map_double_clicked.connect(self.map_double_clicked)
        self.bridge.drawing_clicked.connect(self.drawing_clicked)
        self.bridge.radar_station_clicked.connect(self.radar_station_clicked)
        self.bridge.lidar_location_clicked.connect(self.lidar_location_clicked)
        self.bridge.sounding_clicked.connect(self.sounding_clicked)
        self.bridge.obs_sounding_station_clicked.connect(self.obs_sounding_station_clicked)
        self.bridge.asos_bbox_selected.connect(self.asos_bbox_selected)
        self.bridge.asos_box_cancelled.connect(self.asos_box_cancelled)
        self.bridge.user_dragged.connect(self.user_dragged)
        self.bridge.satellite_idle.connect(self.satellite_idle)
        self.bridge.map_pick_for_route.connect(self.map_pick_for_route)

        # queue for JS calls that arrive before MapLibre has fully loaded.
        self._map_ready = False
        self._js_queue: list[str] = []
        self.bridge.map_loaded.connect(self._on_map_loaded_from_js)
        self.loadFinished.connect(self._on_page_load_finished)

        QTimer.singleShot(0, self._load_map)

    def shutdown(self) -> None:
        """Release storm:// on the default profile so the next MapWidget in
        this process can install its own (MainWindow.closeEvent)."""
        handler = getattr(self, "_scheme_handler", None)
        if SAFE_MAP_MODE or handler is None:
            return
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        QWebEngineProfile.defaultProfile().removeUrlSchemeHandler(handler)
        self._scheme_handler = None

    def javaScriptConsoleMessage(self, level, message, line, source):
        # emit all JS console messages to stdout for debugging (includes errors/warnings/info)
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

    def _on_page_load_finished(self, ok: bool):
        if not ok or self._map_ready:
            return
        QTimer.singleShot(8000, self._mark_map_ready_if_bridge_stalled)

    def _on_map_loaded_from_js(self):
        if self._map_ready:
            return
        self._map_ready = True
        for script in self._js_queue:
            self.map_page().runJavaScript(_no_result(script))
        self._js_queue.clear()
        self._start_perf_log_probe()
        self.map_ready.emit()

    # --perf-log only (core/perf_log.py): the GPU the map draws with, and
    # how smoothly the page renders -- frame times from requestAnimationFrame,
    # read every 5 s. The web view's renderer process is invisible to Python.
    _PERF_JS_START = """(function(){
      var c = document.createElement('canvas');
      var gl = c.getContext('webgl2') || c.getContext('webgl');
      var info = {webgl: gl ? (gl instanceof WebGL2RenderingContext ? 2 : 1) : 0};
      if (gl) {
        var d = gl.getExtension('WEBGL_debug_renderer_info');
        info.renderer = d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
        info.vendor = d ? gl.getParameter(d.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
        info.max_texture = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      }
      info.device_pixel_ratio = window.devicePixelRatio;
      if (!window._stormPerf) {
        window._stormPerf = {frames: 0, worst: 0, slow: 0, last: performance.now()};
        (function tick(now) {
          var p = window._stormPerf, dt = now - p.last;
          p.last = now; p.frames++; if (dt > p.worst) p.worst = dt; if (dt > 50) p.slow++;
          requestAnimationFrame(tick);
        })(performance.now());
      }
      return JSON.stringify(info);
    })()"""
    _PERF_JS_READ = """(function(){
      var p = window._stormPerf; if (!p) return null;
      var out = JSON.stringify({frames: p.frames, worst_ms: Math.round(p.worst), slow_frames: p.slow,
                                heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null});
      p.frames = 0; p.worst = 0; p.slow = 0; return out;
    })()"""

    def _start_perf_log_probe(self) -> None:
        from core import perf_log
        if not perf_log.enabled() or getattr(self, "_perf_timer", None) is not None:
            return

        def logged(kind):
            def callback(result):
                try:
                    if result:
                        perf_log.event(kind, **json.loads(result))
                except Exception:  # noqa: BLE001
                    pass
            return callback
        self.map_page().runJavaScript(self._PERF_JS_START, logged("gpu"))
        self._perf_timer = QTimer(self)
        self._perf_timer.setInterval(5000)
        self._perf_timer.timeout.connect(lambda: self.map_page().runJavaScript(self._PERF_JS_READ, logged("map.frames")))
        self._perf_timer.start()

    def _mark_map_ready_if_bridge_stalled(self):
        """Allow Python startup to continue if the JS map-ready callback stalls."""
        if self._map_ready:
            return
        log.warning("Map page loaded but JS map-ready callback did not fire; continuing startup")
        self._map_ready = True
        self._js_queue.clear()
        self._start_perf_log_probe()
        self.map_ready.emit()

    def map_page(self):
        """The map's page, also while the Video Studio has moved it into an
        off-screen view to render at export size (ui/studio/capture.py), when
        this view's own page() is a blank placeholder."""
        return getattr(self, "_map_page", None) or self.page()

    _BIG_JS_BYTES = 200_000     # --perf-log: log calls this large (see core/perf_log.py)

    @staticmethod
    def _log_big_js(script: str) -> None:
        from core import perf_log
        if perf_log.enabled():
            import re
            names = re.findall(r"storm[A-Z]\w+", script[:400])
            perf_log.event("map.big_js", kb=round(len(script) / 1024), call=names[0] if names else script[:60])

    def run_js(self, script: str):
        if SAFE_MAP_MODE:
            return
        if self._map_ready:
            if len(script) >= self._BIG_JS_BYTES:
                self._log_big_js(script)
            self.map_page().runJavaScript(_no_result(script))
        else:
            self._js_queue.append(script)

    def add_vehicle(self, vehicle_id: str, lat: float, lon: float,
                    color: str = ACCENT_COLOR, icon_type: str = "car",
                    hover_text: str | None = None, past: bool = False):
        """Add or move a vehicle marker; ``past`` draws it faded with a clock
        badge, for a position shown by the loop rather than the live one."""
        tooltip = hover_text or vehicle_id
        self.run_js(
            "stormAddVehicle("
            f"{json.dumps(vehicle_id)}, {lat}, {lon}, {json.dumps(color)}, "
            f"{json.dumps(icon_type)}, {json.dumps(tooltip)}, {'true' if past else 'false'});"
        )

    def remove_vehicle(self, vehicle_id: str):
        self.run_js(f"stormRemoveVehicle('{vehicle_id}');")

    def set_satellite_frame(self, mode: str, frame_id: str, png: bytes,
                            west: float, south: float, east: float, north: float):
        """Hand the PNG to the scheme handler and point the map at its URL."""
        self.scheme_handler.set_satellite_frame(mode, frame_id, png)
        url = f"storm://app/satellite/{mode}/{frame_id}.png"
        self.run_js(
            f"if(window.stormSetSatelliteFrame) "
            f"stormSetSatelliteFrame({json.dumps(url)},{west},{south},{east},{north});"
        )

    def set_satellite_time(self, time_iso: str):
        self.run_js(f"if(window.stormSetSatelliteTime) stormSetSatelliteTime('{time_iso}');")

    def set_satellite_visible(self, visible: bool):
        flag = "true" if visible else "false"
        self.run_js(f"if(window.stormSetSatelliteVisible) stormSetSatelliteVisible({flag});")

    def set_satellite_mode(self, mode: str):
        self.run_js(f"if(window.stormSetSatelliteMode) stormSetSatelliteMode('{mode}');")

    def set_radar_opacity(self, opacity: float) -> None:
        self.run_js(f"if(window.stormSetRadarOpacity) stormSetRadarOpacity({opacity:.3f});")

    def set_satellite_opacity(self, opacity: float):
        self.run_js(f"if(window.stormSetSatelliteOpacity) stormSetSatelliteOpacity({opacity:.3f});")

    def clear_satellite_frame(self) -> None:
        self.run_js("if(window.stormClearSatelliteFrame) stormClearSatelliteFrame();")

    def set_mesoanalysis_overlay(
        self,
        product_id: str,
        tile_url: str,
        source_layer: str,
        west: float,
        south: float,
        east: float,
        north: float,
        minzoom: int = 0,
        maxzoom: int = 8,
        label_units: str = "",
    ) -> None:
        self.run_js(
            "if(window.stormSetMesoanalysisOverlay) "
            f"stormSetMesoanalysisOverlay({json.dumps(product_id)},"
            f"{json.dumps(tile_url)},"
            f"{json.dumps(source_layer)},{west},{south},{east},{north},"
            f"{int(minzoom)},{int(maxzoom)},"
            f"{json.dumps(str(label_units or ''))});"
        )

    def set_sfcoa_overlay(
        self,
        product_id: str,
        tile_url: str,
        source_layer: str,
        west: float,
        south: float,
        east: float,
        north: float,
        minzoom: int = 0,
        maxzoom: int = 8,
        label_units: str = "",
    ) -> None:
        self.run_js(
            "if(window.stormSetSfcoaOverlay) "
            f"stormSetSfcoaOverlay({json.dumps(product_id)},"
            f"{json.dumps(tile_url)},"
            f"{json.dumps(source_layer)},{west},{south},{east},{north},"
            f"{int(minzoom)},{int(maxzoom)},"
            f"{json.dumps(str(label_units or ''))});"
        )

    def register_sfcoa_mbtiles(self, tile_key: str, mbtiles_path: str) -> None:
        handler = getattr(self, "_scheme_handler", None)
        if handler is not None and hasattr(handler, "set_sfcoa_mbtiles"):
            handler.set_sfcoa_mbtiles(tile_key, mbtiles_path)

    def set_sfcoa_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSfcoaVisible) "
            f"stormSetSfcoaVisible({'true' if visible else 'false'});"
        )

    def set_sfcoa_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetSfcoaOpacity) "
            f"stormSetSfcoaOpacity({opacity:.3f});"
        )

    def clear_sfcoa_overlay(self, product_id: str = "") -> None:
        self.run_js(
            f"if(window.stormClearSfcoaOverlay) "
            f"stormClearSfcoaOverlay({json.dumps(str(product_id or ''))});"
        )

    def set_mesoanalysis_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetMesoanalysisVisible) "
            f"stormSetMesoanalysisVisible({'true' if visible else 'false'});"
        )

    def set_mesoanalysis_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetMesoanalysisOpacity) "
            f"stormSetMesoanalysisOpacity({opacity:.3f});"
        )

    def clear_mesoanalysis_overlay(self, product_id: str = "") -> None:
        self.run_js(
            f"if(window.stormClearMesoanalysisOverlay) "
            f"stormClearMesoanalysisOverlay({json.dumps(str(product_id or ''))});"
        )

    def set_nlcd_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetNlcdVisible) "
            f"stormSetNlcdVisible({'true' if visible else 'false'});"
        )

    def set_nlcd_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetNlcdOpacity) "
            f"stormSetNlcdOpacity({opacity:.3f});"
        )

    def set_satellite_basemap_visible(self, visible: bool) -> None:
        self.run_js(
            f"if(window.stormSetSatelliteBasemapVisible) "
            f"stormSetSatelliteBasemapVisible({'true' if visible else 'false'});"
        )

    def set_satellite_basemap_opacity(self, opacity: float) -> None:
        self.run_js(
            f"if(window.stormSetSatelliteBasemapOpacity) "
            f"stormSetSatelliteBasemapOpacity({opacity:.3f});"
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

    def set_cwa_visible(self, visible: bool):
        """Toggle CWA overlay visibility; the map fetches static/cwa_boundaries.geojson on first show."""
        flag = "true" if visible else "false"
        self.run_js(
            f"if(window.stormSetCwaVisible) stormSetCwaVisible({flag});"
        )

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

    def set_private_pin_own_location(self, lat: float, lon: float):
        """Expose the local vehicle fix to local-only private pin tools."""
        self.run_js(
            f"if(window.stormSetPrivatePinOwnLocation) "
            f"stormSetPrivatePinOwnLocation({lat}, {lon});"
        )

    def add_private_pin(self, lat: float, lon: float):
        """Drop a private pin (kept only in this browser's storage) and open its menu."""
        self.run_js(f"if(window.stormAddPrivatePin) stormAddPrivatePin({lat}, {lon});")

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

    def set_annotations_visible(self, visible: bool) -> None:
        """Drawings (fronts, boundaries...), annotation markers and storm cones."""
        self.run_js(f"if(window.stormSetAnnotationsVisible) stormSetAnnotationsVisible({'true' if visible else 'false'});")

    def set_storm_cone_placement_mode(self, active: bool) -> None:
        self.run_js(f"if(window.stormSetStormConePlacementMode) stormSetStormConePlacementMode({'true' if active else 'false'});")

    def set_asos_bbox_mode(self, active: bool) -> None:
        """Toggle JS rectangle-selection mode for ASOS bbox selection."""
        flag = 'true' if active else 'false'
        self.run_js(f"if(window.stormSetAsosBoxMode) stormSetAsosBoxMode({flag});")

    def fit_bounds(self, west: float, south: float, east: float, north: float, padding: int = 40) -> None:
        """Fly the map to fit the given bounding box with optional padding (px)."""
        self.run_js(
            f"map.fitBounds([[{west},{south}],[{east},{north}]], {{padding:{padding}}});"
        )

    def add_station_plot(self, vehicle_id: str, lat: float, lon: float, png_bytes: bytes) -> None:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self.run_js(f"stormAddStationPlot('{vehicle_id}', {lat}, {lon}, '{b64}');")

    def move_station_plot(self, vehicle_id: str, lat: float, lon: float) -> None:
        self.run_js(f"stormMoveStationPlot('{vehicle_id}', {lat}, {lon});")

    def remove_station_plot(self, vehicle_id: str) -> None:
        self.run_js(f"stormRemoveStationPlot('{vehicle_id}');")

    def set_station_plots_visible(self, visible: bool) -> None:
        v = "true" if visible else "false"
        self.run_js(f"stormSetStationPlotsVisible({v});")

    def add_surface_station_plot(self, station_id: str, lat: float, lon: float, png_bytes: bytes, name: str = "") -> None:
        """Single-station add (used by SurfacePlotLayer directly)."""
        import json as _json
        self.scheme_handler.set_station_plots({station_id: png_bytes})
        self.run_js(
            f"stormAddSurfaceStationPlot({_json.dumps(station_id)}, {lat}, {lon}, {_json.dumps(name)});"
        )

    def add_surface_station_plots_batch(self, items: list) -> None:
        """Batch-add stations. items: list of (id, lat, lon, png_bytes, name).
        PNGs are stored in the scheme handler; JS payload contains only metadata."""
        import json as _json
        plots = {sid: png for sid, _, _, png, _ in items}
        self.scheme_handler.set_station_plots(plots)
        data = [{"id": sid, "lat": lat, "lon": lon, "name": name}
                for sid, lat, lon, _png, name in items]
        self.run_js(f"stormAddSurfaceStationPlotBatch({_json.dumps(data)});")

    def remove_surface_station_plot(self, station_id: str) -> None:
        self.scheme_handler.remove_station_plots([station_id])
        self.run_js(f"stormRemoveSurfaceStationPlot('{station_id}');")

    def remove_surface_station_plots_batch(self, station_ids) -> None:
        """Single JS call to remove many stations at once."""
        import json as _json
        ids = list(station_ids)
        self.scheme_handler.remove_station_plots(ids)
        self.run_js(f"stormRemoveSurfaceStationPlotBatch({_json.dumps(ids)});")

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

    def set_scan_sectors_geojson(self, geojson: dict) -> None:
        import json
        self.run_js(
            f"if(window.stormSetScanSectors) stormSetScanSectors({json.dumps(json.dumps(geojson))});"
        )

    def set_scan_sectors_visible(self, visible: bool) -> None:
        self.run_js(f"if(window.stormSetScanSectorsVisible) stormSetScanSectorsVisible({'true' if visible else 'false'});")

    # ── storm track (archive TRACK) ──
    def set_track_geojson(self, geojson: dict) -> None:
        self.run_js(f"if(window.stormSetTrackGeoJSON) stormSetTrackGeoJSON({json.dumps(json.dumps(geojson))});")

    def set_track_edit_mode(self, active: bool) -> None:
        self.run_js(f"if(window.stormSetTrackEditMode) stormSetTrackEditMode({'true' if active else 'false'});")

    def set_track_layers_visible(self, line: bool, points: bool) -> None:
        self.run_js(
            "if(window.stormSetTrackLayersVisible) stormSetTrackLayersVisible("
            f"{'true' if line else 'false'}, {'true' if points else 'false'});"
        )

    def set_track_marker(self, marker: dict | None) -> None:
        """The track's reference marker ({lat, lon, label}), or None to remove it."""
        arg = json.dumps(json.dumps(marker)) if marker else "null"
        self.run_js(f"if(window.stormSetTrackMarker) stormSetTrackMarker({arg});")

    # ── observation trails (archive VEHICLES > show trails) ──
    def set_trails(self, geojson: dict | None, stops: list | None = None, units: str = "",
                   label: str = "") -> None:
        """Observation trails (core/trails.py); None clears them. `stops` are
        the band colors (core.trails.color_stops), `label` and `units` name the
        quantity in the hover readout."""
        data = json.dumps(geojson or {"type": "FeatureCollection", "features": []})
        self.run_js(f"if(window.stormSetTrails) stormSetTrails({json.dumps(data)}, "
                    f"{json.dumps(stops or [])}, {json.dumps(units)}, {json.dumps(label)});")

    # ── Doppler lidar (archive LIDAR) ──
    def set_lidar_locations(self, locations: list[dict]) -> None:
        """Clickable lidar locations: dicts of key, lat, lon, label (under
        the dot), hover (lines) and selected; an empty list removes them."""
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [loc["lon"], loc["lat"]]},
            "properties": {"key": loc["key"], "label": loc.get("label", ""),
                           "hover": loc.get("hover", ""), "selected": bool(loc.get("selected"))},
        } for loc in locations]
        data = json.dumps({"type": "FeatureCollection", "features": features})
        self.run_js(f"if(window.stormSetLidarLocations) stormSetLidarLocations({json.dumps(data)});")

    def set_lidar_image(self, png: bytes, bounds) -> None:
        """One lidar sweep's PNG over [west, south, east, north]."""
        self._lidar_image_seq = getattr(self, "_lidar_image_seq", 0) + 1
        image_id = str(self._lidar_image_seq)
        self.scheme_handler.set_lidar_png(image_id, png)
        west, south, east, north = bounds
        url = f"storm://app/lidar/{image_id}.png"
        self.run_js(f"if(window.stormSetLidarImage) stormSetLidarImage({json.dumps(url)},"
                    f"{west},{south},{east},{north});")

    def clear_lidar_image(self) -> None:
        self.run_js("if(window.stormClearLidarImage) stormClearLidarImage();")

    def set_damage_paths(self, geojson: dict | None) -> None:
        """Damage-survey paths (archive/fetchers/damage_paths_archive_fetcher.py),
        colored by EF rating; None clears them."""
        data = json.dumps(geojson or {"type": "FeatureCollection", "features": []})
        self.run_js(f"if(window.stormSetDamagePaths) stormSetDamagePaths({json.dumps(data)});")

    def set_spc_geojson(
        self,
        cat_str: str,
        wind_str: str,
        hail_str: str,
        tor_str: str,
        prob_str: str = '{"type":"FeatureCollection","features":[]}',
        sig_str: str = '{"type":"FeatureCollection","features":[]}',
    ) -> None:
        import json
        self.run_js(
            "if(window.stormSetSpcGeoJSON) stormSetSpcGeoJSON("
            f"{json.dumps(cat_str)}, "
            f"{json.dumps(wind_str)}, "
            f"{json.dumps(hail_str)}, "
            f"{json.dumps(tor_str)}, "
            f"{json.dumps(prob_str)}, "
            f"{json.dumps(sig_str)}"
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
                f"(function(){{ var lid={json.dumps(layer_id)}; "
                "function go(){ try { if(map.isStyleLoaded && !map.isStyleLoaded()) { setTimeout(go, 100); return; } "
                "if(map.getLayer(lid)) map.moveLayer(lid); } catch(e) { setTimeout(go, 100); } } go(); })();"
            )
        else:
            self.run_js(
                f"(function(){{ var lid={json.dumps(layer_id)}, before={json.dumps(before_layer_id)}; "
                "function go(){ try { if(map.isStyleLoaded && !map.isStyleLoaded()) { setTimeout(go, 100); return; } "
                "if(map.getLayer(lid) && map.getLayer(before)) map.moveLayer(lid, before); } "
                "catch(e) { setTimeout(go, 100); } } go(); })();"
            )
