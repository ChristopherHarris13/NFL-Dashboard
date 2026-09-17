"""Environment-driven configuration shared by all five mock vendors."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

COMMON_DIR = Path(__file__).resolve().parent
DEFAULT_SEED_PATH = COMMON_DIR / "seed" / "players.parquet"
DEFAULT_DIRT_CONFIG = COMMON_DIR.parent / "dirt_config.yaml"


def _env_date(name: str, default: str) -> date:
    return date.fromisoformat(os.environ.get(name, default))


@dataclass
class Settings:
    service_name: str
    port: int
    gen_interval_seconds: int = 300
    backfill_days: int = 45
    sim_start_date: date = field(default_factory=lambda: date(2026, 7, 20))
    schema_drift_date: date = field(default_factory=lambda: date(2026, 9, 1))
    scale_change_date: date = field(default_factory=lambda: date(2026, 8, 25))
    dirt_config_path: Path = DEFAULT_DIRT_CONFIG
    seed_path: Path = DEFAULT_SEED_PATH
    seed_team: str = "KC"
    extra_players: int = 20
    max_team_players: int = 55
    # One seed drives every service's RNG. Same seed + same sim day =>
    # identical simulated world in all five containers, which is what makes
    # the cross-service correlations (load -> soreness -> injury) line up.
    global_seed: str = "gridironops"
    run_background_loop: bool = True

    @classmethod
    def from_env(cls, service_name: str, default_port: int) -> "Settings":
        return cls(
            service_name=service_name,
            port=int(os.environ.get("PORT", default_port)),
            gen_interval_seconds=int(os.environ.get("GEN_INTERVAL_SECONDS", 300)),
            backfill_days=int(os.environ.get("BACKFILL_DAYS", 45)),
            sim_start_date=_env_date("SIM_START_DATE", "2026-07-20"),
            schema_drift_date=_env_date("SCHEMA_DRIFT_DATE", "2026-09-01"),
            scale_change_date=_env_date("SCALE_CHANGE_DATE", "2026-08-25"),
            dirt_config_path=Path(os.environ.get("DIRT_CONFIG_PATH", DEFAULT_DIRT_CONFIG)),
            seed_path=Path(os.environ.get("SEED_PATH", DEFAULT_SEED_PATH)),
            seed_team=os.environ.get("SEED_TEAM", "NE"),
            global_seed=os.environ.get("GLOBAL_SEED", "gridironops"),
        )
