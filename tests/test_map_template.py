import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "ui" / "map" / "map_template.html"


def test_template_starts_with_doctype():
    assert TEMPLATE.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


@pytest.mark.skipif(shutil.which("osascript") is None, reason="needs macOS JavaScriptCore")
def test_template_script_parses(tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", TEMPLATE.read_text(encoding="utf-8"), re.S)
    src = tmp_path / "map.js"
    src.write_text("\n".join(scripts), encoding="utf-8")
    js = (
        "ObjC.import('Foundation');"
        f"var s = $.NSString.stringWithContentsOfFileEncodingError('{src}', 4, null).js;"
        "try { new Function(s); 'OK' } catch (e) { 'ERR ' + e + ' line ' + e.line }"
    )
    out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True)
    assert out.stdout.strip() == "OK"
