"""silver.emr_injuries + silver.emr_status_updates.

Wrinkles: "Last, First" names with suffixes before the comma; free-text body
parts ('hammy', 'Knee (R)', 'L mcl', 'concussion protocol') with the side
sometimes embedded in the text instead of its field; status updates emitted
out of order (Thursday before Wednesday) and corrections that re-emit an
update_id with a changed status and later updated_at; expected_rtp that is
stale (earlier than the last DNP).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from silver.common import (et_date, fetch_bronze, latest_per_key, load_resolver, parse_iso_date,
                           parse_iso_utc, persist_resolver, replace_table)

log = logging.getLogger(__name__)

VENDOR = "emr"
INJURY_COLUMNS = ["bronze_id", "injury_id", "player_sk", "resolved_by", "player_raw", "team",
                  "event_ts", "event_date", "body_part", "body_part_raw", "side", "side_source",
                  "injury_type", "severity", "expected_rtp", "opened_at", "qc_flags"]
UPDATE_COLUMNS = ["bronze_id", "update_id", "injury_id", "player_sk", "resolved_by", "player_raw",
                  "updated_at", "update_date", "practice_status", "game_status", "note",
                  "is_correction", "qc_flags"]

# Free text -> canonical body part. Order matters: more specific first.
BODY_PART_PATTERNS = [
    (re.compile(r"\b(hamstring|hammy|ham)\b", re.I), "hamstring"),
    (re.compile(r"\b(knee|mcl|acl|meniscus)\b", re.I), "knee"),
    (re.compile(r"\b(ankle|high ankle|achilles)\b", re.I), "ankle"),
    (re.compile(r"\b(shoulder|ac joint|labrum)\b", re.I), "shoulder"),
    (re.compile(r"\b(concussion|head|protocol)\b", re.I), "concussion"),
]
SIDE_PATTERNS = [
    (re.compile(r"\((L|R)\)", re.I), None),               # 'Knee (R)'
    (re.compile(r"^(L|R)\s", re.I), None),                # 'L mcl'
    (re.compile(r"\b(left|right)\b", re.I), None),        # 'left hamstring'
]


def parse_body_part(raw: str | None, side_field: str | None) -> tuple[str | None, str | None, str | None]:
    """(canonical body part, side, side_source)."""
    if not raw:
        return None, side_field, "field" if side_field else None
    part = next((canon for rx, canon in BODY_PART_PATTERNS if rx.search(raw)), None)
    if side_field in ("L", "R"):
        return part, side_field, "field"
    for rx, _ in SIDE_PATTERNS:
        m = rx.search(raw)
        if m:
            s = m.group(1).upper()[0]
            return part, s, "text"
    return part, None, None


def build(conn) -> dict:
    resolver = load_resolver(conn)

    # --- injuries -------------------------------------------------------
    inj_bronze = fetch_bronze(conn, VENDOR, "/v1/injuries")
    inj_rows, inj_dropped = latest_per_key(inj_bronze, lambda r: r["payload"]["injury_id"])
    # --- status updates ------------------------------------------------
    upd_bronze = fetch_bronze(conn, VENDOR, "/v1/status_updates")
    versions: dict = defaultdict(list)
    for r in upd_bronze:
        versions[r["payload"]["update_id"]].append(r)
    upd_rows = []
    for update_id, vs in versions.items():
        # The correction carries a later updated_at; latest event time wins,
        # ties broken by ingest order.
        best = max(vs, key=lambda r: (parse_iso_utc(r["payload"]["updated_at"]), r["_ingested_at"], r["id"]))
        upd_rows.append((best, len(vs) > 1))

    # Last DNP date per injury, for the stale expected_rtp check.
    last_dnp: dict = {}
    for r, _ in upd_rows:
        p = r["payload"]
        if p.get("practice_status") == "DNP":
            d = et_date(parse_iso_utc(p["updated_at"]))
            last_dnp[p["injury_id"]] = max(last_dnp.get(p["injury_id"], d), d)

    injuries, part_counts = [], defaultdict(int)
    injury_player: dict = {}
    for r in inj_rows:
        p = r["payload"]
        res = resolver.resolve(VENDOR, name=p.get("player"), team=p.get("team"),
                               context={"bronze_id": r["id"]})
        injury_player[p["injury_id"]] = res
        part, side, side_src = parse_body_part(p.get("body_part"), p.get("side"))
        part_counts[part or "unmapped"] += 1
        flags = []
        if part is None:
            flags.append("body_part_unmapped")
        if side_src == "text":
            flags.append("side_from_text")
        if (p.get("body_part") or "").strip().lower() != (part or ""):
            flags.append("body_part_free_text")
        rtp = parse_iso_date(p.get("expected_rtp"))
        if rtp and p["injury_id"] in last_dnp and rtp < last_dnp[p["injury_id"]]:
            flags.append("stale_expected_rtp")
        if not res.ok:
            flags.append("unresolved_player")
        ts = parse_iso_utc(p["event_date"])
        injuries.append((
            r["id"], p["injury_id"], res.player_sk, res.resolved_by, p.get("player"), p.get("team"),
            ts, et_date(ts), part, p.get("body_part"), side, side_src,
            p.get("injury_type"), p.get("severity"), rtp,
            parse_iso_utc(p["opened_at"]) if p.get("opened_at") else None, flags,
        ))

    updates = []
    for r, corrected in sorted(upd_rows, key=lambda t: (t[0]["payload"]["updated_at"], t[0]["id"])):
        p = r["payload"]
        # Prefer the injury's resolution (same person, one lookup); fall back to the name.
        res = injury_player.get(p["injury_id"]) or resolver.resolve(
            VENDOR, name=p.get("player"), context={"bronze_id": r["id"]})
        ts = parse_iso_utc(p["updated_at"])
        flags = ["correction_overwrite"] if corrected else []
        if p["injury_id"] not in injury_player:
            flags.append("orphan_update")
        if not res.ok:
            flags.append("unresolved_player")
        updates.append((
            r["id"], p["update_id"], p["injury_id"], res.player_sk, res.resolved_by, p.get("player"),
            ts, et_date(ts), p.get("practice_status"), p.get("game_status"), p.get("note"),
            corrected, flags,
        ))

    n_inj = replace_table(conn, "emr_injuries", INJURY_COLUMNS, injuries)
    n_upd = replace_table(conn, "emr_status_updates", UPDATE_COLUMNS, updates)
    idstats = persist_resolver(conn, resolver, VENDOR)
    conn.commit()
    stats = {"injuries": {"bronze_rows": len(inj_bronze), "duplicates_dropped": inj_dropped, "silver_rows": n_inj,
                          "body_parts": dict(part_counts),
                          "side_from_text": sum(1 for i in injuries if i[11] == "text"),
                          "stale_expected_rtp": sum(1 for i in injuries if "stale_expected_rtp" in i[16])},
             "status_updates": {"bronze_rows": len(upd_bronze), "silver_rows": n_upd,
                                "corrections": sum(1 for u in updates if u[11])},
             "resolved_by": dict(resolver.method_counts), **idstats}
    log.info("%s: %s", VENDOR, stats)
    return stats
