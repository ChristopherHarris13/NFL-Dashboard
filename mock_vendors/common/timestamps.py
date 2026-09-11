"""Timestamp format defects shared by every service.

Styles: ``iso_utc`` (``2024-09-01T13:00:00Z``), ``iso_offset``
(``2024-09-01T09:00:00-04:00``), ``epoch_s``, ``epoch_ms``, ``naive_local``
(``2024-09-01 09:00:00`` in the team's local zone, no offset), ``us_date``
(``09/01/2024``), ``us_datetime`` (``09/01/2024 09:00``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .dirt import Dirt

STYLES = ("iso_utc", "iso_offset", "epoch_s", "epoch_ms", "naive_local", "us_date", "us_datetime")


def format_ts(dt: datetime, style: str, tz: str = "America/New_York") -> str | int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    local = dt.astimezone(ZoneInfo(tz))
    if style == "iso_utc":
        return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if style == "iso_offset":
        return local.isoformat(timespec="seconds")
    if style == "epoch_s":
        return int(utc.timestamp())
    if style == "epoch_ms":
        return int(utc.timestamp() * 1000)
    if style == "naive_local":
        return local.strftime("%Y-%m-%d %H:%M:%S")
    if style == "us_date":
        return local.strftime("%m/%d/%Y")
    if style == "us_datetime":
        return local.strftime("%m/%d/%Y %H:%M")
    raise ValueError(f"unknown timestamp style {style!r}")


def dirty_ts(dt: datetime, dirt: Dirt, default_style: str, tz: str = "America/New_York") -> str | int:
    """Format ``dt`` in the service's default style, occasionally in a wrong one."""
    style = default_style
    if dirt.roll_p(float(dirt.timestamps_cfg.get("format_swap_prob", 0.0))):
        style = dirt.choice([s for s in STYLES if s != default_style])
    return format_ts(dt, style, tz)
