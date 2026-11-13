"""setup.py brings an existing storm environment up to envs/storm.yml."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _setup_module():
    spec = importlib.util.spec_from_file_location("storm_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_environment_is_updated_not_skipped(monkeypatch):
    setup = _setup_module()
    calls = []
    monkeypatch.setattr(setup, "env_exists", lambda conda: True)
    monkeypatch.setattr(setup.subprocess, "check_call", lambda cmd: calls.append(cmd))
    setup.create_env("conda")
    assert calls == [["conda", "env", "update", "-n", "storm", "-f", setup.ENV_YML]]
    assert "--prune" not in calls[0]


def test_missing_environment_is_created(monkeypatch):
    setup = _setup_module()
    calls = []
    monkeypatch.setattr(setup, "env_exists", lambda conda: False)
    monkeypatch.setattr(setup.subprocess, "check_call", lambda cmd: calls.append(cmd))
    setup.create_env("conda")
    assert calls == [["conda", "env", "create", "-f", setup.ENV_YML]]
