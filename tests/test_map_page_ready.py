"""The real map page, loaded headless.

Runs in a child process: the storm:// scheme must be registered before any
QApplication exists, which other tests in the same process may already have
created. It exits with os._exit once it has its answer: QtWebEngine's own
teardown can hang the process (see main.py)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWebEngineWidgets")

ROOT = Path(__file__).resolve().parent.parent

_CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import main
main._register_storm_scheme()
import PyQt6.QtWebEngineWidgets
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
app = QApplication(["storm-test"])
from ui.map.widget import MapWidget
SCRIPT = sys.argv[2]
# asked of a blank page first: on the map page, once MapLibre holds its
# context, a new canvas gets none even where WebGL works
WEBGL = "(function() { var c = document.createElement('canvas');" \
        " return !!(c.getContext('webgl2') || c.getContext('webgl')); })()"

def load_map():
    global w
    w = MapWidget()
    w.resize(640, 480)
    w.show()
    w.map_ready.connect(ready)

def ready():
    def got(value):
        print("RESULT " + str(value), flush=True)
        os._exit(0)     # a normal exit can hang tearing down QtWebEngine
    w.page().runJavaScript(SCRIPT, got)

def checked(webgl):
    if not webgl:
        print("NO-WEBGL", flush=True)
        os._exit(0)
    probe.deleteLater()
    load_map()

probe = PyQt6.QtWebEngineWidgets.QWebEngineView()
probe.loadFinished.connect(lambda _ok: probe.page().runJavaScript(WEBGL, checked))
probe.setHtml("<html></html>")
QTimer.singleShot(30000, lambda: os._exit(1))
app.exec()
"""


def _run_on_map_page(script: str, attempts: int = 3) -> dict:
    """Load the map, wait for map_ready, run `script` (an expression giving a
    JSON string) and return its parsed value.

    Headless Chromium now and then loses its GPU context while the page
    starts ("Context lost during MakeCurrent") and the page never gets
    ready -- about one run in twenty under pytest, independent of STORM's
    code -- so a run that never reports is retried in a fresh process. So
    is one whose script threw (None): the widget's stall fallback reported
    ready although the map never loaded. With no WebGL at all (CI runners
    without a GPU) MapLibre can't run, and the test is skipped."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for _attempt in range(attempts):
        out = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT), script], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=90)
        if "NO-WEBGL" in out.stdout.splitlines():
            pytest.skip("no WebGL here, so the map can't load")
        lines = [line for line in out.stdout.splitlines() if line.startswith("RESULT ")]
        if lines and lines[-1] != "RESULT None":
            return json.loads(lines[-1][len("RESULT "):])
    raise AssertionError(f"the map page never answered in {attempts} tries (last exit {out.returncode})\n"
                         f"{out.stdout[-2000:]}\n{out.stderr[-2000:]}")


def test_map_ready_means_storm_sources_exist():
    # Python hears "ready" only once STORM's sources exist, without waiting
    # for basemap tiles (MapLibre's "load")
    assert _run_on_map_page(
        "JSON.stringify({setup: _stormSetupDone, cwa: !!map.getSource('cwa'),"
        " measure: !!map.getSource('measure-line'), loaded: window._stormMapLoaded})"
    ) == {"setup": True, "cwa": True, "measure": True, "loaded": True}


def test_annotations_hide_including_ones_added_while_hidden():
    result = _run_on_map_page("""(function() {
      var line = JSON.stringify({drawing_type: 'polyline', title: 'a',
                                 coordinates: [[35, -97], [36, -96]]});
      function vis(id) { return map.getLayoutProperty('drawing-line-' + id, 'visibility') || 'visible'; }
      stormAddDrawing('before', line);
      stormSetAnnotationsVisible(false);
      stormAddDrawing('during', line);
      var hidden = [vis('before'), vis('during')];
      stormSetAnnotationsVisible(true);
      return JSON.stringify({hidden: hidden, shown: [vis('before'), vis('during')]});
    })()""")
    assert result == {"hidden": ["none", "none"], "shown": ["visible", "visible"]}
