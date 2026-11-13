"""Emitting from worker threads whose owner may be gone.

A fetcher is a child of its window, so Change Day deletes it while one of
its download threads may still be running (main.py's session loop). That
thread's next emit would raise "wrapped C/C++ object has been deleted";
emit_alive() drops the signal instead.
"""
from PyQt6 import sip


def emit_alive(obj, signal: str, *args) -> bool:
    """obj.<signal>.emit(*args), unless obj has been deleted. Returns whether it emitted."""
    if sip.isdeleted(obj):
        return False
    try:
        getattr(obj, signal).emit(*args)
    except RuntimeError:          # deleted between the check and the emit
        return False
    return True
