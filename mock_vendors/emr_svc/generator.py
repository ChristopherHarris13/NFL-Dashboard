"""Team EMR mock, shaped like the public NFL injury/practice report.

Identity: 'Last, First' names with suffixes preserved ('Beckham Jr., Odell').
Timestamps: ISO 8601 with US/Eastern offset. Two resources: /v1/injuries and
/v1/status_updates.

New injuries are driven by the shared simulation: risk multiplies when the
player's ACWR spiked past 1.5 in the prior week or force-plate asymmetry
exceeds 10, so Gold-layer charts show a real signal.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

from ..common.base_service import Pending
from ..common.dirt_engine import DirtConfig
from ..common.seed_loader import Player, build_roster
from ..common.settings import Settings
from ..common.sim_clock import GAME, PRACTICE, day_type
from ..common.simulation import injury_on, rng_for

SERVICE = "emr_svc"

BODY_PART_VOCAB = {
    "hamstring": ["Hamstring", "hamstring", "Ham", "hammy"],
    "knee": ["Knee", "knee", "MCL"],
    "ankle": ["Ankle", "ankle", "high ankle"],
    "shoulder": ["Shoulder", "AC joint"],
    "concussion": ["Concussion", "concussion protocol", "head"],
}
_SIDED = {"hamstring", "knee", "ankle", "shoulder"}
_SUFFIX_RE = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$")


def _eastern(d: date, t: time) -> datetime:
    offset = -4 if 3 <= d.month <= 10 else -5
    return datetime.combine(d, t, tzinfo=timezone(timedelta(hours=offset)))


def _iso_eastern(dt: datetime) -> str:
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


def _emr_name(player: Player) -> str:
    m = _SUFFIX_RE.search(player.full_name)
    suffix = f" {m.group(1)}" if m else ""
    last = _SUFFIX_RE.sub("", player.last_name)
    return f"{last}{suffix}, {player.first_name}"


def _mangle_emr_name(name: str, p: float, rng) -> str:
    if rng.random() >= p:
        return name
    move = rng.choice(["strip_suffix", "first_last", "upper", "no_periods"])
    if move == "strip_suffix":
        return re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV|V),", ",", name)
    if move == "first_last":
        last, _, first = name.partition(", ")
        return f"{first} {last}".strip()
    if move == "upper":
        return name.upper()
    return name.replace(".", "")


class EmrGenerator:
    resources = ["injuries", "status_updates"]

    def __init__(self, settings: Settings):
        self.settings = settings
        self.seed = settings.global_seed
        self.roster = build_roster(settings)
        self.dirt = DirtConfig.load(settings.dirt_config_path)
        self.active: list[dict] = []  # sequential-day state; deterministic

    # ----------------------------------------------------------- emission

    def generate_day(self, day_index: int, d: date) -> list[Pending]:
        out: list[Pending] = []
        self.active = [inj for inj in self.active if inj["end_day"] >= day_index]
        injured_ids = {inj["gsis_id"] for inj in self.active}

        if day_type(d) in (PRACTICE, GAME):
            for player in self.roster:
                if player.gsis_id in injured_ids:
                    continue
                spec = injury_on(self.seed, player.gsis_id, d, self.settings.sim_start_date)
                if spec:
                    out.append(self._open_injury(player, spec, day_index, d))

        if day_type(d) == PRACTICE:
            for inj in self.active:
                if inj["event_day"] == day_index:
                    continue  # injury opened today; first report tomorrow
                out.extend(self._status_update(inj, day_index, d))
        return out

    # ------------------------------------------------------------ injuries

    def _open_injury(self, player: Player, spec: dict, day_index: int, d: date) -> Pending:
        rng = rng_for(self.seed, SERVICE, "open", player.gsis_id, d.toordinal())
        injury_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        duration = spec["duration_days"]
        expected = d + timedelta(days=duration)
        if rng.random() < self.dirt.p(SERVICE, "stale_expected_rtp"):
            expected = d + timedelta(days=max(1, duration // 3))  # stale: too optimistic
        name = _mangle_emr_name(_emr_name(player), self.dirt.p(SERVICE, "name_mangle"), rng)

        body_part, side = self._body_part(spec, rng)
        record = {
            "injury_id": injury_id,
            "player": name,
            "team": player.team,
            "event_date": _iso_eastern(_eastern(d, time(rng.randint(10, 15), rng.randint(0, 59)))),
            "body_part": body_part,
            "side": side,
            "injury_type": spec["injury_type"],
            "severity": spec["severity"],
            "expected_rtp": expected.isoformat() if rng.random() > 0.25 else None,
            "opened_at": _iso_eastern(_eastern(d, time(rng.randint(16, 19), rng.randint(0, 59)))),
        }
        if side is _DROPPED:
            del record["side"]

        self.active.append({
            "injury_id": injury_id,
            "gsis_id": player.gsis_id,
            "player_name": name,
            "event_day": day_index,
            "end_day": day_index + duration,
            "duration": duration,
            "severity": spec["severity"],
            "body_part_key": spec["body_part"],
        })
        return Pending(day_index, (day_index, 0, player.gsis_id), "injuries", record)

    def _body_part(self, spec: dict, rng):
        """Returns (body_part_text, side) where side may be the _DROPPED
        sentinel when it got embedded in the free text instead."""
        key = spec["body_part"]
        side = spec["side"]
        if rng.random() >= self.dirt.p(SERVICE, "free_text_body_part"):
            return key, side  # clean: canonical vocabulary, side in its field
        variant = rng.choice(BODY_PART_VOCAB[key])
        if key in _SIDED and side and rng.random() < self.dirt.p(SERVICE, "side_embedded"):
            style = rng.choice(["paren", "prefix", "word"])
            if style == "paren":
                variant = f"{variant} ({side})"
            elif style == "prefix":
                variant = f"{side} {variant.lower()}"
            else:
                variant = f"{'left' if side == 'L' else 'right'} {variant.lower()}"
            return variant, _DROPPED
        return variant, side

    # ------------------------------------------------------ status updates

    def _status_update(self, inj: dict, day_index: int, d: date) -> list[Pending]:
        rng = rng_for(self.seed, SERVICE, "update", inj["injury_id"], d.toordinal())
        frac = (day_index - inj["event_day"]) / max(1, inj["duration"])
        practice_status = "DNP" if frac < 0.4 else ("LP" if frac < 0.75 else "FP")

        game_status = None
        if d.weekday() == 4:  # Friday report sets game status
            if practice_status == "DNP":
                game_status = "Out" if inj["severity"] != "minor" else "Doubtful"
            elif practice_status == "LP":
                game_status = rng.choice(["Questionable", "Doubtful"])

        note = None
        if rng.random() < 0.3:
            note = rng.choice([
                f"limited in team periods ({inj['body_part_key']})",
                "rehab on side field",
                "re-evaluated by staff",
                "progressing",
                None,
            ])

        record = {
            "update_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
            "injury_id": inj["injury_id"],
            "player": inj["player_name"],
            "practice_status": practice_status,
            "game_status": game_status,
            "note": note,
            "updated_at": _iso_eastern(_eastern(d, time(rng.randint(16, 18), rng.randint(0, 59)))),
        }

        emit_day = day_index
        # Out-of-order: delay Wednesday's update past Thursday's.
        if d.weekday() == 2 and rng.random() < self.dirt.p(SERVICE, "out_of_order_updates"):
            emit_day = day_index + 2
        out = [Pending(emit_day, (day_index, 1, inj["injury_id"]), "status_updates", record)]

        if rng.random() < self.dirt.p(SERVICE, "correction_overwrite"):
            corrected = dict(record)
            order = ["DNP", "LP", "FP"]
            idx = order.index(practice_status)
            corrected["practice_status"] = order[max(0, idx - 1)] if idx > 0 else "LP"
            corrected["updated_at"] = _iso_eastern(
                _eastern(d + timedelta(days=1), time(9, rng.randint(0, 59))))
            out.append(Pending(day_index + 1, (day_index, 2, inj["injury_id"]),
                               "status_updates", corrected))
        return out


_DROPPED = object()
