"""
When shared map items expire.  Annotations, drawings and scan sectors last
until the team's daily reset, ``daily_reset_utc`` in storm.toml's [mqtt]
table (an hour, UTC; default 8), so a day's items clear between field days.
"""

from datetime import datetime, timedelta, timezone


def seconds_until_reset(hour_utc: int, now: datetime | None = None) -> int:
    """Seconds until the next *hour_utc*:00 UTC: today if it's still ahead,
    else tomorrow."""
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))
