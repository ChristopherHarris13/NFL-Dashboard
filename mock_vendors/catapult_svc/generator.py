"""Catapult-style GPS load sessions.

Shape (one record per athlete per session)::

    session_id, athlete_id (CAT-xxxxxxxx), athlete_name, team, session_start (ISO UTC),
    session_type, duration_s, total_distance, high_speed_distance, sprint_count,
    accel_count, decel_count, player_load, max_velocity, distance_unit, velocity_unit, device_id

Dirt: distances sometimes in yards, velocity sometimes in mph, the unit fields
sometimes missing entirely, sensor spikes (10x distance / impossible speed),
late-arriving sessions (event time days before they show up on the feed) and
sessions that simply never arrive.
"""
from __future__ import annotations

from datetime import date

from common.dirt import Dirt
from common.identity import player_ref
from common.roster import load_roster
from common.schedule import hours_later, local_dt, minutes_later, position_group
from common.store import Record
from common.timestamps import dirty_ts

SERVICE = "catapult"
VENDOR = "Catapult OpenField"

# (mean total distance m, high-speed share, mean max velocity m/s)
PROFILE = {
    "speed": (5200.0, 0.12, 9.1),
    "skill": (4300.0, 0.08, 8.6),
    "specialist": (2800.0, 0.02, 7.2),
    "line": (3100.0, 0.02, 7.0),
}

# weekday -> (session type, distance multiplier, probability multiplier)
WEEK = {
    0: ("Recovery", 0.35, 0.5),
    1: (None, 0.0, 0.0),        # Tuesday off
    2: ("Practice", 1.0, 1.4),
    3: ("Practice", 1.05, 1.4),
    4: ("Practice", 0.8, 1.3),
    5: ("Walkthrough", 0.4, 1.0),
    6: ("Game", 1.35, 1.6),
}

M_TO_YD = 1.0936133
MS_TO_MPH = 2.2369363


def generate_day(day: date) -> list[Record]:
    dirt = Dirt(SERVICE, day)
    cfg = dirt.cfg
    session_type, dist_mult, prob_mult = WEEK[day.weekday()]
    records: list[Record] = []
    if session_type is None:
        return records
    base_p = float(cfg.get("session_prob_per_day", 0.5)) * prob_mult

    for player in load_roster():
        if not dirt.roll_p(min(base_p, 0.98)):
            continue
        if dirt.roll("missing_session_prob"):
            continue  # the vendor just never sends this one

        mean_dist, hs_share, mean_vmax = PROFILE[position_group(player.position)]
        start = local_dt(day, 13 if session_type == "Game" else 10, dirt.randint(0, 45), player)
        duration_s = int(dirt.gauss(5400 if session_type != "Walkthrough" else 2700, 600, 900, 9000))
        total_m = dirt.gauss(mean_dist * dist_mult, mean_dist * 0.18, 200)
        hs_m = total_m * max(0.0, dirt.gauss(hs_share, hs_share * 0.4))
        vmax = dirt.gauss(mean_vmax, 0.5, 4.0, 11.5)
        sprints = int(hs_m / 25 * dirt.uniform(0.7, 1.3))
        accels = int(total_m / 90 * dirt.uniform(0.7, 1.3))
        decels = int(accels * dirt.uniform(0.8, 1.1))
        player_load = total_m * dirt.uniform(0.085, 0.11)

        distance_unit, velocity_unit = "m", "m/s"
        total, hs = total_m, hs_m
        if dirt.roll("distance_in_yards_prob"):
            distance_unit = "yd"
            total, hs = total_m * M_TO_YD, hs_m * M_TO_YD
        if dirt.roll("velocity_in_mph_prob"):
            velocity_unit = "mph"
            vmax = vmax * MS_TO_MPH

        if dirt.roll("sensor_spike_prob"):
            if dirt.roll_p(0.5):
                total *= dirt.choice([10, 12, 100])      # unit bug / GPS drift
            else:
                vmax = dirt.choice([44.7, 99.9, 210.0])  # impossible speed

        payload = {
            "session_id": dirt.hex_id(16),
            **player_ref(player, SERVICE, dirt, cfg.get("default_ref_style", "vendor"), "athlete_id", "athlete_name"),
            "team": player.team,
            "session_start": dirty_ts(start, dirt, cfg.get("default_ts_style", "iso_utc"), player.tz),
            "session_type": session_type,
            "duration_s": duration_s,
            "total_distance": round(total, 1),
            "high_speed_distance": round(hs, 1),
            "sprint_count": sprints,
            "accel_count": accels,
            "decel_count": decels,
            "player_load": round(player_load, 1),
            "max_velocity": round(vmax, 2),
            "distance_unit": distance_unit,
            "velocity_unit": velocity_unit,
            "device_id": f"CT-{dirt.int_id(width=5)}",
        }
        if dirt.roll("unit_field_missing_prob"):
            payload.pop("distance_unit")
            payload.pop("velocity_unit")

        end = minutes_later(start, duration_s / 60)
        if dirt.roll("late_arrival_prob"):
            available = hours_later(end, dirt.uniform(6, float(cfg.get("late_arrival_max_hours", 72))))
        else:
            available = minutes_later(end, dirt.uniform(5, 90))
        records.append(Record(payload=payload, event_ts=start, available_at=available))
    return records
