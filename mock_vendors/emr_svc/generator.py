"""Team EMR shaped like an NFL injury report.

Shape (one record per status update)::

    record_id, injury_id, mrn (usually null), player_name (with Jr./III as the EMR spells it),
    team, event_date (MM/DD/YYYY), status_updated_at (ISO UTC), body_part (free text), side,
    injury_type, practice_status (DNP | LP | FP), game_designation (Out | Doubtful |
    Questionable | null), rtp_date (MM/DD/YYYY or null)

Dirt: free-text body parts ("Ham", "left hamstring", "Hamstring (L)"), status
updates that arrive out of order, corrections that re-send an existing
record_id with different values and no version marker, and players identified
by name only.
"""
from __future__ import annotations

from datetime import date, timedelta

from common.dirt import Dirt
from common.identity import player_ref
from common.roster import load_roster
from common.schedule import hours_later, local_dt
from common.store import Record
from common.timestamps import dirty_ts, format_ts

SERVICE = "emr"
VENDOR = "TeamEMR"

INJURY_TYPES = {
    "hamstring": ["strain", "strain", "tear"],
    "knee": ["sprain", "MCL sprain", "contusion", "meniscus"],
    "ankle": ["sprain", "high ankle sprain", "contusion"],
    "shoulder": ["sprain", "AC sprain", "subluxation"],
    "groin": ["strain", "strain"],
    "calf": ["strain", "contusion"],
    "foot": ["sprain", "plantar fasciitis", "contusion"],
    "concussion": ["concussion"],
    "back": ["spasm", "strain"],
    "quad": ["strain", "contusion"],
    "hip": ["flexor strain", "contusion"],
    "illness": ["illness", "flu-like"],
}
NO_SIDE = {"concussion", "back", "illness"}
SIDES = {"L": ["L", "Left", "left", "L"], "R": ["R", "Right", "right", "R"]}


def _designation(days_left: int, dirt: Dirt) -> str | None:
    if days_left > 6:
        return "Out"
    if days_left > 3:
        return "Doubtful"
    if days_left > 0:
        return "Questionable" if dirt.roll_p(0.8) else "Doubtful"
    return None


def generate_day(day: date) -> list[Record]:
    dirt = Dirt(SERVICE, day)
    cfg = dirt.cfg
    p_daily = float(cfg.get("injury_prob_per_player_week", 0.06)) / 7.0
    body_parts: dict[str, list[str]] = cfg.get("body_parts", {})
    records: list[Record] = []

    for player in load_roster():
        if not dirt.roll_p(p_daily):
            continue
        canonical = dirt.choice(list(body_parts))
        injury_type = dirt.choice(INJURY_TYPES.get(canonical, ["strain"]))
        side_key = None if canonical in NO_SIDE else dirt.choice(["L", "R"])
        days_out = int(dirt.gauss(8, 7, 1, 42))
        injury_id = dirt.int_id("INJ", 7)
        onset = local_dt(day, dirt.randint(11, 17), dirt.randint(0, 59), player)
        rtp = day + timedelta(days=days_out)
        ref = player_ref(player, SERVICE, dirt, cfg.get("default_ref_style", "name"), "mrn", "player_name")
        if ref.get("mrn") is None and dirt.roll("mrn_present_prob"):
            ref["mrn"] = player.vendor_id(SERVICE)

        # status timeline: onset, then an update every 1-3 days until return to play
        updates: list[tuple[int, str]] = [(0, "DNP")]
        d = 0
        while True:
            d += dirt.randint(1, 3)
            if d >= days_out:
                updates.append((days_out, "FP"))
                break
            frac = d / days_out
            updates.append((d, "DNP" if frac < 0.45 else "LP" if frac < 0.8 else "FP"))

        prev_record = None
        for offset, practice_status in updates:
            upd_day = day + timedelta(days=offset)
            upd_ts = hours_later(onset, offset * 24 + dirt.uniform(-2, 6)) if offset else onset
            days_left = days_out - offset
            side_txt = None if side_key is None else dirt.choice(SIDES[side_key])
            body_txt = dirt.choice(body_parts[canonical])
            if side_key and dirt.roll_p(0.3):
                body_txt = dirt.choice([f"{side_txt} {body_txt}", f"{body_txt} ({side_key})"])
                side_txt = None if dirt.roll_p(0.5) else side_txt
            payload = {
                "record_id": dirt.int_id("R", 9),
                "injury_id": injury_id,
                **ref,
                "team": player.team,
                "event_date": dirty_ts(onset, dirt, cfg.get("default_ts_style", "us_date"), player.tz),
                "status_updated_at": format_ts(upd_ts, "iso_utc"),
                "body_part": body_txt,
                "side": side_txt,
                "injury_type": injury_type,
                "practice_status": practice_status,
                "game_designation": _designation(days_left, dirt),
                "rtp_date": format_ts(local_dt(rtp, 9, 0, player), "us_date", player.tz) if days_left <= 0 else None,
            }
            available = hours_later(upd_ts, dirt.uniform(0.5, 12))
            if prev_record is not None and dirt.roll("out_of_order_prob"):
                # the previous update was posted late, so this one hits the feed first
                prev_record.available_at = hours_later(available, dirt.uniform(0.5, 24))
            rec = Record(payload=payload, event_ts=upd_ts, available_at=available)
            records.append(rec)
            prev_record = rec

            if dirt.roll("correction_prob"):
                # clinician fixes the note: same record_id, different body part / side, no flag
                fixed = dict(payload)
                fixed["body_part"] = dirt.choice(body_parts[canonical])
                if side_key:
                    fixed["side"] = dirt.choice(SIDES["L" if side_key == "R" else "R"]) if dirt.roll_p(0.3) else dirt.choice(SIDES[side_key])
                if dirt.roll_p(0.3):
                    fixed["injury_type"] = dirt.choice(INJURY_TYPES.get(canonical, ["strain"]))
                records.append(Record(payload=fixed, event_ts=upd_ts, available_at=hours_later(available, dirt.uniform(1, 48))))
    return records
