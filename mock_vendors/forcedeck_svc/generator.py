"""VALD ForceDecks mock: jump / isometric force-plate tests.

Identity: NFL GSIS id (00-00xxxxx). Timestamps: Unix epoch seconds.
Players test ~2x/week on practice days; CMJ 3 reps, IMTP 2, DJ 3.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from ..common import dirt_engine as dirt
from ..common.base_service import Pending
from ..common.dirt_engine import DirtConfig
from ..common.seed_loader import build_roster
from ..common.settings import Settings
from ..common.sim_clock import PRACTICE, day_type
from ..common.simulation import asymmetry, rng_for

SERVICE = "forcedeck_svc"

_REPS = {"CMJ": 3, "IMTP": 2, "DJ": 3}
_DEVICES = ["FD-1041", "FD-1042", "FD-1043"]


class ForcedeckGenerator:
    resources = ["tests"]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.seed = settings.global_seed
        self.roster = build_roster(settings)
        self.dirt = DirtConfig.load(settings.dirt_config_path)

    def _tests_today(self, gsis_id: str, d: date) -> bool:
        if day_type(d) != PRACTICE:
            return False
        week = d.isocalendar()[:2]
        chosen = rng_for(self.seed, "fd_days", gsis_id, week).sample([2, 3, 4], 2)
        return d.weekday() in chosen

    def generate_day(self, day_index: int, d: date) -> list[Pending]:
        out: list[Pending] = []
        for player in self.roster:
            if not self._tests_today(player.gsis_id, d):
                continue
            rng = rng_for(self.seed, SERVICE, player.gsis_id, d.toordinal())
            asym = asymmetry(self.seed, player.gsis_id, d, self.settings.sim_start_date)
            base_ts = datetime.combine(d, time(13, rng.randint(0, 45)), tzinfo=timezone.utc)
            seq = 0
            for test_type in ("CMJ", "IMTP", "DJ"):
                for rep in range(1, _REPS[test_type] + 1):
                    seq += 1
                    ts = base_ts.timestamp() + seq * 90
                    record = self._record(player, test_type, rep, int(ts), asym, rng)
                    copies = dirt.duplicate(
                        record, self.dirt.p(SERVICE, "duplicate"), rng,
                        reround_field="peak_force",
                    )
                    for i, copy in enumerate(copies):
                        out.append(Pending(
                            emit_day=day_index,
                            sort_key=(day_index, player.gsis_id, seq, i),
                            resource="tests",
                            record=copy,
                        ))
        return out

    def _record(self, player, test_type: str, rep: int, ts: int,
                asym: float, rng) -> dict:
        if test_type == "IMTP":
            jump = None
            peak = min(6000.0, max(3000.0, player.weight_lbs * rng.uniform(13.5, 16.5)))
        else:
            lo, hi = (30, 60) if test_type == "CMJ" else (25, 55)
            # Lighter, faster players jump higher.
            athletic = max(0.0, min(1.0, (280 - player.weight_lbs) / 130))
            jump = round(lo + (hi - lo) * (0.25 + 0.6 * athletic) * rng.uniform(0.8, 1.15), 1)
            jump = max(lo, min(hi, jump))
            peak = min(4500.0, max(2000.0, player.weight_lbs * rng.uniform(9.5, 12.5)))

        record = {
            "test_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "athlete_gsis_id": player.gsis_id,
            "test_ts": ts,
            "test_type": test_type,
            "rep": rep,
            "jump_height_cm": jump,
            "peak_force": round(peak, 1),
            "force_unit": "N",
            "rfd": round(rng.uniform(4000, 12000), 1),
            "asymmetry_pct": round(max(-15.0, min(15.0, asym + rng.gauss(0, 0.8))), 1),
            "device_id": rng.choice(_DEVICES),
        }

        # --- dirt ---
        if jump is not None and rng.random() < self.dirt.p(SERVICE, "aborted_rep"):
            record["jump_height_cm"] = round(
                rng.choice([rng.uniform(0.5, 5), rng.uniform(90, 140)]), 1)
        force_val, force_label = dirt.swap_unit(
            record["peak_force"], "N", "lbf",
            self.dirt.p(SERVICE, "unit_swap_force"), rng,
            wrong_label_p=0.25, omit_label_p=0.50,
        )
        record["peak_force"] = force_val
        dirt.put_unit(record, "force_unit", force_label)
        record = dirt.null_field(record, "test_type",
                                 self.dirt.p(SERVICE, "null_test_type"), rng)
        record = dirt.drop_field(record, "device_id",
                                 self.dirt.p(SERVICE, "missing_device"), rng)
        return record
