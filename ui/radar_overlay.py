# ui/radar_overlay.py
# renders a RadarScan onto the MapLibre map as a raster image overlay.
# converts the numpy data array to a PNG, encodes it as base64,
# and adds/updates an image source + raster layer in MapLibre GL JS.

import io
import base64
import logging
import numpy as np
from typing import Optional

from PyQt6.QtCore import QObject
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — no display needed
import matplotlib.cm as mcm
import matplotlib.colors as mcolors
import matplotlib.image as mimg
from scipy.ndimage import map_coordinates

from core.radar_scan import RadarScan

log = logging.getLogger(__name__)

# Output grid resolution for polar→Cartesian reprojection.
# Higher = sharper image, slower render.  Lower = faster, slightly blockier.
#   512 → ~80-150 ms render  (M1 Mac, modern laptop)
#   256 → ~20-40 ms render   (older/slower field laptop — recommended minimum)
# Override at launch with:  python main.py --render-grid-size 256
RENDER_GRID_SIZE = 512


def set_render_grid_size(n: int) -> None:
    """Override the render grid size at startup (called from main.py)."""
    global RENDER_GRID_SIZE
    RENDER_GRID_SIZE = max(64, min(1024, n))
    log.info("RENDER_GRID_SIZE set to %d", RENDER_GRID_SIZE)


# ── NWS Colormaps ─────────────────────────────────────────────────────────────

def _make_nws_ref_cmap():
    """classic NWS reflectivity colormap (ND → 75 dBZ)."""
    colors = [
        (0.00, (0.00, 0.00, 0.00, 0.00)),   # transparent / ND
        (0.10, (0.40, 0.40, 0.40, 0.60)),   # light gray
        (0.20, (0.00, 0.93, 0.93, 1.00)),   # light blue
        (0.30, (0.00, 0.00, 0.93, 1.00)),   # blue
        (0.40, (0.00, 1.00, 0.00, 1.00)),   # green
        (0.50, (0.00, 0.79, 0.00, 1.00)),   # dark green
        (0.57, (1.00, 1.00, 0.00, 1.00)),   # yellow
        (0.63, (1.00, 0.65, 0.00, 1.00)),   # orange
        (0.70, (1.00, 0.00, 0.00, 1.00)),   # red
        (0.77, (0.79, 0.00, 0.00, 1.00)),   # dark red
        (0.84, (1.00, 0.00, 1.00, 1.00)),   # magenta
        (0.91, (0.60, 0.33, 0.79, 1.00)),   # purple
        (1.00, (1.00, 1.00, 1.00, 1.00)),   # white (>75 dBZ)
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_ref",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)   # values below vmin are transparent
    return cmap


def _make_nws_vel_cmap():
    """NWS velocity colormap (inbound=green, outbound=red)."""
    colors = [
        (0.00, (0.00, 0.60, 0.00, 1.00)),   # dark green (strong inbound)
        (0.20, (0.00, 1.00, 0.00, 1.00)),   # green
        (0.40, (0.00, 1.00, 0.60, 1.00)),   # light green
        (0.48, (0.50, 0.50, 0.50, 0.40)),   # gray (near zero)
        (0.52, (0.50, 0.50, 0.50, 0.40)),   # gray (near zero)
        (0.60, (1.00, 0.80, 0.00, 1.00)),   # yellow
        (0.80, (1.00, 0.00, 0.00, 1.00)),   # red
        (1.00, (0.60, 0.00, 0.00, 1.00)),   # dark red (strong outbound)
    ]
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "nws_vel",
        [(pos, rgba) for pos, rgba in colors]
    )
    cmap.set_under(alpha=0)
    cmap.set_over(alpha=0)
    return cmap


NWS_REF_CMAP = _make_nws_ref_cmap()
NWS_VEL_CMAP = _make_nws_vel_cmap()

COLORMAPS = {
    "nws_ref": NWS_REF_CMAP,
    "nws_vel": NWS_VEL_CMAP,
}


# ── Radar Overlay ─────────────────────────────────────────────────────────────

class RadarOverlay(QObject):
    """
    Manages the radar image overlay on the MapLibre map.

    Works by:
    1. Converting RadarScan data → RGBA PNG via matplotlib
    2. Encoding PNG as base64
    3. Injecting into MapLibre as an image source with known lat/lon bounds
    4. Adding a raster layer that displays the image

    The overlay is updated in-place when new scans arrive.
    """

    LAYER_ID  = "radar-overlay"
    SOURCE_ID = "radar-image"

    def __init__(self, map_widget, parent=None):
        super().__init__(parent)
        self._map = map_widget
        self._active = False
        self._current_scan: Optional[RadarScan] = None
        # cache ScalarMappable objects keyed by (colormap, vmin, vmax)
        # avoids recreating norm+cmap on every render call
        self._mapper_cache: dict[tuple, mcm.ScalarMappable] = {}

    def update(self, scan: RadarScan):
        """render and display a new radar scan."""
        self._current_scan = scan

        try:
            png_b64, bounds = self._render_to_png(scan)
        except Exception as e:
            log.error("[RadarOverlay] render failed: %s", e, exc_info=True)
            return

        self._inject_into_map(png_b64, bounds)
        self._active = True
        log.info("[RadarOverlay] updated with %s", scan.label)

    def clear(self):
        """remove the radar overlay from the map."""
        self._map.run_js(f"""
          if (map.getLayer("{self.LAYER_ID}")) map.removeLayer("{self.LAYER_ID}");
          if (map.getSource("{self.SOURCE_ID}")) map.removeSource("{self.SOURCE_ID}");
        """)
        self._active = False
        self._current_scan = None

    def set_opacity(self, opacity: float):
        """set overlay opacity (0.0 – 1.0)."""
        if self._active:
            self._map.run_js(
                f'map.setPaintProperty("{self.LAYER_ID}", "raster-opacity", {opacity:.2f});'
            )

    @property
    def is_active(self) -> bool:
        return self._active

    # ── Internal ──────────────────────────────────────────────────────────────

    def _render_to_png(self, scan: RadarScan) -> tuple[str, list]:
        """
        Convert scan data to a base64-encoded PNG using proper polar→Cartesian
        reprojection so the circular sweep looks correct on the map.

        Returns:
            (base64_png_string, [west, south, east, north])
        """
        IMG = RENDER_GRID_SIZE   # configurable — lower = faster render

        log.debug(
            "rendering %s — grid=%dx%d, colormap=%s, vmin=%.1f vmax=%.1f",
            scan.label, IMG, IMG, scan.colormap, scan.vmin, scan.vmax
        )

        num_az, num_rng = scan.data.shape

        # radar center is at range=0 for all azimuths
        radar_lat = float(scan.lats[:, 0].mean())
        radar_lon = float(scan.lons[:, 0].mean())

        # geographic bounding box
        lat_min = float(np.nanmin(scan.lats))
        lat_max = float(np.nanmax(scan.lats))
        lon_min = float(np.nanmin(scan.lons))
        lon_max = float(np.nanmax(scan.lons))

        # build a square output grid in lat/lon space (north→south, west→east)
        out_lats = np.linspace(lat_max, lat_min, IMG)
        out_lons = np.linspace(lon_min, lon_max, IMG)
        lon_grid, lat_grid = np.meshgrid(out_lons, out_lats)

        # for each output pixel compute azimuth and range from radar center
        cos_lat = np.cos(np.deg2rad(radar_lat))
        dlat = lat_grid - radar_lat
        dlon = (lon_grid - radar_lon) * cos_lat
        range_km = np.sqrt(dlat ** 2 + dlon ** 2) * 111.32

        # azimuth: 0° = North, clockwise (radar convention)
        az_deg = np.degrees(np.arctan2(dlon, dlat)) % 360.0

        # max range from outermost gate lat/lon
        max_range_km = float(np.nanmax(
            np.sqrt(((scan.lats - radar_lat) ** 2 +
                     ((scan.lons - radar_lon) * cos_lat) ** 2)) * 111.32
        ))

        # convert to fractional array indices
        # az_offset accounts for the fact that row 0 of the data is not necessarily North
        az_idx = ((az_deg - scan.az_offset) % 360.0) * num_az / 360.0
        rng_idx = range_km / max_range_km * (num_rng - 1)

        outside = range_km > max_range_km

        # fill NaN with a sentinel before interpolation
        # order=0 (nearest-neighbor) is used because order=1 (bilinear) bleeds
        # sentinel values into valid data at the radar circle edge (~92% of pixels are no-data)
        sentinel = scan.vmin - 999.0
        data_filled = np.where(np.isnan(scan.data), sentinel, scan.data)

        coords = np.array([az_idx.ravel(), rng_idx.ravel()])
        data_out = map_coordinates(
            data_filled, coords, order=0, prefilter=False, mode="constant", cval=sentinel
        ).reshape(IMG, IMG)

        # mask outside and sentinel pixels
        data_out[outside | (data_out <= sentinel + 1.0)] = np.nan
        # for reflectivity only: mask sub-threshold pixels (~8 dBZ matches RadarScope)
        # do NOT apply to velocity — velocity values include negatives (-100 to +100 kt)
        if scan.colormap != "nws_vel":
            data_out[~np.isnan(data_out) & (data_out < 8.0)] = np.nan

        # reuse cached ScalarMappable — recreating norm+cmap every frame is wasteful
        cache_key = (scan.colormap, scan.vmin, scan.vmax)
        if cache_key not in self._mapper_cache:
            cmap = COLORMAPS.get(scan.colormap, NWS_REF_CMAP)
            norm = mcolors.Normalize(vmin=scan.vmin, vmax=scan.vmax, clip=False)
            self._mapper_cache[cache_key] = mcm.ScalarMappable(norm=norm, cmap=cmap)
            log.debug("created new ScalarMappable for key %s", cache_key)
        mapper = self._mapper_cache[cache_key]

        rgba = mapper.to_rgba(data_out, bytes=True)   # (IMG, IMG, 4) uint8
        rgba[np.isnan(data_out), 3] = 0               # transparent for no-data pixels

        # encode as PNG
        buf = io.BytesIO()
        mimg.imsave(buf, rgba, format="png")
        png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        log.debug("render complete: %.0f KB PNG", len(png_b64) * 3 / 4 / 1024)

        bounds = [lon_min, lat_min, lon_max, lat_max]
        return png_b64, bounds

    def _inject_into_map(self, png_b64: str, bounds: list):
        """
        Add or update the radar image source and layer in MapLibre.

        MapLibre image sources expect coordinates as:
          [[NW_lon, NW_lat], [NE_lon, NE_lat], [SE_lon, SE_lat], [SW_lon, SW_lat]]
        """
        west, south, east, north = bounds

        coords_js = (
            f"[[{west},{north}], [{east},{north}], [{east},{south}], [{west},{south}]]"
        )
        data_url = f"data:image/png;base64,{png_b64}"

        log.debug("injecting radar image into map (bounds %s)", bounds)

        js = f"""
        (function() {{
          const imageUrl = "{data_url}";
          const coords   = {coords_js};

          try {{
            if (map.getSource("{self.SOURCE_ID}")) {{
              // update existing source in-place — avoids layer flicker
              map.getSource("{self.SOURCE_ID}").updateImage({{
                url: imageUrl,
                coordinates: coords
              }});
            }} else {{
              // first time — add source and layer
              map.addSource("{self.SOURCE_ID}", {{
                type: "image",
                url: imageUrl,
                coordinates: coords
              }});

              try {{
                map.addLayer({{
                  id: "{self.LAYER_ID}",
                  type: "raster",
                  source: "{self.SOURCE_ID}",
                  paint: {{
                    "raster-opacity": 0.75,
                    "raster-fade-duration": 300
                  }}
                }}, "road-unpaved");   // insert below road labels/roads so they stay visible
              }} catch(layerErr) {{
                // fallback: "road-unpaved" may not exist yet — add without beforeId
                console.warn("[STORM] radar addLayer beforeId failed, adding on top:", layerErr.message);
                map.addLayer({{
                  id: "{self.LAYER_ID}",
                  type: "raster",
                  source: "{self.SOURCE_ID}",
                  paint: {{
                    "raster-opacity": 0.75,
                    "raster-fade-duration": 300
                  }}
                }});
              }}
            }}
          }} catch(e) {{
            console.error("[STORM] radar inject error:", e.message || e);
          }}
        }})();
        """
        self._map.run_js(js)
