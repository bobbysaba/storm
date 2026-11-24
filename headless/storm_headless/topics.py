"""
MQTT topic names.  Every topic STORM uses sits under one root, ``topic_root``
in the [mqtt] table (default "storm"):

    <root>/vehicles/<vehicle_id>
    <root>/annotations/<annotation_id>
    <root>/drawings/<drawing_id>
    <root>/cones/<cone_id>
    <root>/scan_sectors/<vehicle_id>

The STORM GUI (network/topics.py) and the headless publisher both build
topics here.  Payloads are described in docs/wire-format.md.

Standard library only: this package runs on a Pi without the rest of STORM.
"""

DEFAULT_ROOT = "storm"

VEHICLES = "vehicles"
ANNOTATIONS = "annotations"
DRAWINGS = "drawings"
CONES = "cones"
SCAN_SECTORS = "scan_sectors"

KINDS = (VEHICLES, ANNOTATIONS, CONES, DRAWINGS, SCAN_SECTORS)


def validate_root(raw) -> str:
    """The root with surrounding slashes and spaces stripped.  Raises
    ValueError when it's empty, holds a wildcard, an empty level, or starts
    with "$" (reserved by brokers for system topics)."""
    root = str(raw).strip().strip("/")
    if not root:
        raise ValueError("topic_root may not be empty")
    if "+" in root or "#" in root:
        raise ValueError(f"topic_root may not contain MQTT wildcards (+ or #): {root!r}")
    if root.startswith("$"):
        raise ValueError(f"topic_root may not start with '$': {root!r}")
    if "" in root.split("/"):
        raise ValueError(f"topic_root may not contain an empty level ('//'): {root!r}")
    return root


def prefix(root: str, kind: str) -> str:
    """``<root>/<kind>``: the parent of every topic of one kind."""
    return f"{root}/{kind}"


def topic(root: str, kind: str, item_id: str) -> str:
    """The topic one item publishes on."""
    return f"{prefix(root, kind)}/{item_id}"


def subscription(root: str, kind: str) -> str:
    """The filter that receives every item of one kind."""
    return f"{prefix(root, kind)}/+"


def item_id(root: str, kind: str, topic_name: str) -> str | None:
    """The item ID at the end of *topic_name*, or None when it isn't a topic
    of this kind."""
    head = prefix(root, kind) + "/"
    if not topic_name.startswith(head):
        return None
    rest = topic_name[len(head):]
    return rest if rest and "/" not in rest else None
