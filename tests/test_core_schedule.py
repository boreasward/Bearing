"""Next daily run uses ISO weekdays and the configured display time zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from investment_assistant.core.schedule import next_run_at

SH = ZoneInfo("Asia/Shanghai")
WEEKDAYS = (2, 3, 4, 5, 6)


def test_saturday_after_run_goes_to_tuesday():
    now = datetime(2026, 11, 14, 11, 31, tzinfo=SH)
    assert next_run_at(now, weekdays=WEEKDAYS, hour=11, minute=30) == datetime(2026, 11, 17, 11, 30, tzinfo=SH)


@pytest.mark.parametrize("minute,expected_day", [(29, 10), (31, 11)])
def test_tuesday_before_and_after_the_minute(minute, expected_day):
    result = next_run_at(datetime(2026, 11, 10, 11, minute, tzinfo=SH), weekdays=WEEKDAYS, hour=11, minute=30)
    assert result == datetime(2026, 11, expected_day, 11, 30, tzinfo=SH)


def test_exact_scheduled_minute_is_strictly_in_the_past():
    result = next_run_at(datetime(2026, 11, 10, 11, 30, tzinfo=SH), weekdays=WEEKDAYS, hour=11, minute=30)
    assert result == datetime(2026, 11, 11, 11, 30, tzinfo=SH)


def test_now_in_other_zone_is_converted():
    result = next_run_at(datetime(2026, 11, 10, 3, 31, tzinfo=UTC), weekdays=WEEKDAYS, hour=11, minute=30)
    assert result == datetime(2026, 11, 11, 11, 30, tzinfo=SH)
    assert result.tzinfo == SH


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="^now must be timezone-aware$"):
        next_run_at(datetime(2026, 11, 10, 11, 30), weekdays=WEEKDAYS, hour=11, minute=30)


def test_single_weekday_and_explicit_timezone():
    result = next_run_at(datetime(2026, 11, 9, 11, 30, tzinfo=UTC), weekdays=(1,), hour=11, minute=30, tz="UTC")
    assert result == datetime(2026, 11, 16, 11, 30, tzinfo=UTC)
    assert result.tzinfo == ZoneInfo("UTC")
