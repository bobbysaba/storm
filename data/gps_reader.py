# data/gps_reader.py
# Track B — reads NMEA sentences from a serial GPS puck.
#
# Parses GGA and RMC sentences via pynmea2, emits one position-only
# Observation every GPS_EMIT_INTERVAL seconds via Qt signal.
#
# Runs in a daemon thread so it never blocks the UI.  On serial errors
# (port not present, device unplugged) it logs a warning and retries
# every RETRY_DELAY seconds — no crash, no user action needed.

import logging
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from core.observation import Observation

log = logging.getLogger(__name__)

GPS_EMIT_INTERVAL = 10.0   # seconds between emitted observations
RETRY_DELAY       = 5.0    # seconds before retrying after a serial error
_PROBE_TIMEOUT    = 2.0    # seconds to listen when probing a candidate port
_PROBE_LINES      = 20     # max NMEA lines to read during probe

# Description substrings that strongly suggest a GPS device
_GPS_KEYWORDS = ("gps", "gnss", "u-blox", "globalsat", "sirf", "nmea",
                 "garmin", "trimble", "bu-353", "vk-162")

# USB Vendor IDs known to be used almost exclusively by GPS modules
_GPS_VIDS = (
    0x1546,   # u-blox
    0x067B,   # Prolific PL2303 (common on cheap GPS pucks)
)

# USB Vendor IDs used by generic serial adapters (lower priority, but still worth trying)
_ADAPTER_VIDS = (
    0x10C4,   # SiLabs CP210x
    0x0403,   # FTDI
)


def _score_port(port_info) -> int:
    """Return a priority score for a serial port — higher = more likely GPS."""
    score = 0
    desc = (port_info.description or "").lower()
    mfr  = (port_info.manufacturer or "").lower()
    vid  = getattr(port_info, "vid", None)

    for kw in _GPS_KEYWORDS:
        if kw in desc or kw in mfr:
            score += 10

    if vid in _GPS_VIDS:
        score += 5
    elif vid in _ADAPTER_VIDS:
        score += 2

    return score


def _probe_port(port: str, baud: int) -> bool:
    """
    Open *port* briefly and check whether it produces valid NMEA sentences.
    Returns True if at least one GGA or RMC sentence is parsed successfully.
    """
    try:
        import serial
        import pynmea2
    except ImportError:
        return False

    try:
        with serial.Serial(port, baud, timeout=_PROBE_TIMEOUT) as ser:
            for _ in range(_PROBE_LINES):
                raw = ser.readline().decode("ascii", errors="replace").strip()
                if not raw.startswith("$"):
                    continue
                try:
                    msg = pynmea2.parse(raw)
                    if isinstance(msg, (pynmea2.types.talker.GGA,
                                        pynmea2.types.talker.RMC)):
                        return True
                except pynmea2.ParseError:
                    continue
    except Exception:
        pass
    return False


def _detect_gps_port(baud: int) -> str | None:
    """
    Scan all serial ports, rank by GPS likelihood, and return the first one
    that produces valid NMEA sentences.  Returns None if nothing found.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    candidates = sorted(list_ports.comports(), key=_score_port, reverse=True)
    if not candidates:
        log.debug("GPSReader: no serial ports found during auto-detect")
        return None

    log.info("GPSReader: auto-detecting GPS port from %d candidate(s)…", len(candidates))
    for p in candidates:
        log.debug("GPSReader: probing %s (score=%d, desc=%r)",
                  p.device, _score_port(p), p.description)
        if _probe_port(p.device, baud):
            log.info("GPSReader: detected GPS on %s (%s)", p.device, p.description)
            return p.device

    log.warning("GPSReader: auto-detect found no GPS device")
    return None


class GPSReader(QObject):
    """
    Reads NMEA 0183 from a serial port and emits obs_ready once per
    GPS_EMIT_INTERVAL seconds.  Observation contains only lat/lon
    (no meteorological fields — those come from Track A).

    Usage:
        reader = GPSReader(vehicle_id="WX1",
                           port="/dev/tty.usbserial-0001", baud=4800)
        reader.obs_ready.connect(main_window.update_vehicle_obs)
        reader.start()
    """

    obs_ready = pyqtSignal(object)   # Observation

    def __init__(self, vehicle_id: str,
                 port: str, baud: int = 4800, parent=None):
        super().__init__(parent)
        self._vehicle_id = vehicle_id
        self._port       = port
        self._baud       = baud
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="gps-reader"
        )
        self._thread.start()
        if self._port:
            log.info("GPSReader: started on %s @ %d baud", self._port, self._baud)
        else:
            log.info("GPSReader: started in auto-detect mode @ %d baud", self._baud)

    def stop(self):
        self._stop_event.set()
        log.info("GPSReader: stop requested")

    # ── Worker thread ──────────────────────────────────────────────────────────

    def _run(self):
        # defer heavy imports to the worker thread so startup stays fast
        try:
            import serial
            import pynmea2
        except ImportError as e:
            log.error("GPSReader: missing dependency — %s", e)
            return

        lat: float | None = None
        lon: float | None = None
        last_emit: float  = 0.0

        while not self._stop_event.is_set():
            try:
                port = self._port or _detect_gps_port(self._baud)
                if not port:
                    log.debug("GPSReader: no GPS found — retrying in %.0fs", RETRY_DELAY)
                    self._stop_event.wait(RETRY_DELAY)
                    continue

                with serial.Serial(port, self._baud, timeout=1.0) as ser:
                    log.info("GPSReader: serial port %s open", port)
                    while not self._stop_event.is_set():
                        try:
                            raw = ser.readline().decode("ascii", errors="replace").strip()
                        except Exception:
                            continue

                        if not raw.startswith("$"):
                            continue

                        try:
                            msg = pynmea2.parse(raw)
                        except pynmea2.ParseError:
                            continue

                        # accept both GGA (fix + altitude) and RMC (fix + speed/heading)
                        if isinstance(msg, (pynmea2.types.talker.GGA,
                                            pynmea2.types.talker.RMC)):
                            if msg.latitude and msg.longitude:
                                lat = float(msg.latitude)
                                lon = float(msg.longitude)

                        now = time.monotonic()
                        if lat is not None and (now - last_emit) >= GPS_EMIT_INTERVAL:
                            obs = Observation.new(
                                vehicle_id=self._vehicle_id,
                                lat=lat,
                                lon=lon,
                            )
                            self.obs_ready.emit(obs)
                            last_emit = now

            except Exception as e:
                # catches serial.SerialException and anything else
                log.warning("GPSReader: %s — retrying in %.0fs", e, RETRY_DELAY)
                self._stop_event.wait(RETRY_DELAY)
