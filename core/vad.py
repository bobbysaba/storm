# core/vad.py
# VAD (Velocity Azimuth Display) wind profile data structure.

from dataclasses import dataclass
from datetime import datetime
import numpy as np


@dataclass(frozen=True)
class VADProfile:
    """Single VAD wind profile from NEXRAD NVW product.
    
    Attributes:
        timestamp: UTC time of the VAD analysis
        site: Radar site identifier (e.g., "TLX")
        heights_m: Array of height levels in meters AGL
        wind_dir: Array of wind directions in degrees (0-360)
        wind_spd: Array of wind speeds in knots
        rms_error: RMS error of the VAD fit (quality indicator)
    """
    timestamp: datetime
    site: str
    heights_m: np.ndarray
    wind_dir: np.ndarray
    wind_spd: np.ndarray
    rms_error: float = 0.0
    
    def __post_init__(self):
        """Validate that arrays have consistent shapes."""
        if not (len(self.heights_m) == len(self.wind_dir) == len(self.wind_spd)):
            raise ValueError("VAD arrays must have matching lengths")
    
    @property
    def label(self) -> str:
        """Human-readable label for this profile."""
        return f"{self.site} VAD {self.timestamp.strftime('%H:%M')}Z"
    
    def u_component(self) -> np.ndarray:
        """Calculate U (eastward) wind component in knots."""
        return -self.wind_spd * np.sin(np.deg2rad(self.wind_dir))
    
    def v_component(self) -> np.ndarray:
        """Calculate V (northward) wind component in knots."""
        return -self.wind_spd * np.cos(np.deg2rad(self.wind_dir))
    
    def interpolate_to_height(self, target_height_m: float) -> tuple[float, float]:
        """Interpolate wind direction and speed to a specific height.
        
        Returns:
            (wind_dir, wind_spd) at target_height_m, or (nan, nan) if out of range
        """
        if target_height_m < self.heights_m.min() or target_height_m > self.heights_m.max():
            return (np.nan, np.nan)
        
        dir_interp = np.interp(target_height_m, self.heights_m, self.wind_dir)
        spd_interp = np.interp(target_height_m, self.heights_m, self.wind_spd)
        return (dir_interp, spd_interp)


@dataclass(frozen=True)
class VADSet:
    """Collection of VAD profiles for time-series display."""
    profiles: list[VADProfile]
    
    def __post_init__(self):
        """Sort profiles by timestamp."""
        object.__setattr__(self, 'profiles', sorted(self.profiles, key=lambda p: p.timestamp))
    
    def __len__(self) -> int:
        return len(self.profiles)
    
    def __getitem__(self, idx: int) -> VADProfile:
        return self.profiles[idx]
    
    @property
    def site(self) -> str:
        """Radar site (assumes all profiles from same site)."""
        return self.profiles[0].site if self.profiles else ""
    
    @property
    def timestamps(self) -> list[datetime]:
        """List of all profile timestamps."""
        return [p.timestamp for p in self.profiles]
