"""Teamworks/Kitman-style daily wellness questionnaire mock.

Identity: integer nflId (renamed athlete_id after schema drift).
Timestamps: naive local strings ("2026-09-10 07:15:00", really facility ET).
Two landmines: the Likert scale silently changes 1-5 -> 1-10 on
SCALE_CHANGE_DATE, and on SCHEMA_DRIFT_DATE keys rename and the name splits
(schema v2, which also finally admits scale_max).

Soreness/fatigue react to the previous day's simulated Catapult load, so the
cross-source correlation is real.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from ..common import dirt_engine as dirt
from ..common.base_service import Pending, VendorApp
from ..common.dirt_engine import DirtConfig
from ..common.seed_loader import build_roster
from ..common.settings import Settings
from ..common.sim_clock import OFF, day_type
from ..common.simulation import rng_for, wellness_drivers

SERVICE = "ams_wellness_svc"


def _likert(value: float, scale_max: int, rng) -> int:
    """Map a 0..~4.5 driver onto 1..scale_max with noise."""
    if scale_max == 5:
        v = 1 + value + rng.uniform(-0.4, 0.4)
    else:
        v = 1 + value * 2 + rng.uniform(-0.8, 0.8)
    return int(max(1, min(scale_max, round(v))))


class WellnessGenerator:
    resources = ["surveys"]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.seed = settings.global_seed
        self.roster = build_roster(settings)
        self.dirt = DirtConfig.load(settings.dirt_config_path)

    def generate_day(self, day_index: int, d: date) -> list[Pending]:
        out: list[Pending] = []
        for player in self.roster:
            rng = rng_for(self.seed, SERVICE, player.gsis_id, d.toordinal())
            if rng.random() < self.dirt.p(SERVICE, "skipped_day"):
                continue

            drivers = wellness_drivers(self.seed, player.gsis_id, d)
            scale_max = 5 if d < self.settings.scale_change_date else 10
            submitted = datetime.combine(
                d, time(rng.randint(6, 8), rng.randint(0, 59), rng.randint(0, 59))
            )

            soreness = _likert(drivers["soreness"], scale_max, rng)
            fatigue = _likert(drivers["fatigue"], scale_max, rng)
            calm = max(0.0, 3.2 - drivers["fatigue"])  # good mood when fresh
            record = {
                "survey_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "player_id": player.nfl_id,
                "name": player.full_name,
                "submitted_at": self._render_ts(submitted, rng),
                "sleep_hours": round(rng.uniform(5.2, 9.5) - drivers["sleep_penalty"], 1),
                "sleep_quality": _likert(rng.uniform(1.0, 3.4), scale_max, rng),
                "soreness": soreness,
                "fatigue": fatigue,
                "stress": _likert(rng.uniform(0.4, 2.6), scale_max, rng),
                "mood": _likert(calm + rng.uniform(0, 1.2), scale_max, rng),
                "srpe": None if day_type(d) == OFF
                else int(max(0, min(10, round(drivers["today_load"] * 3.5 + rng.uniform(-1, 1))))),
            }

            versioned, _ = self._apply_drift(record, d, scale_max)
            out.append(Pending(day_index, (day_index, player.gsis_id, 0), "surveys", versioned))

            if rng.random() < self.dirt.p(SERVICE, "duplicate_submission"):
                dup = dict(record)
                dup["survey_id"] = str(uuid.UUID(int=rng.getrandbits(128), version=4))
                resub = submitted + timedelta(minutes=rng.randint(2, 20))
                dup["submitted_at"] = self._render_ts(resub, rng)
                for f in rng.sample(["soreness", "fatigue", "sleep_quality", "stress"], rng.randint(1, 2)):
                    delta = rng.choice([-1, 1])
                    dup[f] = int(max(1, min(scale_max, record[f] + delta)))
                dup_versioned, _ = self._apply_drift(dup, d, scale_max)
                out.append(Pending(day_index, (day_index, player.gsis_id, 1), "surveys", dup_versioned))
        return out

    def _render_ts(self, ts: datetime, rng) -> str:
        if rng.random() < self.dirt.p(SERVICE, "wrong_tz"):
            return dirt.mangle_timestamp(ts, "naive_local_wrong_tz", rng)
        return dirt.mangle_timestamp(ts, "naive_local", rng)

    def _apply_drift(self, record: dict, d: date, scale_max: int):
        drifted, version = dirt.schema_drift(
            record, self.settings.schema_drift_date, d,
            rename_map={"player_id": "athlete_id"},
            split_fields={"name": ("first_name", "last_name")},
        )
        if version == "v2":
            drifted["scale_max"] = scale_max
        return drifted, version


def schema_version(vendor: VendorApp) -> str:
    return "v2" if vendor.current_sim_date() >= vendor.settings.schema_drift_date else "v1"
