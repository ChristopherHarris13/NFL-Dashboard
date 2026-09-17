"""Build the player seed cache (mock_vendors/common/seed/players.parquet).

Pulls current-season rosters from nflverse via nfl_data_py, joins the id
crosswalk, and caches every active player with the columns the mock services
need. Services never call nfl_data_py at runtime; they read the parquet
(seed_loader.py) so they can boot offline.

Run with a Python where nfl_data_py is installed (it pins old pandas, so on
new Pythons install `pandas>=2` first and then `pip install --no-deps
nfl_data_py`):

    python -m mock_vendors.common.build_seed [--season 2026]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed"
PARQUET_PATH = SEED_DIR / "players.parquet"

COLUMNS = [
    "gsis_id",
    "nfl_id",
    "espn_id",
    "full_name",
    "first_name",
    "last_name",
    "team",
    "position",
    "weight_lbs",
    "height_in",
]

# Positions the load/testing vendors care about.
KEEP_POSITIONS = {
    "QB", "RB", "FB", "WR", "TE",
    "T", "G", "C", "OL", "OT", "OG",
    "DE", "DT", "NT", "DL", "EDGE",
    "LB", "ILB", "OLB", "MLB",
    "CB", "S", "FS", "SS", "DB",
}

POSITION_DEFAULT_WEIGHT = {
    "QB": 220, "RB": 212, "FB": 245, "WR": 200, "TE": 250,
    "T": 315, "G": 315, "C": 305, "OL": 312, "OT": 315, "OG": 315,
    "DE": 270, "DT": 305, "NT": 330, "DL": 295, "EDGE": 255,
    "LB": 240, "ILB": 240, "OLB": 245, "MLB": 240,
    "CB": 192, "S": 205, "FS": 200, "SS": 210, "DB": 198,
}
POSITION_DEFAULT_HEIGHT = {
    "QB": 75, "RB": 71, "FB": 73, "WR": 73, "TE": 77,
    "T": 78, "G": 76, "C": 75, "OL": 77, "OT": 78, "OG": 76,
    "DE": 76, "DT": 75, "NT": 74, "DL": 76, "EDGE": 76,
    "LB": 74, "ILB": 74, "OLB": 75, "MLB": 74,
    "CB": 71, "S": 72, "FS": 72, "SS": 72, "DB": 71,
}


def build(season: int | None = None) -> Path:
    import nfl_data_py as nfl
    import pandas as pd

    seasons_to_try = [season] if season else [2026, 2025, 2024]
    rosters = None
    for s in seasons_to_try:
        try:
            rosters = nfl.import_seasonal_rosters([s])
            if len(rosters) > 0:
                print(f"Loaded {len(rosters)} roster rows for season {s}")
                break
        except Exception as exc:  # noqa: BLE001 - nflverse 404s raise various things
            print(f"season {s} unavailable ({exc}); trying next")
    if rosters is None or len(rosters) == 0:
        raise RuntimeError("No roster data available from nflverse")

    ids = nfl.import_ids()[["gsis_id", "nfl_id", "espn_id"]].dropna(subset=["gsis_id"])
    ids = ids.drop_duplicates(subset=["gsis_id"], keep="last")

    df = rosters.rename(columns={"player_id": "gsis_id", "player_name": "full_name"})
    if "status" in df.columns:
        df = df[df["status"].isin(["ACT", "Active"])]
    df = df[df["position"].isin(KEEP_POSITIONS)]
    df = df.dropna(subset=["gsis_id", "full_name", "team"])
    df = df.drop_duplicates(subset=["gsis_id"], keep="first")

    # espn_id exists in both frames; prefer the roster's, fill from crosswalk.
    df = df.merge(ids, on="gsis_id", how="left", suffixes=("", "_xwalk"))
    if "espn_id_xwalk" in df.columns:
        df["espn_id"] = df["espn_id"].fillna(df["espn_id_xwalk"])

    df["weight_lbs"] = pd.to_numeric(df["weight"], errors="coerce")
    df["height_in"] = pd.to_numeric(df["height"], errors="coerce")
    df["weight_lbs"] = df["weight_lbs"].fillna(df["position"].map(POSITION_DEFAULT_WEIGHT))
    df["height_in"] = df["height_in"].fillna(df["position"].map(POSITION_DEFAULT_HEIGHT))

    # Missing crosswalk ids get deterministic synthetic ones so every seed row
    # is usable by every service. Real ids are 7-digit-ish ints; synthetic ones
    # live in a 9xxxxxxx range so they cannot collide with real ones.
    def synth_id(gsis: str, offset: int) -> int:
        digits = int("".join(ch for ch in str(gsis) if ch.isdigit()) or "0")
        return 90_000_000 + (digits * 31 + offset) % 9_000_000

    df["nfl_id"] = pd.to_numeric(df["nfl_id"], errors="coerce")
    df["espn_id"] = pd.to_numeric(df["espn_id"], errors="coerce")
    df["nfl_id"] = df.apply(
        lambda r: int(r["nfl_id"]) if pd.notna(r["nfl_id"]) else synth_id(r["gsis_id"], 1), axis=1
    )
    df["espn_id"] = df.apply(
        lambda r: int(r["espn_id"]) if pd.notna(r["espn_id"]) else synth_id(r["gsis_id"], 2), axis=1
    )

    out = df[COLUMNS].reset_index(drop=True)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PARQUET_PATH, index=False)
    print(f"Wrote {len(out)} players -> {PARQUET_PATH}")
    return PARQUET_PATH


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()
    try:
        build(args.season)
    except Exception as exc:  # noqa: BLE001
        print(f"seed build failed: {exc}", file=sys.stderr)
        sys.exit(1)
