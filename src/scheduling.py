"""Schedule math shared by the scheduler Lambda and the API.

Keeping the "when does this user next run?" computation in one place means the
scheduler (which enqueues due users) and the API (which shows the user their
next run) never disagree.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.loader import ScheduleConfig


def next_run_after(schedule: ScheduleConfig, after: datetime) -> datetime:
    """The next scheduled run strictly after `after` (timezone-aware UTC).

    Runs fire on each weekday in `schedule.days_of_week` (0=Mon … 6=Sun) at
    `time_of_day_utc`. This returns the soonest such moment after `after` — used
    both for a user's first run (after=now) and to advance to the following run
    (after=the run that just fired). So [3] gives weekly Thursdays, [0,3] gives
    Mondays & Thursdays, all seven gives daily.
    """
    hh, mm = (int(p) for p in schedule.time_of_day_utc.split(":", 1))
    days = set(schedule.days_of_week)
    # At most 7 days out we're guaranteed to hit a configured weekday whose
    # time is still ahead of `after`; offset 7 covers "today's slot already
    # passed" for a single-day schedule.
    for offset in range(8):
        candidate = (after + timedelta(days=offset)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        if candidate > after and candidate.weekday() in days:
            return candidate
    # Unreachable: ScheduleConfig guarantees days_of_week is non-empty.
    raise ValueError("schedule.days_of_week is empty")
