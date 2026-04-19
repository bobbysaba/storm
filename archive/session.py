
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ArchiveSession:
    """
    Immutable configuration for an archive replay session.

    Attributes
    ----------
    start_time : datetime
        UTC datetime at which the replay begins.
    radar_station : str | None
        NEXRAD 4-letter station ID (e.g. "KTLX").  None until the first
        vehicle position is found and the nearest station is resolved.
    """

    start_time: datetime
    radar_station: Optional[str] = None

    def __post_init__(self):
        # normalise to UTC so callers don't have to worry about tz.
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=timezone.utc)
        else:
            self.start_time = self.start_time.astimezone(timezone.utc)

    @property
    def date_str(self) -> str:
        """UTC date string YYYY-MM-DD."""
        return self.start_time.strftime("%Y-%m-%d")

    @property
    def date_compact(self) -> str:
        """UTC date string YYYYMMDD."""
        return self.start_time.strftime("%Y%m%d")
