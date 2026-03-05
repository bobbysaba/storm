# core/vehicle.py
# Current state of a tracked vehicle, including its latest observation.

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.observation import Observation


@dataclass
class Vehicle:
    id:         str
    lat:        float
    lon:        float
    color:      str = "#FF6B35"
    latest_obs: "Observation | None" = None
