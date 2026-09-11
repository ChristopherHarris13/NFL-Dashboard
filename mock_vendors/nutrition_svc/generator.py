"""Notemeal / DEXA-style body composition export.

Shape (one record per measurement)::

    record_id, client_id (usually null -- this vendor goes by name), client_name, measured_on
    (MM/DD/YYYY), weight, weight_unit (kg | lbs), body_fat_pct, lean_mass, hydration_pct, method
    (DEXA | BIA | scale)

Dirt: weight in kg or lbs depending on which scale was used, sparse and
irregular measurement dates, BIA reading body fat a few points higher than
DEXA for the same athlete, and impossible values (a 600 lb weigh-in, 0 % fat).
"""
from __future__ import annotations

from datetime import date

from common.dirt import Dirt
from common.identity import player_ref
from common.roster import load_roster
from common.schedule import hours_later, local_dt, position_group
from common.store import Record
from common.timestamps import dirty_ts

SERVICE = "nutrition"
VENDOR = "Notemeal"

KG_PER_LB = 0.45359237
BODY_FAT = {"speed": 9.5, "skill": 13.0, "specialist": 16.0, "line": 24.0}
METHODS = ("scale", "scale", "scale", "BIA", "BIA", "DEXA")


def generate_day(day: date) -> list[Record]:
    dirt = Dirt(SERVICE, day)
    cfg = dirt.cfg
    p_measure = 1.0 / float(cfg.get("mean_days_between_measurements", 6))
    bias = float(cfg.get("bia_body_fat_bias_pct", 3.5))
    records: list[Record] = []

    for player in load_roster():
        if not dirt.roll_p(p_measure):
            continue
        method = dirt.choice(METHODS)
        ts = local_dt(day, dirt.randint(6, 8), dirt.randint(0, 59), player)
        true_kg = player.weight_lb * KG_PER_LB * dirt.gauss(1.0, 0.012)
        true_bf = dirt.gauss(BODY_FAT[position_group(player.position)], 1.2, 4.0, 40.0)

        weight, unit = true_kg, "kg"
        if dirt.roll("weight_in_lbs_prob"):
            weight, unit = true_kg / KG_PER_LB, "lbs"

        if method == "scale":
            bf, lean, hydration = None, None, None
        else:
            bf = true_bf + (bias + dirt.gauss(0, 1.0) if method == "BIA" else dirt.gauss(0, 0.4))
            lean = true_kg * (1 - bf / 100)
            hydration = dirt.gauss(60.0 if method == "BIA" else 58.0, 2.5, 45, 75)

        payload = {
            "record_id": dirt.int_id("NM", 8),
            **player_ref(player, SERVICE, dirt, cfg.get("default_ref_style", "name"), "client_id", "client_name"),
            "measured_on": dirty_ts(ts, dirt, cfg.get("default_ts_style", "us_date"), player.tz),
            "weight": round(weight, 1),
            "weight_unit": unit,
            "body_fat_pct": None if bf is None else round(bf, 1),
            "lean_mass": None if lean is None else round(lean, 1),
            "hydration_pct": None if hydration is None else round(hydration, 1),
            "method": method,
        }

        if dirt.roll("impossible_value_prob"):
            what = dirt.choice(["weight_600", "weight_zero", "bf_zero", "bf_negative", "hydration_150"])
            if what == "weight_600":
                payload["weight"], payload["weight_unit"] = 600.0, "lbs"
            elif what == "weight_zero":
                payload["weight"] = 0.0
            elif what == "bf_zero":
                payload["body_fat_pct"] = 0.0
            elif what == "bf_negative":
                payload["body_fat_pct"] = -2.4
            else:
                payload["hydration_pct"] = 150.0

        records.append(Record(payload=payload, event_ts=ts, available_at=hours_later(ts, dirt.uniform(2, 30))))
    return records
