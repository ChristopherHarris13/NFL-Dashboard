"""Dietitian body-composition export mock (Notemeal-ish).

Identity: NAME ONLY, heavily mangled on purpose (AJ vs A.J., nicknames,
Jr./III appearing and vanishing, occasional Last-First). Timestamps:
date only, MM/DD/YYYY. Weekly Wednesday weigh-ins, monthly DEXA, irregular
BIA 1-2x/month. Weights are ALWAYS measured in lb; only the unit label
gets mangled (says 'kg' or goes missing), never the value.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time

from ..common import dirt_engine as dirt
from ..common.base_service import Pending
from ..common.dirt_engine import DirtConfig
from ..common.seed_loader import Player, build_roster
from ..common.settings import Settings
from ..common.sim_clock import PRACTICE, day_type
from ..common.simulation import rng_for

SERVICE = "nutrition_svc"

_SKILL = {"WR", "CB", "S", "FS", "SS", "DB", "RB"}
_BIG = {"T", "G", "C", "OL", "OT", "OG", "DT", "NT", "DL", "DE"}


def _bf_range(position: str) -> tuple[float, float]:
    if position in _SKILL:
        return 8.0, 14.0
    if position in _BIG:
        return 18.0, 28.0
    return 12.0, 18.0


class NutritionGenerator:
    resources = ["measurements"]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.seed = settings.global_seed
        self.roster = build_roster(settings)
        self.dirt = DirtConfig.load(settings.dirt_config_path)

    # ------------------------------------------------------------ modeling

    def _weight_on(self, player: Player, d: date) -> float:
        base = max(158.0, min(390.0, player.weight_lbs))
        phase = rng_for(self.seed, "wt_phase", player.gsis_id).uniform(0, 130)
        drift = 6.0 * math.sin(2 * math.pi * (d.toordinal() + phase) / 130)
        noise = rng_for(self.seed, "wt", player.gsis_id, d.toordinal()).uniform(-1.2, 1.2)
        return round(max(156.0, min(400.0, base + drift + noise)), 1)

    def _bf_on(self, player: Player, d: date, method: str) -> float:
        lo, hi = _bf_range(player.position)
        anchor = rng_for(self.seed, "bf_base", player.gsis_id).uniform(lo + 1, hi - 1)
        season_drift = -0.8 * min(1.0, (d - self.settings.sim_start_date).days / 90)
        noise = rng_for(self.seed, "bf", player.gsis_id, d.toordinal(), method).uniform(-0.7, 0.7)
        return round(max(lo, min(hi, anchor + season_drift + noise)), 1)

    def _dexa_day(self, player: Player, d: date) -> bool:
        month = (d.year, d.month)
        rng = rng_for(self.seed, "dexa", player.gsis_id, month)
        # A deterministic practice day-of-month per player per month.
        candidates = [dom for dom in range(1, 29)
                      if day_type(date(d.year, d.month, dom)) == PRACTICE]
        return bool(candidates) and d.day == rng.choice(candidates)

    def _bia_day(self, player: Player, d: date) -> bool:
        month = (d.year, d.month)
        rng = rng_for(self.seed, "bia", player.gsis_id, month)
        candidates = [dom for dom in range(1, 29)
                      if day_type(date(d.year, d.month, dom)) == PRACTICE]
        if not candidates:
            return False
        picks = rng.sample(candidates, min(len(candidates), rng.randint(1, 2)))
        return d.day in picks

    # ----------------------------------------------------------- emission

    def generate_day(self, day_index: int, d: date) -> list[Pending]:
        out: list[Pending] = []
        for player in self.roster:
            rng = rng_for(self.seed, SERVICE, player.gsis_id, d.toordinal())
            methods: list[str] = []
            if d.weekday() == 2:  # Wednesday team weigh-in
                if rng.random() >= self.dirt.p(SERVICE, "sparse"):
                    methods.append("scale")
            if self._dexa_day(player, d):
                methods.append("DEXA")
                if rng.random() < self.dirt.p(SERVICE, "method_disagreement"):
                    methods.append("BIA")  # same-day disagreement
            elif self._bia_day(player, d):
                methods.append("BIA")

            for i, method in enumerate(methods):
                record = self._record(player, d, method, rng,
                                      disagree=(method == "BIA" and "DEXA" in methods))
                out.append(Pending(day_index, (day_index, player.gsis_id, i),
                                   "measurements", record))
        return out

    def _record(self, player: Player, d: date, method: str, rng,
                disagree: bool) -> dict:
        weight = self._weight_on(player, d)
        if method == "scale":
            bf = None
            est_bf = self._bf_on(player, d, "DEXA")
            lean = round(weight * (1 - est_bf / 100), 1)
        else:
            bf = self._bf_on(player, d, method)
            if disagree:
                # Offset from the same-day DEXA reading so the two methods
                # reliably disagree by 3-6 points.
                dexa_bf = self._bf_on(player, d, "DEXA")
                bf = round(max(4.5, min(31.5, dexa_bf + rng.choice([-1, 1]) * rng.uniform(3, 6))), 1)
            lean = round(weight * (1 - bf / 100), 1)

        record = {
            "measurement_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "player_name": dirt.mangle_name(player.full_name,
                                            self.dirt.p(SERVICE, "name_mangle"), rng),
            "team": player.team,
            "measured_on": dirt.mangle_timestamp(
                datetime.combine(d, time(7, 30)), "date_us", rng),
            "method": method,
            "weight": weight,
            "body_fat_pct": bf,
            "lean_mass": lean,
            "hydration_status": (
                rng.choice(["hydrated", "hydrated", "mild", "dehydrated", None])
                if method == "BIA" else None
            ),
        }

        # --- dirt ---
        if rng.random() < self.dirt.p(SERVICE, "outlier"):
            move = rng.choice(["w600", "w45", "bf"])
            if move == "w600":
                record["weight"] = 600.0
            elif move == "w45":
                record["weight"] = 45.0
            elif record["body_fat_pct"] is not None:
                record["body_fat_pct"] = 1.5

        wt_label = dirt.mislabel_unit(
            "lb", "kg",
            self.dirt.p(SERVICE, "weight_unit_mislabel"), rng,
            wrong_label_p=0.30, omit_label_p=0.40,
        )
        dirt.put_unit(record, "weight_unit", wt_label)

        if rng.random() < self.dirt.p(SERVICE, "lean_mass_ambiguity"):
            # Percent instead of absolute, with no indicator. You're welcome.
            base_bf = record["body_fat_pct"] if record["body_fat_pct"] else 15.0
            record["lean_mass"] = round(100 - base_bf, 1)

        return _ordered(record)


_FIELD_ORDER = [
    "measurement_id", "player_name", "team", "measured_on", "method",
    "weight", "weight_unit", "body_fat_pct", "lean_mass", "hydration_status",
]


def _ordered(record: dict) -> dict:
    return {k: record[k] for k in _FIELD_ORDER if k in record}
