"""The recorder (archive/recorder.py) writes the files archive/history.py reads."""
import json
from datetime import datetime, timezone

from archive import history
from archive.fetchers.mqtt_reader import _parse_jsonl
from archive.recorder import Recorder

T = datetime(2026, 5, 6, 23, 59, tzinfo=timezone.utc)
NEXT = datetime(2026, 5, 7, 0, 1, tzinfo=timezone.utc)


def _cone(cone_id="3f9a1c2e"):
    return json.dumps({"id": cone_id, "created_at": "2026-05-06T23:59:00+00:00"}).encode()


def test_messages_go_to_the_days_file_for_their_kind(tmp_path):
    rec = Recorder(tmp_path, "storm")
    assert rec.handle("storm/cones/3f9a1c2e", _cone(), now=T)
    assert rec.handle("storm/cones/3f9a1c2f", _cone("3f9a1c2f"), now=NEXT)
    rec.close()
    assert (tmp_path / "storm.cones.20260506").read_text() == _cone().decode() + "\n"
    assert json.loads((tmp_path / "storm.cones.20260507").read_text())["id"] == "3f9a1c2f"
    assert rec.count == 2


def test_retained_copies_other_topics_and_junk_are_skipped(tmp_path):
    rec = Recorder(tmp_path, "storm")
    assert not rec.handle("storm/cones/3f9a1c2e", _cone(), retained=True, now=T)
    assert not rec.handle("other/cones/3f9a1c2e", _cone(), now=T)
    assert not rec.handle("storm/cones/a/b", _cone(), now=T)
    assert not rec.handle("storm/cones/3f9a1c2e", b"not json", now=T)
    assert not rec.handle("storm/cones/3f9a1c2e", b"[1, 2]", now=T)
    assert not rec.handle("storm/vehicles/wx1", b"", now=T)
    rec.close()
    assert list(tmp_path.iterdir()) == []


def test_an_empty_payload_is_written_as_the_delete_it_means(tmp_path):
    rec = Recorder(tmp_path, "storm")
    rec.handle("storm/annotations/3f9a1c2e", b"", now=T)
    rec.handle("storm/scan_sectors/wx1", b"", now=T)
    rec.close()
    (_, ann), = _parse_jsonl((tmp_path / "storm.annotations.20260506").read_text())
    assert ann == {"id": "3f9a1c2e", "deleted": True, "deleted_at": T.isoformat()}
    (_, sector), = _parse_jsonl((tmp_path / "storm.scan_sectors.20260506").read_text())
    assert sector["vehicle_id"] == "wx1" and sector["active"] is False


def test_a_multi_line_payload_becomes_one_line(tmp_path):
    rec = Recorder(tmp_path, "storm")
    rec.handle("storm/cones/3f9a1c2e", b'{\n "id": "3f9a1c2e"\n}', now=T)
    rec.close()
    assert (tmp_path / "storm.cones.20260506").read_text() == '{"id": "3f9a1c2e"}\n'


def test_a_nested_root_names_files_with_dots_and_reads_back(tmp_path):
    rec = Recorder(tmp_path, "team/storm")
    rec.handle("team/storm/cones/3f9a1c2e", _cone(), now=T)
    rec.close()
    source = history.FolderSource(tmp_path, "team.storm")
    assert history.fetch_text([source], "cones", "20260506")[0] == _cone().decode() + "\n"


def test_the_mqtt_client_hands_every_message_to_its_taps():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace
    from network.mqtt_client import MQTTClient

    client = MQTTClient()
    seen = []
    tap = lambda *args: seen.append(args)
    client.add_tap(tap)
    client._on_message(None, None, SimpleNamespace(topic="storm/cones/x", payload=b"{}", retain=True))
    client.remove_tap(tap)
    client._on_message(None, None, SimpleNamespace(topic="storm/cones/y", payload=b"{}", retain=False))
    assert seen == [("storm/cones/x", b"{}", True)]
