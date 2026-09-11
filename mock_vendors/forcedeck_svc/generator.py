"""VALD ForceDecks-style force plate tests.

Shape (one record per rep)::

    testId, athleteId (integer nflId), athleteName, recordedUTC (epoch seconds), testType
    (CMJ | IMTP | DJ), rep, jumpHeight (cm, CMJ/DJ only), peakForce (N), rfd100 (N/s),
    asymmetry (% , +ve = right dominant), deviceId, units {force, height}

Dirt: duplicate uploads (same testId/rep re-sent later), peak force in lbf
instead of N, the ``units`` object missing, and aborted reps left in the
export as outliers.
"""
from __future__ import annotations

from datetime import date

from common.dirt import Dirt
from common.identity import player_ref
from common.roster import load_roster
from common.schedule import local_dt, minutes_later, position_group
from common.store import Record
from common.timestamps import dirty_ts

SERVICE = "forcedeck"
VENDOR = "VALD ForceDecks"

N_TO_LBF = 0.2248089
TEST_TYPES = ("CMJ", "CMJ", "CMJ", "IMTP", "DJ")  # CMJ is the workhorse test

# jump height cm mean by position group
JUMP = {"speed": 44.0, "skill": 40.0, "specialist": 34.0, "line": 31.0}


def generate_day(day: date) -> list[Record]:
    dirt = Dirt(SERVICE, day)
    cfg = dirt.cfg
    records: list[Record] = []
    if day.weekday() in (1, 6):  # no testing Tuesday (off) or Sunday (game)
        return records
    reps = int(cfg.get("reps_per_test", 3))

    for player in load_roster():
        if not dirt.roll("test_prob_per_day", 0.25):
            continue
        test_type = dirt.choice(TEST_TYPES)
        start = local_dt(day, dirt.randint(7, 9), dirt.randint(0, 59), player)
        test_id = f"{dirt.hex_id(8)}-{dirt.hex_id(4)}-{dirt.hex_id(4)}-{dirt.hex_id(12)}"
        device = f"FD-{dirt.int_id(width=6)}"
        weight_n = player.weight_lb * 0.4536 * 9.81
        ref = player_ref(player, SERVICE, dirt, cfg.get("default_ref_style", "nfl_id"), "athleteId", "athleteName")
        base_jump = JUMP[position_group(player.position)]
        force_in_lbs = dirt.roll("force_in_lbs_prob")
        units_missing = dirt.roll("unit_field_missing_prob")

        for rep in range(1, reps + 1):
            ts = minutes_later(start, rep * dirt.uniform(1.0, 2.5))
            if test_type == "IMTP":
                jump = None
                peak = dirt.gauss(weight_n * 2.4, weight_n * 0.25, weight_n * 1.3)
            else:
                jump = dirt.gauss(base_jump, 3.0, 15.0, 60.0)
                peak = dirt.gauss(weight_n * 2.1, weight_n * 0.2, weight_n * 1.2)
            rfd = dirt.gauss(peak * 6.5, peak * 1.2, 500)
            asym = dirt.gauss(0.0, 5.5)

            if dirt.roll("aborted_rep_prob"):
                # athlete stepped off / mis-trigger; vendor still exports the rep
                jump = None if jump is None else round(dirt.uniform(0.5, 4.0), 1)
                peak = dirt.uniform(50, 400)
                rfd = dirt.uniform(0, 300)
                asym = dirt.choice([-97.0, 88.0, 0.0])

            payload = {
                "testId": test_id,
                **ref,
                "recordedUTC": dirty_ts(ts, dirt, cfg.get("default_ts_style", "epoch_s"), player.tz),
                "testType": test_type,
                "rep": rep,
                "jumpHeight": None if jump is None else round(jump, 1),
                "peakForce": round(peak * N_TO_LBF, 1) if force_in_lbs else round(peak, 1),
                "rfd100": round(rfd, 0),
                "asymmetry": round(asym, 1),
                "deviceId": device,
            }
            if not units_missing:
                payload["units"] = {"force": "lbf" if force_in_lbs else "N", "height": "cm"}

            available = minutes_later(ts, dirt.uniform(10, 240))
            records.append(Record(payload=payload, event_ts=ts, available_at=available))
            if dirt.roll("duplicate_upload_prob"):
                # the desktop app re-synced; identical row, later arrival
                records.append(Record(payload=dict(payload), event_ts=ts, available_at=minutes_later(available, dirt.uniform(30, 2880))))
    return records
