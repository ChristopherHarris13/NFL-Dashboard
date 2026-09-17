"""Shared simulated calendar.

All five services derive the current simulated day from wall time the same
way, so any two containers booted on the same UTC day agree on the simulated
date for any given emission.

Day 0 is SIM_START_DATE. On boot a service backfills days 0..backfill_days-1,
and one further simulated day elapses every GEN_INTERVAL_SECONDS of wall time
(anchored to UTC midnight, or to SIM_WALL_EPOCH if set, so services that boot
minutes apart still land on the same simulated day).

Weekly rhythm (SIM_START_DATE defaults to a Monday):
    Mon/Tue  off
    Wed/Thu/Fri practice
    Sat      walkthrough
    Sun      game
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

from .settings import Settings

OFF = "off"
PRACTICE = "practice"
WALKTHROUGH = "walkthrough"
GAME = "game"


def day_type(d: date) -> str:
    wd = d.weekday()  # Monday == 0
    if wd in (0, 1):
        return OFF
    if wd in (2, 3, 4):
        return PRACTICE
    if wd == 5:
        return WALKTHROUGH
    return GAME


class SimClock:
    def __init__(self, settings: Settings):
        self.settings = settings
        epoch_env = os.environ.get("SIM_WALL_EPOCH")
        if epoch_env:
            self.wall_epoch = datetime.fromisoformat(epoch_env.replace("Z", "+00:00"))
        else:
            now = datetime.now(timezone.utc)
            self.wall_epoch = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def current_day_index(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        elapsed = max(0.0, (now - self.wall_epoch).total_seconds())
        return self.settings.backfill_days - 1 + int(elapsed // self.settings.gen_interval_seconds)

    def date_for(self, day_index: int) -> date:
        return self.settings.sim_start_date + timedelta(days=day_index)
