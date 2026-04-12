# ui/vad_dialog.py
# VAD Wind Profile dialog with hodograph display and time scrubber.

import logging
import numpy as np
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QWidget, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView

from core.vad import VADProfile, VADSet
from data.vad_fetcher import fetch_recent_vads

log = logging.getLogger(__name__)


class VADDialog(QDialog):
    """Dialog displaying VAD wind profile hodograph with time scrubber."""
    
    def __init__(self, site: str, parent=None):
        super().__init__(parent)
        self.site = site
        self.vad_set: Optional[VADSet] = None
        self.current_index = 0
        
        self.setWindowTitle(f"VAD Wind Profile — {site}")
        self.setMinimumSize(700, 800)
        
        self._build_ui()
        self._apply_styles()
        
        # Fetch VAD data in background
        QTimer.singleShot(100, self._fetch_data)
    
    def _build_ui(self):
        """Build the dialog UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QLabel(f"VAD Wind Profile — {self.site}")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        
        # Time scrubber
        scrubber_layout = QHBoxLayout()
        scrubber_layout.setSpacing(8)
        
        self._time_label_start = QLabel("--:--Z")
        self._time_label_start.setObjectName("timeLabel")
        scrubber_layout.addWidget(self._time_label_start)
        
        self._time_slider = QSlider(Qt.Orientation.Horizontal)
        self._time_slider.setMinimum(0)
        self._time_slider.setMaximum(0)
        self._time_slider.setValue(0)
        self._time_slider.setEnabled(False)
        self._time_slider.valueChanged.connect(self._on_slider_changed)
        scrubber_layout.addWidget(self._time_slider, stretch=1)
        
        self._time_label_end = QLabel("--:--Z")
        self._time_label_end.setObjectName("timeLabel")
        scrubber_layout.addWidget(self._time_label_end)
        
        layout.addLayout(scrubber_layout)
        
        # Current time display
        self._current_time_label = QLabel("Loading VAD data...")
        self._current_time_label.setObjectName("currentTime")
        self._current_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._current_time_label)
        
        # Hodograph display
        self._hodograph_label = QLabel()
        self._hodograph_label.setObjectName("hodograph")
        self._hodograph_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hodograph_label.setMinimumSize(600, 600)
        self._hodograph_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._hodograph_label.setStyleSheet("background-color: #0A0A0F; border: 1px solid #1E1E2E; border-radius: 6px;")
        layout.addWidget(self._hodograph_label, stretch=1)
        
        # Parameters display
        params_widget = QWidget()
        params_widget.setObjectName("paramsWidget")
        params_layout = QVBoxLayout(params_widget)
        params_layout.setContentsMargins(12, 12, 12, 12)
        params_layout.setSpacing(6)
        
        params_title = QLabel("PARAMETERS")
        params_title.setObjectName("paramsTitle")
        params_layout.addWidget(params_title)
        
        self._params_text = QLabel("--")
        self._params_text.setObjectName("paramsText")
        self._params_text.setWordWrap(True)
        params_layout.addWidget(self._params_text)
        
        layout.addWidget(params_widget)
        
        # Cursor readout
        self._cursor_label = QLabel("Hover over hodograph for wind data")
        self._cursor_label.setObjectName("cursorLabel")
        self._cursor_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._cursor_label)
    
    def _apply_styles(self):
        """Apply dark theme styles."""
        self.setStyleSheet("""
            QDialog {
                background-color: #0A0A0F;
            }
            QLabel {
                color: #E8EAF0;
                background: transparent;
            }
            QLabel#header {
                color: #00CFFF;
                font-size: 18px;
                font-weight: 700;
                letter-spacing: 2px;
            }
            QLabel#timeLabel {
                color: #8E97AB;
                font-size: 11px;
                font-family: monospace;
            }
            QLabel#currentTime {
                color: #00CFFF;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#paramsTitle {
                color: #8E97AB;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 1px;
            }
            QLabel#paramsText {
                color: #E8EAF0;
                font-size: 12px;
                font-family: monospace;
            }
            QLabel#cursorLabel {
                color: #5A5B6A;
                font-size: 11px;
                font-family: monospace;
            }
            QWidget#paramsWidget {
                background-color: #12121E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
            }
            QSlider::groove:horizontal {
                background: #1A1A2E;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #00CFFF;
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #33D9FF;
            }
            QPushButton#closeBtn {
                background-color: #1A1A2E;
                border: 1px solid #1E1E2E;
                border-radius: 6px;
                color: #8E97AB;
                font-size: 12px;
                padding: 6px 24px;
            }
            QPushButton#closeBtn:hover {
                border-color: #00CFFF;
                color: #00CFFF;
            }
        """)
    
    def _fetch_data(self):
        """Fetch VAD data from THREDDS."""
        try:
            self.vad_set = fetch_recent_vads(self.site, count=12)
            
            if len(self.vad_set) == 0:
                self._current_time_label.setText(
                    f"VAD data not available via THREDDS"
                )
                self._current_time_label.setStyleSheet(
                    "color: #FFD166; font-size: 13px; font-weight: 600; padding: 10px;"
                )
                self._hodograph_label.setText(
                    "VAD Wind Profile data is not currently available\n\n"
                    "The NVW (VAD Wind Profile) product is not archived on the public\n"
                    "THREDDS server. VAD data may be available from:\n\n"
                    "  • Real-time Level II radar data streams\n"
                    "  • NEXRAD Level II archive (AWS S3)\n"
                    "  • Local radar data processing\n\n"
                    "This feature will be enhanced in a future update to support\n"
                    "VAD extraction from Level II radar data."
                )
                self._hodograph_label.setStyleSheet(
                    "background-color: #0A0A0F; border: 1px solid #1E1E2E; "
                    "border-radius: 6px; color: #8E97AB; font-size: 11px; padding: 40px;"
                )
                log.info(f"VAD data not available on THREDDS for {self.site}")
                return
            
            # Update slider range
            self._time_slider.setMaximum(len(self.vad_set) - 1)
            self._time_slider.setValue(0)
            self._time_slider.setEnabled(True)
            
            # Update time labels
            timestamps = self.vad_set.timestamps
            self._time_label_start.setText(timestamps[-1].strftime("%H:%M") + "Z")
            self._time_label_end.setText(timestamps[0].strftime("%H:%M") + "Z")
            
            # Display first (most recent) profile
            self._display_profile(0)
            
        except Exception as e:
            log.error(f"Failed to fetch VAD data: {e}", exc_info=True)
            self._current_time_label.setText(
                f"Error loading VAD data: {str(e)}\n\n"
                "Check the error log (Ctrl+E) for details."
            )
            self._current_time_label.setStyleSheet(
                "color: #E53935; font-size: 12px; font-weight: 500; padding: 20px;"
            )
    
    def _on_slider_changed(self, value: int):
        """Handle time slider change."""
        self.current_index = value
        self._display_profile(value)
    
    def _display_profile(self, index: int):
        """Display VAD profile at given index."""
        if not self.vad_set or index >= len(self.vad_set):
            return
        
        profile = self.vad_set[index]
        
        # Update current time label
        self._current_time_label.setText(
            f"{profile.timestamp.strftime('%Y-%m-%d %H:%M')}Z"
        )
        
        # Render hodograph
        self._render_hodograph(profile)
        
        # Calculate and display parameters
        self._update_parameters(profile)
    
    def _render_hodograph(self, profile: VADProfile):
        """Render hodograph as PNG and display."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from matplotlib.figure import Figure
            from io import BytesIO
            
            # Create figure
            fig = Figure(figsize=(8, 8), facecolor='#0A0A0F')
            ax = fig.add_subplot(111, facecolor='#0A0A0F')
            
            # Calculate U and V components
            u = profile.u_component()
            v = profile.v_component()
            
            # Plot hodograph
            ax.plot(u, v, color='#00CFFF', linewidth=2, marker='o', markersize=4)
            
            # Add height labels at key levels
            for height_km in [1, 3, 6, 9]:
                height_m = height_km * 1000
                if height_m <= profile.heights_m.max():
                    idx = np.argmin(np.abs(profile.heights_m - height_m))
                    ax.text(u[idx], v[idx], f'{height_km}km', 
                           color='#8E97AB', fontsize=9, ha='right', va='bottom')
            
            # Add origin marker
            ax.plot(0, 0, 'o', color='#E8EAF0', markersize=8)
            
            # Add range rings every 20 kt
            for ring_kt in [20, 40, 60, 80]:
                circle = plt.Circle((0, 0), ring_kt, fill=False, 
                                   color='#1E1E2E', linewidth=1, linestyle='--')
                ax.add_patch(circle)
                ax.text(ring_kt, 0, f'{ring_kt}kt', color='#5A5B6A', 
                       fontsize=8, ha='left', va='bottom')
            
            # Set equal aspect and limits
            max_wind = max(80, np.max(np.sqrt(u**2 + v**2)) + 10)
            ax.set_xlim(-max_wind, max_wind)
            ax.set_ylim(-max_wind, max_wind)
            ax.set_aspect('equal')
            
            # Style axes
            ax.spines['left'].set_color('#1E1E2E')
            ax.spines['bottom'].set_color('#1E1E2E')
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.tick_params(colors='#5A5B6A', labelsize=9)
            ax.grid(True, color='#1E1E2E', linewidth=0.5, alpha=0.5)
            
            ax.set_xlabel('U (kt)', color='#8E97AB', fontsize=10)
            ax.set_ylabel('V (kt)', color='#8E97AB', fontsize=10)
            ax.set_title(f'Hodograph — {profile.label}', 
                        color='#E8EAF0', fontsize=12, pad=10)
            
            # Convert to PNG
            buf = BytesIO()
            fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', 
                       facecolor='#0A0A0F', edgecolor='none')
            buf.seek(0)
            plt.close(fig)
            
            # Display in QLabel
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            self._hodograph_label.setPixmap(pixmap)
            
        except Exception as e:
            log.error(f"Failed to render hodograph: {e}", exc_info=True)
            self._hodograph_label.setText("Error rendering hodograph")
    
    def _update_parameters(self, profile: VADProfile):
        """Calculate and display wind shear parameters."""
        try:
            params = self._calculate_parameters(profile)
            
            text = (
                f"0-1 km SRH:  {params['srh_0_1']:.0f} m²/s²     "
                f"0-1 km Shear: {params['shear_0_1']:.0f} kt\n"
                f"0-3 km SRH:  {params['srh_0_3']:.0f} m²/s²     "
                f"0-3 km Shear: {params['shear_0_3']:.0f} kt\n"
                f"0-6 km BWD:  {params['bwd_0_6']:.0f} kt\n"
                f"Bunkers RM:  {params['bunkers_dir']:.0f}° @ {params['bunkers_spd']:.0f} kt"
            )
            
            self._params_text.setText(text)
            
        except Exception as e:
            log.error(f"Failed to calculate parameters: {e}", exc_info=True)
            self._params_text.setText("Error calculating parameters")
    
    def _calculate_parameters(self, profile: VADProfile) -> dict:
        """Calculate hodograph-derived parameters."""
        # Get winds at key levels
        def get_wind_at_height(height_m):
            dir_val, spd_val = profile.interpolate_to_height(height_m)
            if np.isnan(dir_val) or np.isnan(spd_val):
                return 0.0, 0.0
            u = -spd_val * np.sin(np.deg2rad(dir_val))
            v = -spd_val * np.cos(np.deg2rad(dir_val))
            return u, v
        
        u_sfc, v_sfc = get_wind_at_height(0)
        u_1km, v_1km = get_wind_at_height(1000)
        u_3km, v_3km = get_wind_at_height(3000)
        u_6km, v_6km = get_wind_at_height(6000)
        
        # Bulk wind difference (0-6 km)
        bwd_0_6 = np.sqrt((u_6km - u_sfc)**2 + (v_6km - v_sfc)**2)
        
        # Shear magnitudes
        shear_0_1 = np.sqrt((u_1km - u_sfc)**2 + (v_1km - v_sfc)**2)
        shear_0_3 = np.sqrt((u_3km - u_sfc)**2 + (v_3km - v_sfc)**2)
        
        # Bunkers storm motion (simplified right-mover)
        u_mean = (u_sfc + u_6km) / 2
        v_mean = (v_sfc + v_6km) / 2
        shear_u = u_6km - u_sfc
        shear_v = v_6km - v_sfc
        
        # Right-mover deviation (7.5 m/s perpendicular to shear)
        deviation = 7.5 * 1.94384  # Convert m/s to kt
        shear_mag = np.sqrt(shear_u**2 + shear_v**2)
        if shear_mag > 0:
            u_rm = u_mean + deviation * (-shear_v / shear_mag)
            v_rm = v_mean + deviation * (shear_u / shear_mag)
        else:
            u_rm, v_rm = u_mean, v_mean
        
        bunkers_spd = np.sqrt(u_rm**2 + v_rm**2)
        bunkers_dir = (np.degrees(np.arctan2(-u_rm, -v_rm)) + 360) % 360
        
        # Storm-relative helicity (simplified calculation)
        # SRH = integral of (V x dV/dz) · k
        srh_0_1 = self._calculate_srh(profile, 0, 1000, u_rm, v_rm)
        srh_0_3 = self._calculate_srh(profile, 0, 3000, u_rm, v_rm)
        
        return {
            'bwd_0_6': bwd_0_6,
            'shear_0_1': shear_0_1,
            'shear_0_3': shear_0_3,
            'bunkers_dir': bunkers_dir,
            'bunkers_spd': bunkers_spd,
            'srh_0_1': srh_0_1,
            'srh_0_3': srh_0_3,
        }
    
    def _calculate_srh(self, profile: VADProfile, z_bottom: float, z_top: float, 
                       u_storm: float, v_storm: float) -> float:
        """Calculate storm-relative helicity over a layer."""
        # Filter heights in layer
        mask = (profile.heights_m >= z_bottom) & (profile.heights_m <= z_top)
        if mask.sum() < 2:
            return 0.0
        
        heights = profile.heights_m[mask]
        u = profile.u_component()[mask]
        v = profile.v_component()[mask]
        
        # Storm-relative winds
        u_sr = u - u_storm
        v_sr = v - v_storm
        
        # Integrate SRH using trapezoidal rule
        srh = 0.0
        for i in range(len(heights) - 1):
            dz = heights[i+1] - heights[i]
            du = u[i+1] - u[i]
            dv = v[i+1] - v[i]
            
            # Cross product: (u_sr, v_sr, 0) x (du, dv, dz)
            srh += (u_sr[i] * dv - v_sr[i] * du)
        
        return srh
