# data/vad_fetcher.py
# Fetches and decodes NEXRAD VAD Wind Profile (NVW) products from THREDDS.

import logging
import requests
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
from metpy.io import Level3File

from core.vad import VADProfile, VADSet

log = logging.getLogger(__name__)

# THREDDS base URL for Level 3 NVW products
THREDDS_BASE = "https://thredds.ucar.edu/thredds/fileServer/nexrad/level3/NVW"


def fetch_vad_profile(site: str, timestamp: datetime) -> Optional[VADProfile]:
    """Fetch a single VAD profile from THREDDS.
    
    Args:
        site: Radar site identifier (e.g., "TLX")
        timestamp: UTC datetime to fetch (rounded to nearest VAD time)
    
    Returns:
        VADProfile if successful, None otherwise
    """
    # NVW files are named: Level3_{SITE}_NVW_{YYYYMMDD}_{HHMM}.nids
    date_str = timestamp.strftime("%Y%m%d")
    time_str = timestamp.strftime("%H%M")
    filename = f"Level3_{site}_NVW_{date_str}_{time_str}.nids"
    url = f"{THREDDS_BASE}/{site}/{date_str}/{filename}"
    
    try:
        log.debug(f"Fetching VAD: {url}")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        # Decode NIDS file with MetPy
        f = Level3File(BytesIO(response.content))
        
        # Extract VAD data from the product
        # NVW product structure: altitude levels with wind speed/direction
        vad_data = _parse_nvw_product(f, site, timestamp)
        
        if vad_data:
            log.info(f"Fetched VAD profile: {site} {timestamp.strftime('%Y-%m-%d %H:%M')}Z")
            return vad_data
        else:
            log.warning(f"No valid VAD data in {filename}")
            return None
            
    except requests.RequestException as e:
        log.debug(f"Failed to fetch VAD {filename}: {e}")
        return None
    except Exception as e:
        log.error(f"Error parsing VAD {filename}: {e}", exc_info=True)
        return None


def _parse_nvw_product(f: Level3File, site: str, timestamp: datetime) -> Optional[VADProfile]:
    """Parse MetPy Level3File NVW product into VADProfile.
    
    NVW product contains tabular wind data at various altitudes.
    """
    try:
        # NVW stores data in tabular format
        # Access the product-specific data
        if not hasattr(f, 'sym_block') or not f.sym_block:
            return None
        
        # Extract wind data from symbology block
        # NVW format: each packet contains altitude, direction, speed
        heights = []
        directions = []
        speeds = []
        
        for packet in f.sym_block[0]:
            # Packet type 8: text/special symbols (VAD uses this)
            if hasattr(packet, 'data') and len(packet.data) > 0:
                # Parse VAD table entries
                # Format varies, but typically: altitude(ft), dir(deg), spd(kt)
                for entry in packet.data:
                    if hasattr(entry, 'height') and hasattr(entry, 'direction') and hasattr(entry, 'speed'):
                        # Convert feet to meters
                        height_m = entry.height * 0.3048
                        heights.append(height_m)
                        directions.append(entry.direction)
                        speeds.append(entry.speed)
        
        # If no data found in symbology block, try tabular block
        if not heights and hasattr(f, 'tab_block') and f.tab_block:
            for page in f.tab_block:
                for line in page:
                    # VAD tabular format: typically space-separated values
                    # Example: "  5000  240  35" (height_ft, dir, spd)
                    if isinstance(line, str):
                        parts = line.split()
                        if len(parts) >= 3:
                            try:
                                height_ft = float(parts[0])
                                direction = float(parts[1])
                                speed = float(parts[2])
                                if 0 <= direction <= 360 and speed >= 0:
                                    heights.append(height_ft * 0.3048)
                                    directions.append(direction)
                                    speeds.append(speed)
                            except ValueError:
                                continue
        
        if not heights:
            log.debug(f"No VAD data found in NVW product for {site}")
            return None
        
        # Convert to numpy arrays and sort by height
        heights = np.array(heights)
        directions = np.array(directions)
        speeds = np.array(speeds)
        
        sort_idx = np.argsort(heights)
        heights = heights[sort_idx]
        directions = directions[sort_idx]
        speeds = speeds[sort_idx]
        
        # Calculate RMS error if available in product header
        rms_error = 0.0
        if hasattr(f, 'metadata') and 'rms_error' in f.metadata:
            rms_error = f.metadata['rms_error']
        
        return VADProfile(
            timestamp=timestamp,
            site=site,
            heights_m=heights,
            wind_dir=directions,
            wind_spd=speeds,
            rms_error=rms_error,
        )
        
    except Exception as e:
        log.error(f"Error parsing NVW product: {e}", exc_info=True)
        return None


def fetch_recent_vads(site: str, count: int = 12) -> VADSet:
    """Fetch the most recent VAD profiles for a site.
    
    Args:
        site: Radar site identifier (e.g., "TLX")
        count: Number of recent profiles to fetch (default: 12, ~1-2 hours)
    
    Returns:
        VADSet containing successfully fetched profiles
    """
    now = datetime.now(timezone.utc)
    profiles = []
    
    # VADs are generated approximately every 6-10 minutes
    # Try fetching at 6-minute intervals going back up to 3 hours
    max_attempts = count * 3  # Try 3x as many times to account for gaps
    for i in range(max_attempts):
        # Go back in 6-minute increments
        target_time = now - timedelta(minutes=i * 6)
        # Round to nearest 6 minutes
        target_time = target_time.replace(minute=(target_time.minute // 6) * 6, second=0, microsecond=0)
        
        profile = fetch_vad_profile(site, target_time)
        if profile:
            profiles.append(profile)
            if len(profiles) >= count:
                break
    
    log.info(f"Fetched {len(profiles)}/{count} VAD profiles for {site}")
    return VADSet(profiles=profiles)


def fetch_vad_at_time(site: str, timestamp: datetime) -> Optional[VADProfile]:
    """Fetch VAD profile closest to a specific time (for archive mode).
    
    Args:
        site: Radar site identifier
        timestamp: Target UTC datetime
    
    Returns:
        VADProfile if found within ±30 minutes, None otherwise
    """
    # Try exact time first
    profile = fetch_vad_profile(site, timestamp)
    if profile:
        return profile
    
    # Try nearby times (±30 minutes in 10-minute steps)
    for offset_min in [10, -10, 20, -20, 30, -30]:
        nearby_time = timestamp + timedelta(minutes=offset_min)
        profile = fetch_vad_profile(site, nearby_time)
        if profile:
            return profile
    
    log.warning(f"No VAD profile found for {site} near {timestamp.strftime('%Y-%m-%d %H:%M')}Z")
    return None
