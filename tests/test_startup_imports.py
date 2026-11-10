import subprocess
import sys

HEAVY = ("matplotlib", "metpy", "scipy", "cartopy", "xarray")


def test_startup_does_not_load_science_stack():
    """main and the main window must not pull in the heavy science modules at import."""
    code = (
        "import sys, main, ui.app.main_window\n"
        f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == ""
