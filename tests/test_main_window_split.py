"""MainWindow is split across mixins; moving code must not lose or shadow a method."""
import inspect
from pathlib import Path

from ui.app.main_window import MainWindow

SNAPSHOT = Path(__file__).parent / "data" / "main_window_methods.txt"


def _definitions():
    """method name -> classes in MainWindow's MRO (from ui.app) that define it."""
    defs: dict[str, list[str]] = {}
    for cls in MainWindow.__mro__:
        if not cls.__module__.startswith("ui.app."):
            continue
        for name, value in vars(cls).items():
            if inspect.isfunction(value):
                defs.setdefault(name, []).append(cls.__name__)
    return defs


def test_every_method_still_resolves():
    expected = SNAPSHOT.read_text().split()
    missing = [n for n in expected if not inspect.isfunction(getattr(MainWindow, n, None))]
    assert missing == []


def test_no_method_defined_twice():
    dupes = {n: owners for n, owners in _definitions().items() if len(owners) > 1}
    assert dupes == {}
