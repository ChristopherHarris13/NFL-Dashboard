"""Load the cached player seed and build the simulated roster.

The parquet cache is produced by build_seed.py (nfl_data_py). At runtime we
only need pyarrow to read it, so services boot fully offline. The roster is
every active player on SEED_TEAM (capped) plus `extra_players` random others,
sampled deterministically from GLOBAL_SEED so all five services pick the same
players.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .settings import Settings
from .simulation import rng_for


@dataclass(frozen=True)
class Player:
    gsis_id: str
    nfl_id: int
    espn_id: int
    full_name: str
    first_name: str
    last_name: str
    team: str
    position: str
    weight_lbs: float
    height_in: float


def _load_rows(seed_path: Path) -> list[dict]:
    if seed_path.exists():
        import pyarrow.parquet as pq

        return pq.read_table(seed_path).to_pylist()
    fallback = seed_path.with_name("players_fallback.json")
    if fallback.exists():
        return json.loads(fallback.read_text())
    raise FileNotFoundError(
        f"No player seed at {seed_path}. Run mock_vendors/common/build_seed.py first."
    )


def load_all_players(seed_path: Path) -> list[Player]:
    players = []
    for row in _load_rows(seed_path):
        players.append(
            Player(
                gsis_id=str(row["gsis_id"]),
                nfl_id=int(row["nfl_id"]),
                espn_id=int(row["espn_id"]),
                full_name=str(row["full_name"]),
                first_name=str(row["first_name"]),
                last_name=str(row["last_name"]),
                team=str(row["team"]),
                position=str(row["position"]),
                weight_lbs=float(row["weight_lbs"]),
                height_in=float(row["height_in"]),
            )
        )
    return players


def build_roster(settings: Settings) -> list[Player]:
    everyone = sorted(load_all_players(settings.seed_path), key=lambda p: p.gsis_id)
    team = [p for p in everyone if p.team == settings.seed_team]
    others = [p for p in everyone if p.team != settings.seed_team]
    if not team:
        raise ValueError(f"No players found for SEED_TEAM={settings.seed_team}")

    rng = rng_for(settings.global_seed, "roster")
    if len(team) > settings.max_team_players:
        team = rng.sample(team, settings.max_team_players)
    extras = rng.sample(others, min(settings.extra_players, len(others)))
    roster = sorted(team + extras, key=lambda p: p.gsis_id)
    return roster
