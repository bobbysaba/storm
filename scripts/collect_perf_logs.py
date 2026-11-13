"""Zip STORM's performance logs into one file to upload.

    python scripts/collect_perf_logs.py

Collects perf_logs/ (performance logs, each with
a readable summary next to it), storm_errors.log* and storm_fault.log into
storm-perf-<computer>-<date>.zip on the Desktop (or the home folder), and
prints where it went. Nothing in the zip contains passwords or keys.
"""
from __future__ import annotations

import socket
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _desktop() -> Path:
    home = Path.home()
    for folder in (home / "Desktop", home / "OneDrive" / "Desktop", *home.glob("OneDrive*/Desktop")):
        if folder.is_dir():
            return folder
    return home


def main() -> int:
    from summarize_perf_log import _load, summarize
    logs = sorted((ROOT / "perf_logs").glob("perf-*.jsonl"))
    benchmarks = sorted((ROOT / "perf_logs").glob("benchmark-*.json"))
    extras = [p for pattern in ("storm_errors.log*", "storm_fault.log") for p in ROOT.glob(pattern)]
    if not (logs or benchmarks):
        print("Nothing to collect: run STORM with --perf-log first.")
        return 1
    host = "".join(c for c in socket.gethostname() if c.isalnum() or c in "-_")[:32] or "computer"
    out = _desktop() / f"storm-perf-{host}-{datetime.now():%Y%m%d-%H%M}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for log in logs:
            bundle.write(log, f"perf_logs/{log.name}")
            try:
                bundle.writestr(f"perf_logs/{log.stem}-summary.md", summarize(_load(log)))
            except Exception as exc:  # noqa: BLE001 - the raw log is what matters
                bundle.writestr(f"perf_logs/{log.stem}-summary.md", f"Summary failed: {exc}\n")
        for result in benchmarks:
            bundle.write(result, f"perf_logs/{result.name}")
        for extra in extras:
            bundle.write(extra, extra.name)
    size_mb = out.stat().st_size / 1e6
    print(f"{len(logs)} session log(s), {len(benchmarks)} benchmark(s), {len(extras)} STORM log file(s)")
    print(f"Saved {out} ({size_mb:.1f} MB) -- upload this file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
