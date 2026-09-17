"""Deterministic shared world model.

Every service computes the same per-player, per-day quantities from the same
GLOBAL_SEED, without any cross-service communication:

    daily_load(seed, player, date)  -> how hard the player worked that day
    acwr(seed, player, date)        -> 7-day / 28-day load ratio
    asymmetry(seed, player, date)   -> force-plate L/R asymmetry, with a few
                                       players drifting toward +12 over time

catapult_svc scales its GPS numbers off daily_load; ams_wellness_svc makes
soreness/fatigue track the previous day's load; emr_svc makes new injuries
more likely when ACWR spiked or asymmetry is high. Because all of it is a
pure function of (seed, player, date), the correlations line up across
containers and survive restarts.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta
from functools import lru_cache

from .sim_clock import GAME, OFF, PRACTICE, WALKTHROUGH, day_type


def rng_for(seed: str, *parts) -> random.Random:
    key = "|".join([seed, *[str(p) for p in parts]])
    digest = hashlib.sha256(key.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# Position archetypes: skill players cover more ground at higher speeds,
# linemen less. Used for distance/speed scaling everywhere.
_HIGH = {"WR", "CB", "S", "FS", "SS", "DB", "RB"}
_LOW = {"T", "G", "C", "OL", "OT", "OG", "DT", "NT", "DL", "DE"}


def position_factor(position: str) -> float:
    if position in _HIGH:
        return 1.18
    if position in _LOW:
        return 0.80
    return 1.0


_DAY_BASE = {OFF: 0.0, PRACTICE: 1.0, WALKTHROUGH: 0.38, GAME: 1.45}


@lru_cache(maxsize=200_000)
def daily_load(seed: str, gsis_id: str, d: date) -> float:
    """Unitless load, ~0.3..2.5 on active days, 0 on off days."""
    base = _DAY_BASE[day_type(d)]
    if base == 0.0:
        return 0.0
    # Some player-weeks spike (ramping back from a modified program, extra
    # conditioning, etc.). This is what pushes ACWR past 1.5 for a few players.
    week = d.isocalendar()[:2]
    spike_rng = rng_for(seed, "spike", gsis_id, week)
    spike = 2.2 if spike_rng.random() < 0.08 else 1.0
    # A few quiet weeks too, so the chronic average moves.
    quiet = 0.45 if spike_rng.random() < 0.08 else 1.0
    noise = rng_for(seed, "load", gsis_id, d.toordinal()).uniform(0.88, 1.12)
    return base * spike * quiet * noise


@lru_cache(maxsize=200_000)
def _window_mean(seed: str, gsis_id: str, end: date, days: int) -> float:
    total = 0.0
    for i in range(days):
        total += daily_load(seed, gsis_id, end - timedelta(days=i))
    return total / days


def acwr(seed: str, gsis_id: str, d: date) -> float:
    """Acute (7d) : chronic (28d) workload ratio, ending at d inclusive."""
    chronic = _window_mean(seed, gsis_id, d, 28)
    if chronic < 1e-6:
        return 1.0
    return _window_mean(seed, gsis_id, d, 7) / chronic


def is_asymmetry_drifter(seed: str, gsis_id: str) -> bool:
    return rng_for(seed, "drifter", gsis_id).random() < 0.08


def asymmetry(seed: str, gsis_id: str, d: date, season_start: date) -> float:
    """Percent asymmetry. Drifters ramp from ~0 toward +12 over ~10 weeks."""
    rng = rng_for(seed, "asym", gsis_id, d.toordinal())
    base = rng.gauss(0, 2.6)
    if is_asymmetry_drifter(seed, gsis_id):
        weeks = max(0.0, (d - season_start).days / 7)
        base += min(12.0, weeks * 1.3) + rng.gauss(0, 1.0)
    return max(-15.0, min(15.0, base))


def wellness_drivers(seed: str, gsis_id: str, d: date) -> dict:
    """Soreness/fatigue drivers for day d, reacting to *yesterday's* load."""
    yesterday = d - timedelta(days=1)
    load = daily_load(seed, gsis_id, yesterday)
    # Map load 0..2.5 onto an added 0..~3 points of soreness/fatigue.
    bump = min(3.0, load * 1.25)
    rng = rng_for(seed, "wellness", gsis_id, d.toordinal())
    return {
        "soreness": bump + rng.uniform(0, 1.4),
        "fatigue": bump * 0.9 + rng.uniform(0, 1.6),
        "sleep_penalty": 0.5 if load > 1.6 else 0.0,
        "today_load": daily_load(seed, gsis_id, d),
    }


BODY_PARTS = ["hamstring", "knee", "ankle", "shoulder", "concussion"]
INJURY_TYPES = {
    "hamstring": "strain",
    "knee": "sprain",
    "ankle": "sprain",
    "shoulder": "contusion",
    "concussion": "concussion",
}
SEVERITY_DAYS = {"minor": (4, 9), "moderate": (10, 21), "major": (22, 45)}


def injury_on(seed: str, gsis_id: str, d: date, season_start: date) -> dict | None:
    """Deterministically decide whether `gsis_id` gets injured on day d.

    Base risk is low; it multiplies up when last week's ACWR spiked past 1.5
    or force-plate asymmetry exceeds 10, so Gold-layer ACWR/asymmetry charts
    show a real signal. Roster-wide this lands around 1-3 new injuries/week.
    """
    if day_type(d) not in (PRACTICE, GAME):
        return None
    p = 0.006 if day_type(d) == PRACTICE else 0.015
    prior_week = d - timedelta(days=3)
    if acwr(seed, gsis_id, prior_week) > 1.5:
        p *= 6.0
    if asymmetry(seed, gsis_id, d, season_start) > 10:
        p *= 4.0
    rng = rng_for(seed, "injury", gsis_id, d.toordinal())
    if rng.random() >= p:
        return None
    body_part = rng.choices(BODY_PARTS, weights=[30, 22, 22, 16, 10])[0]
    severity = rng.choices(["minor", "moderate", "major"], weights=[55, 33, 12])[0]
    lo, hi = SEVERITY_DAYS[severity]
    return {
        "body_part": body_part,
        "injury_type": INJURY_TYPES[body_part] if rng.random() > 0.12 else "illness",
        "severity": severity,
        "duration_days": rng.randint(lo, hi),
        "side": rng.choice(["L", "R"]) if body_part != "concussion" else None,
    }
