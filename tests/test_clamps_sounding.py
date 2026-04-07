"""
Quick test: load a local .skewT file and display it in the SoundingDialog.
Run with:
    python test_clamps_sounding.py [path/to/file.skewT]
Defaults to the sample file in ~/Downloads.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import QApplication
from core.sounding import SoundingSet
from data.clamps_sounding_fetcher import _parse_skewt, _FILENAME_RE
from ui.sounding_dialog import SoundingDialog

DEFAULT_FILE = os.path.expanduser(
    "~/Downloads/upperair.NSSL_Lidar_sonde.202506061759.skewT"
)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE

    with open(path) as f:
        text = f.read()

    # Parse timestamp from filename, fall back to now
    m = _FILENAME_RE.search(os.path.basename(path))
    if m:
        file_time = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(
            tzinfo=timezone.utc
        )
    else:
        file_time = datetime.now(timezone.utc)

    snd = _parse_skewt(text, file_time, 0)
    if snd is None:
        print("ERROR: failed to parse sounding from", path)
        sys.exit(1)

    snd.slot_offset = 0
    snd.label       = file_time.strftime("%-HZ %b %-d")

    sset = SoundingSet(
        lat          = 0.0,
        lon          = 0.0,
        elevation    = float(snd.height[0]),
        fetch_time   = datetime.now(timezone.utc),
        soundings    = [snd],
        station_id   = "CLAMPS",
        station_name = "NSSL CLAMPS DL Truck",
        source       = "nssl",
    )

    print(f"Loaded {len(snd.pressure)} levels, "
          f"{snd.pressure[0]:.1f}–{snd.pressure[-1]:.1f} hPa")

    app = QApplication(sys.argv)
    dlg = SoundingDialog()
    dlg.load(sset)
    dlg.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
