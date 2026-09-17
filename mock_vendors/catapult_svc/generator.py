"""Catapult GPS/LPS mock: practice and game load sessions.

Identity: vendor UUIDs (cat_<8 hex>); the gsis mapping is internal and
never exposed. Like the real OpenField API, /v1/athletes lists the account's
athletes by name (no league id) — emitted once, on day 0 — so a pipeline can
pin each vendor id to a player by name. Timestamps: ISO 8601 UTC with Z.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from ..common import dirt_engine as dirt
from ..common.base_service import Pending
from ..common.dirt_engine import DirtConfig
from ..common.seed_loader import build_roster
from ..common.settings import Settings
from ..common.sim_clock import GAME, OFF, PRACTICE, WALKTHROUGH, day_type
from ..common.simulation import daily_load, position_factor, rng_for

SERVICE = "catapult_svc"

_DIST_RANGE = {PRACTICE: (3000, 7000), GAME: (5000, 9000), WALKTHROUGH: (800, 2000)}
_BASE_LOAD = {PRACTICE: 1.0, GAME: 1.45, WALKTHROUGH: 0.38}


class CatapultGenerator:
    resources = ["sessions", "athletes"]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.seed = settings.global_seed
        self.roster = build_roster(settings)
        self.dirt = DirtConfig.load(settings.dirt_config_path)
        # Internal-only mapping; deliberately never emitted.
        self.vendor_ids = {
            p.gsis_id: "cat_" + rng_for(self.seed, "cat_id", p.gsis_id).getrandbits(32).to_bytes(4, "big").hex()
            for p in self.roster
        }

    def generate_day(self, day_index: int, d: date) -> list[Pending]:
        out: list[Pending] = []
        if day_index == 0:
            out.extend(self._athletes(day_index))
        dt = day_type(d)
        if dt == OFF:
            return out
        for player in self.roster:
            rng = rng_for(self.seed, SERVICE, player.gsis_id, d.toordinal())
            if rng.random() < self.dirt.p(SERVICE, "missing_session"):
                continue  # silently absent player-day

            load = daily_load(self.seed, player.gsis_id, d)
            rel = max(0.4, min(1.7, load / _BASE_LOAD[dt]))
            lo, hi = _DIST_RANGE[dt]
            total = (lo + (hi - lo) * (rel - 0.4) / 1.3) * position_factor(player.position)
            total = round(max(lo * 0.7, min(hi * 1.3, total)), 1)
            hsd = round(total * rng.uniform(0.05, 0.20), 1)

            hour = {PRACTICE: rng.randint(14, 17), WALKTHROUGH: 15, GAME: rng.choice([17, 20])}[dt]
            ts = datetime.combine(d, time(hour, rng.randint(0, 59)), tzinfo=timezone.utc)

            pos_f = position_factor(player.position)
            record = {
                "session_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "player_id": self.vendor_ids[player.gsis_id],
                "session_ts": dirt.mangle_timestamp(ts, "iso_z", rng),
                "session_type": dt,
                "duration_min": {
                    PRACTICE: rng.randint(75, 130),
                    WALKTHROUGH: rng.randint(30, 55),
                    GAME: rng.randint(120, 150),
                }[dt],
                "total_distance": total,
                "high_speed_distance": hsd,
                "sprint_count": min(40, max(0, int(rng.gauss(14, 7) * pos_f))),
                "accel_count": min(80, max(10, int(rng.gauss(40, 12)))),
                "decel_count": min(80, max(10, int(rng.gauss(38, 12)))),
                "player_load": round(min(900.0, max(200.0, 200 + total * 0.075 + rng.uniform(-40, 40))), 1),
                "max_speed": round(min(10.5, max(6.0, 6.2 + 4.0 * (pos_f - 0.80) / 0.38 + rng.uniform(-0.2, 0.3))), 2),
            }

            # --- dirt ---
            record["total_distance"] = dirt.outlier(
                record["total_distance"], self.dirt.p(SERVICE, "outlier_distance"),
                rng, (30000, 50000),
            )
            dist_val, dist_label = dirt.swap_unit(
                record["total_distance"], "m", "yd",
                self.dirt.p(SERVICE, "unit_swap_distance"), rng,
                wrong_label_p=0.4, omit_label_p=0.3,
            )
            if dist_val != record["total_distance"]:
                record["high_speed_distance"] = round(record["high_speed_distance"] / dirt.M_PER_YD, 1)
            record["total_distance"] = dist_val
            dirt.put_unit(record, "distance_unit", dist_label)

            speed_val, speed_label = dirt.swap_unit(
                record["max_speed"], "m/s", "mph",
                self.dirt.p(SERVICE, "unit_swap_speed"), rng,
                wrong_label_p=0.4, omit_label_p=0.3,
            )
            record["max_speed"] = speed_val
            dirt.put_unit(record, "speed_unit", speed_label)

            record = dirt.null_field(record, "player_load",
                                     self.dirt.p(SERVICE, "null_player_load"), rng)

            late = dirt.late_emit_days(
                self.dirt.p(SERVICE, "late_emit"),
                int(self.dirt.opt(SERVICE, "late_emit", "max_days", 3)), rng,
            )
            # Field order: keep units next to their values for readability.
            record = _ordered(record)
            out.append(Pending(
                emit_day=day_index + late,
                sort_key=(day_index, hour, player.gsis_id),
                resource="sessions",
                record=record,
            ))
        return out

    def _athletes(self, day_index: int) -> list[Pending]:
        """The account's athlete list, as Catapult's athlete export shows it."""
        out = []
        for player in self.roster:
            rng = rng_for(self.seed, SERVICE, "athlete", player.gsis_id)
            record = {
                "athlete_id": self.vendor_ids[player.gsis_id],
                "first_name": player.first_name,
                "last_name": player.last_name,
                "jersey": rng.randint(1, 99),
                "position_name": player.position,
            }
            out.append(Pending(
                emit_day=day_index, sort_key=(day_index, 0, player.gsis_id),
                resource="athletes", record=record,
            ))
        return out


_FIELD_ORDER = [
    "session_id", "player_id", "session_ts", "session_type", "duration_min",
    "total_distance", "high_speed_distance", "distance_unit", "sprint_count",
    "accel_count", "decel_count", "player_load", "max_speed", "speed_unit",
]


def _ordered(record: dict) -> dict:
    return {k: record[k] for k in _FIELD_ORDER if k in record}
