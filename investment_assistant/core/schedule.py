"""Daily run time projection; the actual trigger remains the external scheduler."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def next_run_at(
    now: datetime,
    *,
    weekdays: tuple[int, ...],
    hour: int,
    minute: int,
    tz: str = "Asia/Shanghai",
) -> datetime:
    """Return the first scheduled local time strictly after an aware ``now``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(ZoneInfo(tz))
    for offset in range(8):
        candidate = (local_now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.isoweekday() in weekdays and candidate > local_now:
            return candidate
    raise ValueError("weekdays must be a non-empty subset of 1..7")
