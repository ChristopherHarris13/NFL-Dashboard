"""Teamworks / Kitman-style daily wellness survey.

Shape (one record per submission)::

    submission_id, player_id (GSIS 00-00xxxxx) -> athlete_id after schema drift, player_name,
    submitted_at (naive local, sometimes ISO UTC), sleep_hours, sleep_quality, soreness,
    fatigue, stress, mood, rpe, session_duration_min, srpe_load, form_version

Dirt: skipped days, duplicate submissions, the Likert scale silently changing
from 1-5 to 1-10 part-way through the season (form v2), the identifier key
renamed ``player_id`` -> ``athlete_id`` later still (form v3), and timestamps
that are naive local time for most submissions but UTC for the rest.
"""
from __future__ import annotations

from datetime import date, timedelta

from common.config import load_config
from common.dirt import Dirt
from common.identity import player_ref
from common.roster import load_roster
from common.schedule import local_dt, minutes_later
from common.store import Record, utcnow
from common.timestamps import dirty_ts

SERVICE = "ams_wellness"
VENDOR = "Teamworks AMS"

LIKERT = ("sleep_quality", "soreness", "fatigue", "stress", "mood")


def _history_start() -> date:
    return utcnow().date() - timedelta(days=int(load_config()["history_days"]))


def form_version_for(day: date) -> str:
    cfg = load_config().get(SERVICE, {})
    offset = (day - _history_start()).days
    if offset >= int(cfg.get("schema_drift_day_offset", 42)):
        return "v3"
    if offset >= int(cfg.get("likert_switch_day_offset", 30)):
        return "v2"
    return "v1"


def current_form_version() -> str:
    return form_version_for(utcnow().date())


def _likert(dirt: Dirt, mu5: float, ten_point: bool) -> int:
    """A 1-5 score, or the same latent value expressed on 1-10 when the form changed."""
    v = dirt.gauss(mu5, 0.9, 1.0, 5.0)
    if ten_point:
        return int(round(v * 2))
    return int(round(v))


def _submission(player, day: date, dirt: Dirt, version: str, ts_dt, rpe: int, dur: int) -> dict:
    cfg = dirt.cfg
    ten = version in ("v2", "v3")
    id_field = "athlete_id" if version == "v3" else "player_id"
    ts_style = "iso_utc" if dirt.roll("utc_timestamp_prob") else cfg.get("default_ts_style", "naive_local")
    return {
        "submission_id": dirt.int_id("S", 9),
        **player_ref(player, "ams", dirt, cfg.get("default_ref_style", "gsis"), id_field, "player_name"),
        "submitted_at": dirty_ts(ts_dt, dirt, ts_style, player.tz),
        "sleep_hours": round(dirt.gauss(7.1, 1.1, 3.0, 11.0), 1),
        "sleep_quality": _likert(dirt, 3.6, ten),
        "soreness": _likert(dirt, 2.6, ten),
        "fatigue": _likert(dirt, 2.7, ten),
        "stress": _likert(dirt, 2.3, ten),
        "mood": _likert(dirt, 3.8, ten),
        "rpe": rpe,
        "session_duration_min": dur,
        "srpe_load": rpe * dur,
        "form_version": version,
    }


def generate_day(day: date) -> list[Record]:
    dirt = Dirt(SERVICE, day)
    version = form_version_for(day)
    records: list[Record] = []
    off_day = day.weekday() == 1

    for player in load_roster():
        if dirt.roll("skip_day_prob"):
            continue
        ts = local_dt(day, dirt.randint(6, 9), dirt.randint(0, 59), player)
        # sRPE refers to yesterday's session; off day -> zero load
        rpe = 0 if off_day else dirt.randint(3, 9)
        dur = 0 if off_day else int(dirt.gauss(80, 20, 20, 150))
        payload = _submission(player, day, dirt, version, ts, rpe, dur)
        records.append(Record(payload=payload, event_ts=ts, available_at=minutes_later(ts, dirt.uniform(1, 30))))

        if dirt.roll("duplicate_submission_prob"):
            # tapped submit twice / edited on a second device: new id, nearly identical answers
            ts2 = minutes_later(ts, dirt.uniform(0.5, 45))
            dup = _submission(player, day, dirt, version, ts2, rpe, dur)
            for k in ("sleep_hours", *LIKERT):
                dup[k] = payload[k]
            if dirt.roll_p(0.5):
                dup["soreness"] = payload["soreness"] + 1
            records.append(Record(payload=dup, event_ts=ts2, available_at=minutes_later(ts2, dirt.uniform(1, 30))))
    return records
