"""Small helpers for turning a roster + calendar day into local event times."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .roster import Player


def local_dt(day: date, hour: int, minute: int, player: Player) -> datetime:
    """A tz-aware UTC datetime for ``hour:minute`` local time on ``day`` at the player's team."""
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(player.tz))
    return local.astimezone(timezone.utc)


def minutes_later(dt: datetime, minutes: float) -> datetime:
    return dt + timedelta(minutes=minutes)


def hours_later(dt: datetime, hours: float) -> datetime:
    return dt + timedelta(hours=hours)


def position_group(position: str) -> str:
    p = position.upper()
    if p in {"WR", "CB", "DB", "S", "FS", "SS"}:
        return "speed"
    if p in {"RB", "FB", "LB", "ILB", "OLB", "MLB", "TE"}:
        return "skill"
    if p in {"QB", "K", "P", "LS"}:
        return "specialist"
    return "line"  # OL, OT, OG, C, DL, DE, DT, NT, EDGE
