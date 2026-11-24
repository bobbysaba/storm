"""Annotations, drawings, cones and scan sectors are retained on the broker, so
a client that connects late gets the current items; deletes are retained too,
and an empty retained payload clears an item."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.annotation import Annotation
from core.drawing import DrawingAnnotation
from core.scan_sector import ScanSector
from core.storm_cone import StormCone
from network.annotation_sync import AnnotationSync
from network.drawing_sync import DrawingSync
from network.mqtt_client import MQTTClient
from network.scan_sector_sync import ScanSectorSync
from network.storm_cone_sync import StormConeSync


class _FakePaho:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=1, retain=False, properties=None):
        self.published.append((topic, json.loads(payload), retain))

    def subscribe(self, topic, **kwargs):
        pass


def _client():
    client = MQTTClient()
    client._client = paho = _FakePaho()
    return client, paho


def _items():
    ann = Annotation.new("flooded", 35.0, -97.0)
    drawing = DrawingAnnotation.from_dict({
        "id": "b41d07aa", "drawing_type": "dryline",
        "coordinates": [[-98.0, 36.0], [-97.5, 35.0]],
    })
    cone = StormCone.from_dict({"id": "c0ffee12", "lat": 35.5, "lon": -98.2,
                                "heading": 240, "speed_kts": 30})
    return [
        (AnnotationSync, ann, "annotations", "annotation_received", "annotation_deleted"),
        (DrawingSync, drawing, "drawings", "drawing_received", "drawing_deleted"),
        (StormConeSync, cone, "cones", "cone_received", "cone_deleted"),
    ]


@pytest.mark.parametrize("sync_cls,item,kind,_recv,_deleted", _items())
def test_items_and_their_deletes_are_retained(sync_cls, item, kind, _recv, _deleted):
    client, paho = _client()
    sync = sync_cls(client)
    sync.publish_create(item)
    sync.publish_update(item)
    sync.publish_delete(item.id)
    topic = f"storm/{kind}/{item.id}"
    assert [(t, r) for t, _, r in paho.published] == [(topic, True)] * 3
    assert paho.published[-1][1]["deleted"] is True


@pytest.mark.parametrize("sync_cls,item,kind,recv,deleted", _items())
def test_an_empty_retained_payload_deletes_the_item(sync_cls, item, kind, recv, deleted):
    client, _ = _client()
    sync = sync_cls(client)
    got, gone = [], []
    getattr(sync, recv).connect(got.append)
    getattr(sync, deleted).connect(lambda item_id, *_: gone.append(item_id))

    topic = f"storm/{kind}/{item.id}"
    client.message_received.emit(topic, json.dumps(item.to_dict()).encode())
    client.message_received.emit(topic, b"")
    assert [i.id for i in got] == [item.id]
    assert gone == [item.id]


def test_an_empty_retained_scan_sector_hides_it():
    client, _ = _client()
    sync = ScanSectorSync(client)
    got = []
    sync.scan_received.connect(got.append)
    client.message_received.emit("storm/scan_sectors/lidar1", b"")
    assert [(s.vehicle_id, s.active) for s in got] == [("lidar1", False)]


def test_scan_sectors_are_still_retained():
    client, paho = _client()
    ScanSectorSync(client).publish(ScanSector.from_dict({"vehicle_id": "lidar1"}))
    assert paho.published[0][0] == "storm/scan_sectors/lidar1"
    assert paho.published[0][2] is True


def test_each_vehicle_gets_an_exact_scan_sector_subscription():
    # AWS IoT Core only sends retained messages on exact-topic subscriptions
    client, _ = _client()
    subscribed = []
    client._client.subscribe = lambda topic, **kw: subscribed.append(topic)
    sync = ScanSectorSync(client)  # noqa: F841 (keep it alive)
    client.connected.emit()
    assert subscribed == ["storm/scan_sectors/+"]

    vehicle = json.dumps({"vehicle_id": "lidar1", "lat": 35, "lon": -97}).encode()
    client.message_received.emit("storm/vehicles/lidar1", vehicle)
    client.message_received.emit("storm/vehicles/lidar1", vehicle)
    assert subscribed[1:] == ["storm/scan_sectors/lidar1"]

    # a reconnect subscribes again, the vehicles it has heard from included
    subscribed.clear()
    client.connected.emit()
    assert subscribed == ["storm/scan_sectors/+", "storm/scan_sectors/lidar1"]
