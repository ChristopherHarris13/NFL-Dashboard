"""Player roster used to seed every mock.

Names and IDs come from the real nflverse roster. A snapshot of four teams'
active rosters ships in ``data/roster_snapshot.csv`` so the services boot
offline; set ``roster.source: nflverse`` in dirt_config.yaml (and install
``nfl_data_py``) to pull a live roster instead.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import load_config

log = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "roster_snapshot.csv"

TEAM_TZ = {
    "ARI": "America/Phoenix", "ATL": "America/New_York", "BAL": "America/New_York",
    "BUF": "America/New_York", "CAR": "America/New_York", "CHI": "America/Chicago",
    "CIN": "America/New_York", "CLE": "America/New_York", "DAL": "America/Chicago",
    "DEN": "America/Denver", "DET": "America/Detroit", "GB": "America/Chicago",
    "HOU": "America/Chicago", "IND": "America/Indiana/Indianapolis", "JAX": "America/New_York",
    "KC": "America/Chicago", "LA": "America/Los_Angeles", "LAC": "America/Los_Angeles",
    "LV": "America/Los_Angeles", "MIA": "America/New_York", "MIN": "America/Chicago",
    "NE": "America/New_York", "NO": "America/Chicago", "NYG": "America/New_York",
    "NYJ": "America/New_York", "PHI": "America/New_York", "PIT": "America/New_York",
    "SEA": "America/Los_Angeles", "SF": "America/Los_Angeles", "TB": "America/New_York",
    "TEN": "America/Chicago", "WAS": "America/New_York",
}


@dataclass(frozen=True)
class Player:
    gsis_id: str          # "00-0030506"
    nfl_id: int           # NGS integer id, e.g. 40011
    esb_id: str
    pfr_id: str
    full_name: str        # as nflverse spells it, suffix included ("Antoine Winfield Jr.")
    first_name: str
    last_name: str
    football_name: str    # nflverse's "goes by" name ("Pat", "DJ")
    team: str
    position: str
    jersey_number: str
    birth_date: str
    height_in: int
    weight_lb: int

    @property
    def tz(self) -> str:
        return TEAM_TZ.get(self.team, "America/New_York")

    def vendor_id(self, vendor: str) -> str:
        """Stable, vendor-specific identifier that has nothing to do with GSIS/nflId."""
        digest = hashlib.sha1(f"{vendor}:{self.gsis_id}".encode()).hexdigest()
        if vendor == "catapult":
            return f"CAT-{digest[:8].upper()}"
        if vendor == "forcedeck":
            return str(uuid.UUID(digest[:32]))
        if vendor == "ams":
            return f"TW{int(digest[:6], 16) % 900000 + 100000}"
        if vendor == "nutrition":
            return f"NM-{int(digest[:5], 16) % 90000 + 10000}"
        if vendor == "emr":
            return f"MRN{int(digest[:7], 16) % 9000000 + 1000000}"
        return digest[:12]


def _from_rows(rows: list[dict]) -> list[Player]:
    players = []
    for r in rows:
        if not r.get("gsis_id") or not r.get("nfl_id"):
            continue
        players.append(
            Player(
                gsis_id=r["gsis_id"],
                nfl_id=int(float(r["nfl_id"])),
                esb_id=r.get("esb_id") or "",
                pfr_id=r.get("pfr_id") or "",
                full_name=r["full_name"],
                first_name=r.get("first_name") or r["full_name"].split(" ")[0],
                last_name=r.get("last_name") or r["full_name"].split(" ")[-1],
                football_name=r.get("football_name") or r.get("first_name") or "",
                team=r["team"],
                position=r.get("position") or "",
                jersey_number=str(r.get("jersey_number") or ""),
                birth_date=r.get("birth_date") or "",
                height_in=int(float(r.get("height") or 0)),
                weight_lb=int(float(r.get("weight") or 0)),
            )
        )
    return players


def _load_snapshot(teams: list[str]) -> list[Player]:
    with SNAPSHOT_PATH.open() as f:
        rows = [r for r in csv.DictReader(f) if not teams or r["team"] in teams]
    return _from_rows(rows)


def _load_nflverse(season: int, teams: list[str]) -> list[Player]:
    import nfl_data_py as nfl  # optional dependency

    df = nfl.import_seasonal_rosters([season])
    df = df[df["status"] == "ACT"]
    if teams:
        df = df[df["team"].isin(teams)]
    df = df.sort_values("week").drop_duplicates("gsis_id", keep="last")
    df = df.rename(columns={"gsis_it_id": "nfl_id"})
    return _from_rows(df.fillna("").to_dict("records"))


@lru_cache(maxsize=1)
def load_roster() -> list[Player]:
    cfg = load_config().get("roster", {})
    teams = list(cfg.get("teams", []))
    source = cfg.get("source", "snapshot")
    if source == "nflverse":
        try:
            players = _load_nflverse(int(cfg.get("season", 2024)), teams)
            if players:
                log.info("roster: loaded %d players from nflverse", len(players))
                return players
        except Exception as exc:  # noqa: BLE001 - offline containers must still boot
            log.warning("roster: nflverse load failed (%s); using bundled snapshot", exc)
    players = _load_snapshot(teams)
    log.info("roster: loaded %d players from snapshot", len(players))
    return players
