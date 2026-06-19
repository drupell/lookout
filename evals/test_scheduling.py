"""Unit tests for the shared next-run computation (no AWS, <1s)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.config.loader import ScheduleConfig
from src.scheduling import next_run_after

# 2026-05-25 is a Monday, 2026-05-28 a Thursday.
MON_9 = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
THU_9 = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)


class TestNextRunAfter:
    def test_default_is_next_thursday_1300(self):
        nr = next_run_after(ScheduleConfig(), MON_9)  # default [3] @ 13:00 UTC
        assert nr.weekday() == 3
        assert (nr.hour, nr.minute) == (13, 0)
        assert nr > MON_9

    def test_target_day_before_time_is_today(self):
        nr = next_run_after(ScheduleConfig(), THU_9)  # Thu 09:00, run at 13:00
        assert nr.date() == THU_9.date()
        assert nr.hour == 13

    def test_target_day_after_time_rolls_a_week(self):
        now = THU_9.replace(hour=14)  # Thu 14:00, past the 13:00 slot
        nr = next_run_after(ScheduleConfig(), now)
        assert nr.weekday() == 3
        assert (nr - now).days >= 6  # next Thursday, not today

    def test_multiple_days_picks_the_soonest(self):
        sched = ScheduleConfig(days_of_week=[0, 3], time_of_day_utc="13:00")  # Mon & Thu
        nr = next_run_after(sched, MON_9.replace(hour=14))  # Mon past its slot
        assert nr.weekday() == 3
        assert nr.date() == THU_9.date()

    def test_every_day_is_tomorrow_when_today_passed(self):
        sched = ScheduleConfig(days_of_week=[0, 1, 2, 3, 4, 5, 6], time_of_day_utc="08:00")
        nr = next_run_after(sched, MON_9)  # Mon 09:00, past 08:00
        assert nr.date() == datetime(2026, 5, 26, tzinfo=UTC).date()  # tomorrow
        assert nr.hour == 8

    def test_advance_from_a_fired_run_gives_the_following_run(self):
        # The scheduler advances by calling this with the slot it just fired.
        sched = ScheduleConfig(days_of_week=[3], time_of_day_utc="13:00")
        fired = datetime(2026, 5, 28, 13, 0, tzinfo=UTC)  # Thursday's slot
        assert next_run_after(sched, fired) == datetime(2026, 6, 4, 13, 0, tzinfo=UTC)

    def test_days_are_normalized(self):
        assert ScheduleConfig(days_of_week=[3, 0, 3]).days_of_week == [0, 3]

    def test_empty_or_out_of_range_days_rejected(self):
        with pytest.raises(ValidationError):
            ScheduleConfig(days_of_week=[])
        with pytest.raises(ValidationError):
            ScheduleConfig(days_of_week=[7])
