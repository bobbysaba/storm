"""scripts/check_broker.py reads the broker the way STORM does: STORM_CONFIG
for the file, .env for the passwords (through settings)."""
import importlib.util

import settings


def _script():
    spec = importlib.util.spec_from_file_location(
        "check_broker", settings.PROJECT_DIR / "scripts" / "check_broker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_it_reads_the_settings_file_storm_does(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "team.toml"
    cfg.write_text("[map]\nzoom = 6\n")
    monkeypatch.setenv("STORM_CONFIG", str(cfg))
    mod = _script()
    assert mod.main(["check_broker.py"]) == 1
    assert f"{cfg}: no [mqtt] host" in capsys.readouterr().out
    # importing settings is what loads .env's STORM_MQTT_PASSWORD
    assert mod.settings is settings
