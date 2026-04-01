# network/topic_cache.py
# Persists known retained MQTT topic IDs to disk.
#
# AWS IoT Core only delivers retained messages to exact-topic subscriptions,
# not wildcards.  This cache stores the IDs seen on each prefix so that on
# reconnect we can subscribe to each exact topic and receive its retained msg.

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent.parent / ".mqtt_topic_cache.json"


def _load() -> dict[str, list[str]]:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict[str, list[str]]) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(data))
    except Exception as e:
        log.warning("topic_cache: save failed: %s", e)


def get_topics(prefix: str) -> list[str]:
    """Return all cached exact topics for a given prefix."""
    return _load().get(prefix, [])


def add_topic(prefix: str, topic: str) -> None:
    data = _load()
    topics = data.setdefault(prefix, [])
    if topic not in topics:
        topics.append(topic)
        _save(data)


def remove_topic(prefix: str, topic: str) -> None:
    data = _load()
    topics = data.get(prefix, [])
    if topic in topics:
        topics.remove(topic)
        data[prefix] = topics
        _save(data)
