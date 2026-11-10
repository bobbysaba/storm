#!/usr/bin/env python3
"""
Step 0 of PERFORMANCE_PLAN.md: measure what dominates on this machine.

Run it on the target hardware from the repo root:

    python scripts/profile_storm.py            # imports + radar render
    python scripts/profile_storm.py --rss      # also RSS of running STORM processes

It needs no extra packages and prints a short table to paste into the plan.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _time_import(stmt: str) -> float:
    # a fresh interpreter each time, so nothing is already cached in sys.modules
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", stmt], check=True, capture_output=True)
    return (time.perf_counter() - t0) * 1000.0


def _synthetic_scan(minute: int = 0, n_az: int = 720, n_gates: int = 1840):
    import numpy as np
    from core.radar_scan import RadarScan

    table = np.full(256, np.nan, dtype=np.float32)
    table[2:] = (np.arange(2, 256) - 2) * 0.5 - 32.0
    rng = np.random.default_rng(0)
    raw = rng.integers(0, 256, size=(n_az, n_gates), dtype=np.uint8)
    return RadarScan(
        site="KTST",
        product="N0B",
        scan_time=datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc),
        raw=raw,
        vmin=-32.0,
        vmax=90.0,
        units="dBZ",
        colormap="nws_ref",
        radar_lat=35.0,
        radar_lon=-97.0,
        azimuths_deg=np.arange(n_az) * (360.0 / n_az),
        first_gate_m=0.0,
        gate_width_m=250.0,
        value_table=table,
    )


def _radar_rows(grids: list[int]) -> list[tuple[str, str]]:
    from ui.map import radar_overlay as ro

    _synthetic_scan()  # warm numpy and the colour tables before timing
    ro._mpl_colors()   # first-frame matplotlib import is reported separately
    rows = []
    for i, grid in enumerate(grids):
        # the pixel index is cached per grid size, so the first frame at each grid is cold
        scan = _synthetic_scan(minute=2 * i)
        t0 = time.perf_counter()
        ro.render_scan_to_png(scan, grid)
        cold = (time.perf_counter() - t0) * 1000.0
        # same geometry, new data: reuses the pixel index, as a live loop does
        scan2 = _synthetic_scan(minute=2 * i + 1)
        t0 = time.perf_counter()
        ro.render_scan_to_png(scan2, grid)
        warm = (time.perf_counter() - t0) * 1000.0
        rows.append((f"radar render grid {grid}", f"cold {cold:.0f} ms / warm {warm:.0f} ms"))
    return rows


def _rss_rows() -> list[tuple[str, str]]:
    # ps works on macOS and Linux; Windows users can read Task Manager instead
    if sys.platform == "win32":
        return [("process RSS", "use Task Manager on Windows")]
    out = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True).stdout
    py_kb = web_kb = 0
    for line in out.splitlines():
        kb, _, cmd = line.strip().partition(" ")
        if "QtWebEngineProcess" in cmd:
            web_kb += int(kb)
        elif "main.py" in cmd and "python" in cmd.lower():
            py_kb += int(kb)
    return [
        ("STORM python RSS", f"{py_kb / 1024:.0f} MB"),
        ("QtWebEngineProcess RSS (all)", f"{web_kb / 1024:.0f} MB"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--rss", action="store_true", help="report RSS of a running STORM")
    parser.add_argument("--grids", default="384,768,1536", help="radar grid sizes to time")
    args = parser.parse_args()

    rows = [
        ("python startup", f"{_time_import('pass'):.0f} ms"),
        ("import main (launch dialog)", f"{_time_import('import main'):.0f} ms"),
        ("import main window", f"{_time_import('import main, ui.app.main_window'):.0f} ms"),
        ("import matplotlib (first radar frame)", f"{_time_import('import matplotlib.pyplot'):.0f} ms"),
        ("import metpy.calc (sounding dialog)", f"{_time_import('import metpy.calc'):.0f} ms"),
    ]
    rows += _radar_rows([int(g) for g in args.grids.split(",")])
    if args.rss:
        rows += _rss_rows()

    width = max(len(name) for name, _ in rows)
    print(f"{'measurement':<{width}}  result")
    print(f"{'-' * width}  ------")
    for name, value in rows:
        print(f"{name:<{width}}  {value}")


if __name__ == "__main__":
    main()
