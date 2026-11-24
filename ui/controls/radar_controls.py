
import threading
import xml.etree.ElementTree as ET
from urllib.request import urlopen

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QComboBox, QToolButton, QLabel,
    QCheckBox, QSlider, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal, Qt
from ui import palette as P
from ui.widgets.drawer import Drawer
from ui.layer_status import StatusTimeLabel
from data import thredds_paths as paths
from data.fetchers.radar_fetcher import normalize_site, thredds_site_token
from core.qt_signals import emit_alive


# listed, but never picked as the nearest site: the Radar Operations
# Center's test radar in Norman, whose feed isn't a normal site's
NOT_NEAREST_SITES = frozenset({"KCRI"})

# every WSR-88D site in the US and its territories (NCEI's NEXRAD station
# list): (ICAO id, name, lat, lon). Live Level 3 comes from Unidata and
# ARCHIVE's Level 2 from the AWS bucket, which carry all of them.
NEXRAD_SITES = [
    ("KABR", "Aberdeen, SD", 45.456, -98.413),
    ("KABX", "Albuquerque, NM", 35.150, -106.824),
    ("KAKQ", "Norfolk Rich, VA", 36.984, -77.007),
    ("KAMA", "Amarillo, TX", 35.233, -101.709),
    ("KAMX", "Miami, FL", 25.611, -80.413),
    ("KAPX", "Gaylord, MI", 44.906, -84.720),
    ("KARX", "La Crosse, WI", 43.823, -91.191),
    ("KATX", "Seattle, WA", 48.195, -122.496),
    ("KBBX", "Beale AFB, CA", 39.496, -121.632),
    ("KBGM", "Binghamton, NY", 42.200, -75.985),
    ("KBHX", "Eureka, CA", 40.499, -124.292),
    ("KBIS", "Bismarck, ND", 46.771, -100.761),
    ("KBLX", "Billings, MT", 45.854, -108.607),
    ("KBMX", "Birmingham, AL", 33.172, -86.770),
    ("KBOX", "Boston, MA", 41.956, -71.137),
    ("KBRO", "Brownsville, TX", 25.916, -97.419),
    ("KBUF", "Buffalo, NY", 42.949, -78.737),
    ("KBYX", "Key West, FL", 24.598, -81.703),
    ("KCAE", "Columbia, SC", 33.949, -81.118),
    ("KCBW", "Houlton, ME", 46.039, -67.806),
    ("KCBX", "Boise, ID", 43.490, -116.236),
    ("KCCX", "State College, PA", 40.923, -78.004),
    ("KCLE", "Cleveland, OH", 41.413, -81.860),
    ("KCLX", "Charleston, SC", 32.656, -81.042),
    ("KCRI", "Roc Faa Redundant Rda 1, OK", 35.238, -97.460),
    ("KCRP", "Corpus Christi, TX", 27.784, -97.511),
    ("KCXX", "Burlington, VT", 44.511, -73.166),
    ("KCYS", "Cheyenne, WY", 41.152, -104.806),
    ("KDAX", "Sacramento, CA", 38.501, -121.678),
    ("KDDC", "Dodge City, KS", 37.761, -99.969),
    ("KDFX", "Laughlin AFB, TX", 29.273, -100.280),
    ("KDGX", "Jackson/Central MS", 32.280, -89.984),
    ("KDIX", "Philadelphia, NJ", 39.947, -74.411),
    ("KDLH", "Duluth, MN", 46.837, -92.210),
    ("KDMX", "Des Moines, IA", 41.731, -93.723),
    ("KDOX", "Dover AFB, DE", 38.826, -75.440),
    ("KDTX", "Detroit, MI", 42.700, -83.472),
    ("KDVN", "Davenport, IA", 41.612, -90.581),
    ("KDYX", "Dyess AFB, TX", 32.538, -99.254),
    ("KEAX", "Kansas City, MO", 38.810, -94.264),
    ("KEMX", "Tucson, AZ", 31.894, -110.630),
    ("KENX", "Albany, NY", 42.587, -74.064),
    ("KEOX", "Fort Rucker, AL", 31.461, -85.459),
    ("KEPZ", "El Paso, NM", 31.873, -106.698),
    ("KESX", "Las Vegas, NV", 35.701, -114.892),
    ("KEVX", "Eglin AFB, FL", 30.565, -85.922),
    ("KEWX", "Austin San Antonio, TX", 29.704, -98.029),
    ("KEYX", "Edwards, CA", 35.098, -117.561),
    ("KFCX", "Roanoke, VA", 37.024, -80.274),
    ("KFDR", "Frederick, OK", 34.362, -98.977),
    ("KFDX", "Clovis/E NM", 34.634, -103.619),
    ("KFFC", "Atlanta, GA", 33.364, -84.566),
    ("KFSD", "Sioux Falls, SD", 43.588, -96.729),
    ("KFSX", "Flagstaff, AZ", 34.574, -111.198),
    ("KFTG", "Denver, CO", 39.787, -104.546),
    ("KFWS", "Dallas/Ft. Worth, TX", 32.573, -97.303),
    ("KGGW", "Glasgow, MT", 48.206, -106.625),
    ("KGJX", "Grand Junction, CO", 39.062, -108.214),
    ("KGLD", "Goodland, KS", 39.367, -101.700),
    ("KGRB", "Green Bay, WI", 44.499, -88.111),
    ("KGRK", "Fort Hood, TX", 30.722, -97.383),
    ("KGRR", "Grand Rapids, MI", 42.894, -85.545),
    ("KGSP", "Greer, SC", 34.883, -82.220),
    ("KGWX", "Columbus/NE MS", 33.897, -88.329),
    ("KGYX", "Portland, ME", 43.891, -70.256),
    ("KHDC", "Hammond Municipal Airport, LA", 30.519, -90.407),
    ("KHDX", "Holloman AFB, NM", 33.077, -106.120),
    ("KHGX", "Houston, TX", 29.472, -95.079),
    ("KHNX", "San Joaquin Valley, CA", 36.314, -119.632),
    ("KHPX", "Fort Campbell, KY", 36.737, -87.286),
    ("KHTX", "Huntsville/N AL", 34.931, -86.084),
    ("KICT", "Wichita, KS", 37.654, -97.443),
    ("KICX", "Cedar City, UT", 37.591, -112.862),
    ("KILN", "Cincinnati, OH", 39.420, -83.821),
    ("KILX", "Lincoln/Central IL", 40.151, -89.337),
    ("KIND", "Indianapolis, IN", 39.708, -86.280),
    ("KINX", "Tulsa, OK", 36.175, -95.564),
    ("KIWA", "Phoenix, AZ", 33.289, -111.670),
    ("KIWX", "Northern Indiana", 41.359, -85.700),
    ("KJAX", "Jacksonville, FL", 30.485, -81.702),
    ("KJGX", "Robins AFB, GA", 32.676, -83.351),
    ("KJKL", "Jackson, KY", 37.591, -83.313),
    ("KLBB", "Lubbock, TX", 33.654, -101.814),
    ("KLCH", "Lake Charles, LA", 30.125, -93.216),
    ("KLGX", "Langley Hill Nw Washington, WA", 47.117, -124.107),
    ("KLIX", "New Orleans, LA", 30.337, -89.825),
    ("KLNX", "North Platte, NE", 41.958, -100.576),
    ("KLOT", "Chicago/NE IL", 41.604, -88.084),
    ("KLRX", "Elko, NV", 40.740, -116.803),
    ("KLSX", "St. Louis, MO", 38.699, -90.683),
    ("KLTX", "Wilmington, NC", 33.989, -78.429),
    ("KLVX", "Louisville, KY", 37.975, -85.944),
    ("KLWX", "Sterling, VA", 38.976, -77.487),
    ("KLZK", "Little Rock, AR", 34.837, -92.262),
    ("KMAF", "Midland, TX", 31.943, -102.189),
    ("KMAX", "Medford, OR", 42.081, -122.717),
    ("KMBX", "Minot, ND", 48.393, -100.864),
    ("KMHX", "Morehead City, NC", 34.776, -76.876),
    ("KMKX", "Milwaukee, WI", 42.968, -88.551),
    ("KMLB", "Melbourne, FL", 28.113, -80.654),
    ("KMOB", "Mobile, AL", 30.679, -88.240),
    ("KMPX", "Minneapolis-St. Paul, MN", 44.849, -93.566),
    ("KMQT", "Marquette, MI", 46.531, -87.548),
    ("KMRX", "Knoxville/E TN", 36.169, -83.402),
    ("KMSX", "Missoula, MT", 47.041, -113.986),
    ("KMTX", "Salt Lake City, UT", 41.263, -112.448),
    ("KMUX", "San Francisco, CA", 37.155, -121.898),
    ("KMVX", "Grand Forks, ND", 47.528, -97.326),
    ("KMXX", "Maxwell AFB, AL", 32.537, -85.790),
    ("KNKX", "San Diego, CA", 32.919, -117.042),
    ("KNQA", "Memphis, TN", 35.345, -89.873),
    ("KOAX", "Omaha, NE", 41.320, -96.367),
    ("KOHX", "Nashville, TN", 36.247, -86.562),
    ("KOKX", "New York City, NY", 40.866, -72.864),
    ("KOTX", "Spokane, WA", 47.680, -117.627),
    ("KPAH", "Paducah, KY", 37.068, -88.772),
    ("KPBZ", "Pittsburgh, PA", 40.532, -80.218),
    ("KPDT", "Pendleton, OR", 45.691, -118.853),
    ("KPOE", "Fort Polk, LA", 31.155, -92.976),
    ("KPUX", "Pueblo, CO", 38.460, -104.181),
    ("KRAX", "Raleigh Durham, NC", 35.666, -78.490),
    ("KRGX", "Reno, NV", 39.754, -119.462),
    ("KRIW", "Riverton, WY", 43.066, -108.477),
    ("KRLX", "Charleston, WV", 38.311, -81.723),
    ("KRTX", "Portland, OR", 45.715, -122.965),
    ("KSFX", "Pocatello, ID", 43.106, -112.686),
    ("KSGF", "Springfield, MO", 37.235, -93.400),
    ("KSHV", "Shreveport, LA", 32.451, -93.841),
    ("KSJT", "San Angelo, TX", 31.371, -100.493),
    ("KSOX", "Santa Ana Mountains, CA", 33.818, -117.636),
    ("KSRX", "Ft. Smith, AR", 35.290, -94.362),
    ("KTBW", "Tampa, FL", 27.706, -82.402),
    ("KTFX", "Great Falls, MT", 47.460, -111.385),
    ("KTLH", "Tallahassee, FL", 30.398, -84.329),
    ("KTLX", "Oklahoma City, OK", 35.333, -97.278),
    ("KTWX", "Topeka, KS", 38.997, -96.233),
    ("KTYX", "Fort Drum, NY", 43.756, -75.680),
    ("KUDX", "Rapid City, SD", 44.125, -102.830),
    ("KUEX", "Grand Island, NE", 40.321, -98.442),
    ("KVAX", "Moody AFB, GA", 30.890, -83.002),
    ("KVBX", "Vandenberg AFB, CA", 34.839, -120.398),
    ("KVNX", "Enid/Vance, OK", 36.741, -98.128),
    ("KVTX", "Los Angeles, CA", 34.412, -119.179),
    ("KVWX", "Evansville, IN", 38.260, -87.725),
    ("KYUX", "Yuma, AZ", 32.495, -114.657),
    ("PABC", "Bethel Faa, AK", 60.792, -161.876),
    ("PACG", "Sitka, AK", 56.853, -135.529),
    ("PAEC", "Nome, AK", 64.511, -165.295),
    ("PAHG", "Anchorage, AK", 60.726, -151.351),
    ("PAIH", "Middleton Island, AK", 59.461, -146.303),
    ("PAKC", "King Salmon, AK", 58.679, -156.629),
    ("PAPD", "Fairbanks, AK", 65.035, -147.501),
    ("PGUA", "Andersen AFB Agana, GU", 13.456, 144.811),
    ("PHKI", "South Kauai, HI", 21.894, -159.553),
    ("PHKM", "Kamuela, HI", 20.125, -155.778),
    ("PHMO", "Molokai, HI", 21.133, -157.180),
    ("PHWA", "South Shore, HI", 19.095, -155.569),
    ("TJUA", "San Juan, PR", 18.116, -66.078),
]

PRODUCTS = [("N0B", "REFLECTIVITY (SR)"), ("N0U", "VELOCITY")]
OPTIONAL_PRODUCTS = [("N0C", "CORR COEFF"), ("N0K", "SPEC DIFF PHASE")]
ALL_PRODUCTS = PRODUCTS + OPTIONAL_PRODUCTS
THREDDS_CATALOG_ROOT = f"{paths.catalog_root(paths.UCAR)}/{paths.UCAR_NEXRAD_LEVEL3}"
class RadarControls(Drawer):
    """
    Toolbar widget containing:
      - RADAR toggle button — slides the two-row control drawer in/out
      - collapsible two-row drawer:
          Row 1: stations button | product selector | show data checkbox | stretch
          Row 2: ⏮ ⏪ ▶/⏸ ⏩ ⏭  +  expanding timeline slider  +  time label

    Signals:
        radar_toggled(bool)    — data fetch enabled/disabled (from show data checkbox)
        site_changed(str)      — new site ID selected
        stations_requested()   — toggle/open the map station picker overlay
        product_changed(str)   — selected product code (e.g. "N0Q")
        fetch_requested()      — trigger immediate fetch
        frame_requested(int)   — user selected a specific cache frame index
        loop_toggled(bool)     — loop playback started/stopped
        sync_vehicles_toggled(bool) — vehicles follow the loop
    """

    radar_toggled   = pyqtSignal(bool)
    site_changed    = pyqtSignal(str)
    stations_requested = pyqtSignal()
    product_changed = pyqtSignal(str)
    tilt_changed    = pyqtSignal(int)
    fetch_requested = pyqtSignal()
    frame_requested = pyqtSignal(int)
    loop_toggled    = pyqtSignal(bool)
    speed_changed   = pyqtSignal(int)      # new interval in ms
    vad_requested   = pyqtSignal()         # VAD hodograph dialog requested
    opacity_changed = pyqtSignal(float)    # 0.1–1.0
    sync_vehicles_toggled = pyqtSignal(bool)
    dealias_toggled = pyqtSignal(bool)     # archive velocity only
    archive_data_toggled = pyqtSignal(bool)  # archive: fetch and draw the radar, or not

    # internal — emitted from the _refresh_product_availability background thread
    _products_refreshed = pyqtSignal(dict)  # {code: bool} availability map
    products_available_changed = pyqtSignal(list)
    product_availability_changed = pyqtSignal(dict)  # {code: bool} for current site

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radar_on           = False
        self._product            = "N0B"
        self._archive_mode       = False
        self._vad_archive_tip    = "Lidar wind profiles"
        self._product_availability: dict[tuple[str, str], bool] = {}
        self._site               = "KTLX"
        self._all_sites          = list(NEXRAD_SITES)
        self._setup_ui()
        self._products_refreshed.connect(self._apply_product_items)
        self.set_selected_site("KTLX")

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        # starts collapsed — height animates open as a dropdown pill below toolbar.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._drawer = QWidget()
        self._drawer.setObjectName("radarDrawer")
        drawer_layout = QVBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(4)

        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        self._stations_button = QToolButton()
        self._stations_button.setFixedHeight(22)
        self._stations_button.setMinimumWidth(140)
        self._stations_button.setObjectName("radarStationsButton")
        self._stations_button.setToolTip("Show radar station picker on the map")
        self._stations_button.clicked.connect(self.stations_requested.emit)
        r1.addWidget(self._stations_button)

        self._product_combo = QComboBox()
        self._product_combo.setFixedHeight(22)
        self._product_combo.setMinimumWidth(128)
        self._product_combo.setObjectName("radarProductCombo")
        # start with all 4 products; optional ones disabled until THREDDS probe confirms
        self._set_product_items(ALL_PRODUCTS, preserve_code=self._product)
        self._apply_availability_to_combo({"N0B": True, "N0U": True, "N0C": False, "N0K": False})
        self._product_combo.currentIndexChanged.connect(self._on_product_changed)
        r1.addWidget(self._product_combo)

        self._tilt_combo = QComboBox()
        self._tilt_combo.setFixedHeight(22)
        self._tilt_combo.setFixedWidth(74)
        self._tilt_combo.setObjectName("radarTiltCombo")
        self._tilt_combo.setToolTip("Radar tilt angle")
        self._tilt_combo.currentIndexChanged.connect(self._on_tilt_changed)
        self._tilt_combo.setVisible(False)
        # each sweep's angle in volume order (set_archive_tilts)
        self._sweep_angles: list[float] = []
        r1.addWidget(self._tilt_combo)

        # archive WSR-88D velocity only (_refresh_dealias_visible)
        self._btn_dealias = QToolButton()
        self._btn_dealias.setText("DEALIAS")
        self._btn_dealias.setCheckable(True)
        self._btn_dealias.setFixedHeight(22)
        self._btn_dealias.setObjectName("radarDealiasButton")
        self._btn_dealias.setToolTip(
            "Unfold aliased velocities (region-based). A sweep that can't be "
            "dealiased is shown raw, and the archive bar says so"
        )
        self._btn_dealias.toggled.connect(self.dealias_toggled)
        self._btn_dealias.setVisible(False)
        r1.addWidget(self._btn_dealias)

        # checkbox sits immediately right of product selector
        self._chk_show_data = QCheckBox("show data")
        self._chk_show_data.setChecked(False)
        self._chk_show_data.setFixedHeight(22)
        self._chk_show_data.setToolTip("enable or disable radar data fetch and display")
        self._chk_show_data.toggled.connect(self._on_data_enabled_toggled)
        r1.addWidget(self._chk_show_data)

        self._btn_vad = QToolButton()
        self._btn_vad.setText("VAD")
        self._btn_vad.setFixedHeight(22)
        self._btn_vad.setFixedWidth(48)
        self._btn_vad.setObjectName("radarVadButton")
        self._btn_vad.setToolTip("View VAD wind profile hodograph")
        self._btn_vad.clicked.connect(self.vad_requested.emit)
        r1.addWidget(self._btn_vad)

        opacity_label = QLabel("OPACITY")
        opacity_label.setStyleSheet(
            f"color: {P.TEXT_SECONDARY}; font-size: 11px; letter-spacing: 0.5px;"
        )
        opacity_label.setFixedHeight(22)
        r1.addWidget(opacity_label)

        # floor at 10 %: a 0 opacity layer reads as hidden to the map
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(10, 100)
        self._opacity_slider.setValue(75)
        self._opacity_slider.setFixedHeight(22)
        self._opacity_slider.setFixedWidth(90)
        self._opacity_slider.setToolTip("Radar opacity")
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        r1.addWidget(self._opacity_slider)

        r1.addStretch()

        drawer_layout.addWidget(row1)

        row2 = QWidget()
        row2.setObjectName("radarPlaybackRow")
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(2)

        self._btn_jump_start = QToolButton()
        self._btn_jump_start.setText("⏮")
        self._btn_jump_start.setFixedSize(32, 26)
        self._btn_jump_start.setEnabled(False)
        self._btn_jump_start.setToolTip("Oldest frame")
        self._btn_jump_start.clicked.connect(self._on_jump_start)
        r2.addWidget(self._btn_jump_start)

        self._btn_back = QToolButton()
        self._btn_back.setText("⏪")
        self._btn_back.setFixedSize(32, 26)
        self._btn_back.setEnabled(False)
        self._btn_back.setToolTip("Step back one frame")
        self._btn_back.clicked.connect(self._on_step_back)
        r2.addWidget(self._btn_back)

        self._btn_play = QToolButton()
        self._btn_play.setText("▶")
        self._btn_play.setCheckable(True)
        self._btn_play.setFixedSize(32, 26)
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip("Play / Pause loop")
        self._btn_play.toggled.connect(self._on_play_toggled)
        r2.addWidget(self._btn_play)

        self._btn_fwd = QToolButton()
        self._btn_fwd.setText("⏩")
        self._btn_fwd.setFixedSize(32, 26)
        self._btn_fwd.setEnabled(False)
        self._btn_fwd.setToolTip("Step forward one frame")
        self._btn_fwd.clicked.connect(self._on_step_forward)
        r2.addWidget(self._btn_fwd)

        self._btn_jump_end = QToolButton()
        self._btn_jump_end.setText("⏭")
        self._btn_jump_end.setFixedSize(32, 26)
        self._btn_jump_end.setEnabled(False)
        self._btn_jump_end.setToolTip("Latest (live)")
        self._btn_jump_end.clicked.connect(self._on_jump_end)
        r2.addWidget(self._btn_jump_end)

        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.setFixedHeight(26)
        self._frame_slider.setMinimumWidth(80)   # expand to fill available space
        self._frame_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._frame_slider.setEnabled(False)
        # use sliderReleased instead of valueChanged — avoids re-render on every drag pixel
        self._frame_slider.sliderReleased.connect(self._on_slider_released)
        r2.addWidget(self._frame_slider)

        # time label sits right of slider so it reads: [slider] [23:45Z]
        self._frame_time_label = StatusTimeLabel()
        self._frame_time_label.setObjectName("radarTimeLabel")
        self._frame_time_label.setFixedHeight(26)
        self._frame_time_label.setMinimumWidth(52)
        r2.addWidget(self._frame_time_label)

        # speed selector — 0.5×, 1×, 2×, 3×
        self._speed_combo = QComboBox()
        self._speed_combo.setFixedHeight(26)
        self._speed_combo.setMaximumWidth(5)
        self._speed_combo.setToolTip("Playback speed")
        for label, ms in [("0.5×", 1000), ("1×", 500), ("2×", 250), ("3×", 167)]:
            self._speed_combo.addItem(label, userData=ms)
        self._speed_combo.setCurrentIndex(1)   # default 1×
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        r2.addWidget(self._speed_combo)

        # vehicle markers (VEH) follow the loop. Never shown: it holds the state
        # (saved with the session) and the settings pill (gear) shows and changes it.
        self._btn_sync_vehicles = QToolButton()
        self._btn_sync_vehicles.setObjectName("radarSyncVehiclesBtn")
        self._btn_sync_vehicles.setText("VEH")
        self._btn_sync_vehicles.setCheckable(True)
        self._btn_sync_vehicles.setFixedHeight(26)
        self._btn_sync_vehicles.setToolTip("Vehicles follow the loop, shown where they were")
        self._btn_sync_vehicles.toggled.connect(self.sync_vehicles_toggled)
        self._btn_sync_vehicles.hide()

        drawer_layout.addWidget(row2)
        self._playback_row = row2

        layout.addWidget(self._drawer)



    def current_site(self) -> str:
        return self._site

    def current_product(self) -> str:
        return self._product

    def set_selected_site(self, site_id: str, emit: bool = False):
        normalized = normalize_site(site_id) or "KTLX"
        self._site = normalized
        self._stations_button.setText(f"Stations: {normalized}")
        # fetcher always fetches all 4; availability only drives combo disabled state
        self.products_available_changed.emit([code for code, _ in ALL_PRODUCTS])
        # apply any cached availability immediately so combo disables stale unavailable items
        cached = {
            code: self._product_availability.get((normalized, code), True)
            for code, _ in ALL_PRODUCTS
        }
        # n0B/N0U are always assumed available (never probed)
        cached["N0B"] = True
        cached["N0U"] = True
        self._apply_availability_to_combo(cached)
        self.product_availability_changed.emit(dict(cached))
        _site = normalized
        threading.Thread(
            target=self._refresh_product_availability,
            args=(_site,),
            daemon=True,
        ).start()
        if emit:
            self.site_changed.emit(normalized)
            if self.is_data_enabled():
                self.fetch_requested.emit()

    def set_scan_time(self, time_str: str, when=None):
        # update the time label next to the slider; ``when`` adds the age
        self._frame_time_label.set_frame(time_str, when)

    def bind_status(self, status) -> None:
        """Color the time label and show data age from a LayerStatus."""
        self._frame_time_label.bind(status)

    def is_data_enabled(self) -> bool:
        """Return whether radar data fetch/display is enabled."""
        return self._chk_show_data.isChecked()

    def opacity(self) -> float:
        return self._opacity_slider.value() / 100.0

    def is_sync_vehicles(self) -> bool:
        return self._btn_sync_vehicles.isChecked()

    def is_dealias(self) -> bool:
        return self._btn_dealias.isChecked()

    def set_dealias(self, enabled: bool) -> None:
        self._btn_dealias.setChecked(enabled)

    def _refresh_dealias_visible(self) -> None:
        btn = getattr(self, "_btn_dealias", None)   # the product list is built first
        visible = self._archive_mode and self._product == "velocity"
        if btn is not None and btn.isHidden() == visible:
            btn.setVisible(visible)
            self.content_changed()

    def save_state(self) -> dict:
        return {"data": self.is_data_enabled(), "product": self._product,
                "opacity": self._opacity_slider.value(),
                "sync_vehicles": self._btn_sync_vehicles.isChecked(),
                "dealias": self._btn_dealias.isChecked()}

    def restore_state(self, state: dict) -> None:
        """Re-select the product and opacity, then turn data on; all go through
        the normal signals."""
        opacity = state.get("opacity")
        if isinstance(opacity, int):
            self._opacity_slider.setValue(max(10, min(100, opacity)))
        if isinstance(state.get("sync_vehicles"), bool):
            self._btn_sync_vehicles.setChecked(state["sync_vehicles"])
        if isinstance(state.get("dealias"), bool):
            self._btn_dealias.setChecked(state["dealias"])
        idx = self._product_combo.findData(state.get("product"))
        if idx >= 0 and idx != self._product_combo.currentIndex():
            self._product_combo.setCurrentIndex(idx)
        if state.get("data") and not self.is_data_enabled():
            self._chk_show_data.setChecked(True)

    def _set_product_items(self, items: list[tuple[str, str]], preserve_code: str | None = None):
        self._product_combo.blockSignals(True)
        self._product_combo.clear()
        for code, label in items:
            self._product_combo.addItem(label, userData=code)
        if preserve_code:
            idx = self._product_combo.findData(preserve_code)
            if idx >= 0:
                self._product_combo.setCurrentIndex(idx)
            else:
                self._product_combo.setCurrentIndex(0)
        else:
            self._product_combo.setCurrentIndex(0)
        self._product_combo.blockSignals(False)
        # sync internal product state to the combo selection
        current_code = self._product_combo.currentData() or ""
        self._product = current_code
        self._refresh_dealias_visible()

    def products(self) -> list[tuple[str, str]]:
        """(code, label) for each product the picker offers and has data for."""
        model = self._product_combo.model()
        out = []
        for i in range(self._product_combo.count()):
            item = model.item(i)
            if item is not None and not item.isEnabled():
                continue
            out.append((self._product_combo.itemData(i), self._product_combo.itemText(i)))
        return out

    def select_product(self, code: str) -> None:
        """Pick ``code`` in the product box, as choosing it there does."""
        idx = self._product_combo.findData(code)
        if idx >= 0:
            self._product_combo.setCurrentIndex(idx)

    def set_cache_size(self, n: int):
        """update slider range as scan cache grows; stay at live if already there."""
        was_live = self.is_at_latest_frame()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, n - 1))
        if was_live:
            self._frame_slider.setValue(n - 1)
        self._frame_slider.blockSignals(False)
        has_history = n > 1
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(has_history)

    def set_frame(self, idx: int):
        """move slider to index without emitting frame_requested."""
        self._frame_slider.blockSignals(True)
        self._frame_slider.setValue(idx)
        self._frame_slider.blockSignals(False)

    def current_frame(self) -> int:
        return self._frame_slider.value()

    def is_at_latest_frame(self) -> bool:
        return self._frame_slider.value() >= self._frame_slider.maximum()

    def is_looping(self) -> bool:
        return self._btn_play.isChecked()

    def toggle_loop(self) -> None:
        """Play/pause the loop (Space). No-op until there are frames to loop."""
        if self._btn_play.isEnabled():
            self._btn_play.toggle()

    def step_frame(self, delta: int) -> None:
        """Step one frame back (-1) or forward (+1) ([ / ]). No-op without history."""
        btn = self._btn_back if delta < 0 else self._btn_fwd
        if btn.isEnabled():
            btn.click()

    def stop_loop(self):
        if self._btn_play.isChecked():
            self._btn_play.setChecked(False)   # triggers _on_play_toggled(False)

    def set_sync_vehicles(self, enabled: bool) -> None:
        self._btn_sync_vehicles.setChecked(enabled)

    # ── playback API shared with the loop timeline ──
    def request_frame(self, idx: int) -> None:
        """Show frame ``idx`` as if the user picked it."""
        idx = max(0, min(self._frame_slider.maximum(), int(idx)))
        self.set_frame(idx)
        self.frame_requested.emit(idx)

    def speed_labels(self) -> list[str]:
        return [self._speed_combo.itemText(i) for i in range(self._speed_combo.count())]

    def speed_index(self) -> int:
        return self._speed_combo.currentIndex()

    def set_speed_index(self, index: int) -> None:
        self._speed_combo.setCurrentIndex(index)

    def set_playback_row_visible(self, visible: bool) -> None:
        self._playback_row.setVisible(visible)
        self.content_changed()

    def jump_to_latest(self) -> None:
        """Stop the loop and show the live frame."""
        self.stop_loop()
        self._on_jump_end()

    def reset_cache_ui(self):
        """reset playback controls when site or product changes."""
        self.stop_loop()
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.setValue(0)
        self._frame_slider.blockSignals(False)
        for w in (self._frame_slider, self._btn_back, self._btn_fwd,
                  self._btn_play, self._btn_jump_start, self._btn_jump_end):
            w.setEnabled(False)

    def configure_for_archive(self, enabled: bool) -> None:
        self._archive_mode = enabled
        # archive: on from the start, and turning it off stops the fetching
        # too (the lidar's "show on map"); live keeps its own off default
        self._chk_show_data.blockSignals(True)
        self._chk_show_data.setChecked(enabled)
        self._chk_show_data.blockSignals(False)
        self._chk_show_data.setText("show on map" if enabled else "show data")
        self._chk_show_data.setToolTip(
            "Fetch and draw the radar; off stops its downloads and clears it from the map" if enabled
            else "enable or disable radar data fetch and display"
        )
        playback_row = self._drawer.layout().itemAt(1).widget()
        if playback_row is not None:
            playback_row.setVisible(not enabled)
        self._tilt_combo.setVisible(enabled and self._tilt_combo.count() > 0)
        self._btn_dealias.setVisible(enabled and self._product == "velocity")
        self._btn_vad.setToolTip(self._vad_archive_tip if enabled else "View VAD wind profile hodograph")
        self.content_changed()

    def set_vad_available(self, available: bool, tooltip: str = "") -> None:
        """Archive VAD is a provider's wind profiles (archive_wind_profiles: NSSL's
        lidar winds): hidden without one; *tooltip* names its instruments."""
        self._btn_vad.setVisible(available)
        self._vad_archive_tip = tooltip or "Lidar wind profiles"
        if self._archive_mode:
            self._btn_vad.setToolTip(self._vad_archive_tip)
        self.content_changed()

    def current_tilt_index(self) -> int:
        """The chosen sweep's index in the volume, or -1 when none is listed."""
        sweep = self._tilt_combo.currentData()
        return int(sweep) if sweep is not None else self._tilt_combo.currentIndex()

    def select_tilt(self, sweep_index: int) -> None:
        """Select the entry for this sweep's angle, emitting tilt_changed."""
        if self._tilt_combo.count():
            self._tilt_combo.setCurrentIndex(self._tilt_row_for_sweep(sweep_index))

    def _tilt_row_for_sweep(self, sweep_index: int) -> int:
        if not 0 <= sweep_index < len(self._sweep_angles):
            return 0
        angle = self._sweep_angles[sweep_index]
        return next(
            (row for row in range(self._tilt_combo.count())
             if self._sweep_angles[self._tilt_combo.itemData(row)] == angle),
            0,
        )

    def set_archive_products(self, products: list[tuple[str, str]]) -> None:
        if not products:
            return
        current = self._product if self._archive_mode else None
        self._set_product_items(products, preserve_code=current or products[0][0])

    def set_archive_tilts(self, tilts: list[float], current_index: int | None = None) -> None:
        """tilts: every sweep's elevation, in volume order. Each angle is
        listed once (split cuts and SAILS rescans repeat 0.5° etc.), as its
        first sweep; the decoder then uses the sweep at that angle that
        carries the chosen product. current_index is a sweep index."""
        self._sweep_angles = [round(float(deg), 1) for deg in tilts]
        first_sweep: dict[float, int] = {}
        for i, deg in enumerate(self._sweep_angles):
            first_sweep.setdefault(deg, i)
        self._tilt_combo.blockSignals(True)
        self._tilt_combo.clear()
        for deg, sweep in sorted(first_sweep.items()):
            self._tilt_combo.addItem(f"{deg:.1f}°", sweep)
        if self._tilt_combo.count():
            current = current_index if current_index is not None else 0
            current = max(0, min(current, len(self._sweep_angles) - 1))
            self._tilt_combo.setCurrentIndex(self._tilt_row_for_sweep(current))
        self._tilt_combo.blockSignals(False)
        self._tilt_combo.setVisible(self._archive_mode and self._tilt_combo.count() > 0)
        self.content_changed()


    def _refresh_product_availability(self, site_id: str):
        site = normalize_site(site_id)
        if not site:
            return

        availability: dict[str, bool] = {"N0B": True, "N0U": True}
        for code, _label in OPTIONAL_PRODUCTS:
            avail = self._is_product_available(site, code)
            self._product_availability[(site, code)] = avail
            availability[code] = avail

        # only apply if the user hasn't switched sites since the probe started
        if site != self._site:
            return

        # emit signal instead of QTimer.singleShot — PyQt6 AutoConnection safely
        emit_alive(self, "_products_refreshed", availability)

    def _apply_product_items(self, availability: dict):
        """Slot — always runs on the main thread via the _products_refreshed signal."""
        self._apply_availability_to_combo(availability)
        self.product_availability_changed.emit(dict(availability))

    def _apply_availability_to_combo(self, availability: dict):
        """Enable/disable combo items based on availability map."""
        model = self._product_combo.model()
        for i in range(self._product_combo.count()):
            code = self._product_combo.itemData(i)
            item = model.item(i)
            if item is None:
                continue
            avail = bool(availability.get(code, True))
            item.setEnabled(avail)
            if not avail:
                item.setToolTip(f"No {code} data available for {self._site}")
            else:
                item.setToolTip("")

    def _is_product_available(self, site: str, product: str) -> bool:
        key = (site, product)
        if key in self._product_availability:
            return self._product_availability[key]

        site_token = thredds_site_token(site)
        url = f"{THREDDS_CATALOG_ROOT}/{product}/{site_token}/catalog.xml"
        try:
            with urlopen(url, timeout=6) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            root = ET.fromstring(xml_text)
            ns = {"cat": paths.THREDDS_NS}
            # site catalogs typically contain day subcatalog refs, not dataset
            for ds in root.findall(".//cat:dataset", ns):
                if ds.attrib.get("urlPath"):
                    return True
            for ref in root.findall(".//cat:catalogRef", ns):
                if ref.attrib.get("{http://www.w3.org/1999/xlink}href"):
                    return True
        except Exception:
            return False
        return False


    def toggle_drawer(self, checked: bool):
        """animate RadarControls open or closed — called by the toolbar RADAR button."""
        self._radar_on = checked
        super().toggle_drawer(checked)

    def _on_data_enabled_toggled(self, checked: bool):
        """show data checkbox — controls whether data is actually fetched."""
        if self._archive_mode:
            self.archive_data_toggled.emit(checked)
            return
        self.radar_toggled.emit(checked)
        if checked:
            self.fetch_requested.emit()

    def _on_play_toggled(self, checked: bool):
        self._btn_play.setText("⏸" if checked else "▶")
        self.loop_toggled.emit(checked)

    def _on_product_changed(self, index: int):
        product = self._product_combo.itemData(index)
        if not product:
            return
        self._product = product
        self._refresh_dealias_visible()
        self.product_changed.emit(product)

    def _on_tilt_changed(self, index: int):
        if index >= 0:
            sweep = self._tilt_combo.itemData(index)
            self.tilt_changed.emit(int(sweep) if sweep is not None else index)

    def _on_slider_released(self):
        # emit frame_requested only when user releases the slider handle
        self.frame_requested.emit(self._frame_slider.value())

    def _on_jump_start(self):
        self.set_frame(0)
        self.frame_requested.emit(0)

    def _on_jump_end(self):
        n = self._frame_slider.maximum()
        self.set_frame(n)
        self.frame_requested.emit(n)

    def _on_step_back(self):
        # use set_frame + manual emit so we bypass the sliderReleased path consistently
        new_val = max(0, self._frame_slider.value() - 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _on_step_forward(self):
        new_val = min(self._frame_slider.maximum(), self._frame_slider.value() + 1)
        self.set_frame(new_val)
        self.frame_requested.emit(new_val)

    def _on_speed_changed(self, index: int):
        ms = self._speed_combo.itemData(index)
        if ms is not None:
            self.speed_changed.emit(ms)
