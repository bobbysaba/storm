"""NSSL is a provider like any team's: STORM reaches it only through the
provider hooks (settings/code_providers.py), so it runs without it."""
import ast
import subprocess
import sys

import settings

ROOT = settings.PROJECT_DIR
# what may import NSSL's package: itself, its tests
_ALLOWED = ("providers/nssl/", "tests/")
_SKIPPED = (".claude/", ".git/", "headless/build/")


def _imports_nssl(path) -> bool:
    tree = ast.parse(path.read_text("utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if any(n == "providers.nssl" or n.startswith("providers.nssl.") for n in names):
            return True
    return False


def test_nothing_outside_the_provider_imports_it():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(_ALLOWED + _SKIPPED):
            continue
        if _imports_nssl(path):
            offenders.append(rel)
    assert offenders == []


def test_storm_imports_and_finds_no_hooks_without_the_provider():
    """With providers.nssl unimportable, every app module still imports and
    no hook answers."""
    code = """
import importlib, pkgutil, sys

class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "providers.nssl" or name.startswith("providers.nssl."):
            raise ImportError("blocked: " + name)

sys.meta_path.insert(0, Block())
import main, settings
import ui.app
for mod in pkgutil.iter_modules(ui.app.__path__):
    importlib.import_module("ui.app." + mod.name)
from settings import code_providers
s = settings.load(settings.PROJECT_DIR / "storm.example.toml")
# only the built-in NSSL provider, which fails to import here
assert not s.error and [(p.name, p.builtin) for p in s.code_providers] == [("nssl", True)], s.error
hooks = ("live_sounding_fetcher",
         "archive_sounding_fetcher", "archive_wind_profiles", "archive_surface_playback",
         "archive_lidar")
print([h for h in hooks if code_providers.first_hook(s, h) is not None])
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
