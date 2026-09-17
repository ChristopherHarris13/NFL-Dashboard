"""Player identity resolution.

Five vendors, five ways to say who a player is: a vendor UUID, a GSIS id, an
integer nflId, a mangled name, "Last, First". Everything in Silver hangs off
one `player_sk`, and this module is the only place that decides which one.

Pure Python on purpose (no Airflow, no DB imports): `clean_player_name` and
`PlayerResolver` are unit-tested in tests/test_identity.py; silver/common.py
loads the resolver from Postgres and persists what it learns.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# --------------------------------------------------------------------- names

# Generational suffixes nflverse strips (nflreadr::clean_player_names).
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

# Nickname -> formal first name. Both sides of every join go through the same
# map, so "Mitch Trubisky" and "Mitchell Trubisky" collapse to one key. This
# is deliberately small: only unambiguous, common NFL-roster shortenings.
NICKNAMES = {
    "mitch": "mitchell", "mike": "michael", "chris": "christopher",
    "matt": "matthew", "josh": "joshua", "nick": "nicholas",
    "cam": "cameron", "zach": "zachary", "zack": "zachary", "ben": "benjamin",
    "alex": "alexander", "jon": "jonathan", "will": "william",
    "tony": "anthony", "dan": "daniel", "danny": "daniel", "joe": "joseph",
    "tom": "thomas", "ken": "kenneth", "kenny": "kenneth", "pat": "patrick",
}

_PUNCT_RE = re.compile(r"[.'’`]")
_WS_RE = re.compile(r"\s+")


def clean_player_name(name: str | None, *, nicknames: bool = True) -> str:
    """Canonical join key for a player name.

    Mirrors nflverse's `merge_name` (lowercase, ASCII, suffix stripped,
    periods/apostrophes removed, hyphens kept) and adds two things the
    vendors need: "Last, First" is flipped (suffix may sit before the comma,
    "Beckham Jr., Odell"), and a small nickname map canonicalises the first
    token. With nicknames=False the output equals nflverse's merge_name.

    >>> clean_player_name("Beckham Jr., Odell")
    'odell beckham'
    >>> clean_player_name("A.J. Brown")
    'aj brown'
    >>> clean_player_name("MITCH TRUBISKY")
    'mitchell trubisky'
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.strip()
    if "," in s:
        last, first = s.split(",", 1)
        s = f"{first.strip()} {last.strip()}"
    s = _PUNCT_RE.sub("", s.lower())
    s = _WS_RE.sub(" ", s).strip()
    tokens = s.split(" ")
    # Suffix can be trailing ("kenneth walker iii") or, after a flip, in the
    # middle ("odell beckham jr"); never treat the first token as a suffix.
    tokens = [t for i, t in enumerate(tokens) if i == 0 or t not in SUFFIXES]
    if nicknames and tokens and tokens[0] in NICKNAMES:
        tokens[0] = NICKNAMES[tokens[0]]
    return " ".join(tokens)


def split_name(clean_name: str) -> tuple[str, str]:
    """('first', 'rest of name') from a cleaned name."""
    first, _, last = clean_name.partition(" ")
    return first, last


# ---------------------------------------------------------------- resolver

# Method names, in the order they are tried. `resolved_by` on every Silver
# row is one of these (or UNRESOLVED), which is what the DQ scorecard charts.
GSIS_ID = "gsis_id"
NFL_ID = "nfl_id"
VENDOR_MAP = "vendor_map"
NAME_TEAM = "name_team"
NAME_UNIQUE = "name_unique"
UNRESOLVED = "unresolved"

CONFIDENCE = {GSIS_ID: 1.0, NFL_ID: 1.0, NAME_TEAM: 0.9, NAME_UNIQUE: 0.75}


@dataclass(frozen=True)
class Resolution:
    player_sk: int | None
    resolved_by: str
    confidence: float | None = None
    reason: str | None = None            # only when unresolved: 'unresolved' | 'ambiguous'
    candidates: tuple[int, ...] = ()     # player_sks that tied (ambiguous)

    @property
    def ok(self) -> bool:
        return self.player_sk is not None


@dataclass
class QuarantineEntry:
    vendor: str
    vendor_player_id: str
    raw_name: str | None
    raw_team: str | None
    reason: str
    candidates: tuple[int, ...]
    occurrences: int = 0
    example_context: Any = None


class PlayerResolver:
    """In-memory index over dim_player_master + vendor_player_map.

    Build once per task, call `resolve()` per record, then read
    `new_map_entries` and `quarantine` back out to persist.
    """

    def __init__(self, players: Iterable[Mapping[str, Any]],
                 vendor_map: Iterable[Mapping[str, Any]] = ()):
        self.by_gsis: dict[str, int] = {}
        self.by_nfl_id: dict[int, int] = {}
        self.by_name_team: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.by_name: dict[str, list[int]] = defaultdict(list)
        for p in players:
            sk = int(p["player_sk"])
            if p.get("gsis_id"):
                self.by_gsis[str(p["gsis_id"])] = sk
            if p.get("nfl_id") is not None:
                self.by_nfl_id[int(p["nfl_id"])] = sk
            # A player answers to more than one name: the roster's player_name
            # ("Joshua Dobbs"), the legal first name an EMR uses ("Robert Dobbs"),
            # a football_name ("Hollywood Brown"). Index every alias the
            # caller supplies alongside the primary clean_name.
            names = {p.get("clean_name") or clean_player_name(p.get("full_name"))}
            names.update(clean_player_name(a) for a in (p.get("alt_names") or ()))
            names.discard("")
            for cn in names:
                if sk not in self.by_name[cn]:
                    self.by_name[cn].append(sk)
                if p.get("team"):
                    key = (cn, str(p["team"]).upper())
                    if sk not in self.by_name_team[key]:
                        self.by_name_team[key].append(sk)

        self.vendor_map: dict[tuple[str, str], tuple[int, str, float | None]] = {}
        for m in vendor_map:
            self.vendor_map[(m["vendor"], str(m["vendor_player_id"]))] = (
                int(m["player_sk"]), m.get("resolved_by") or VENDOR_MAP,
                None if m.get("confidence") is None else float(m["confidence"]),
            )

        self.new_map_entries: dict[tuple[str, str], tuple[int, str, float]] = {}
        self.quarantine: dict[tuple[str, str, str], QuarantineEntry] = {}
        self.method_counts: dict[str, int] = defaultdict(int)

    # -- the six rules, in order ------------------------------------------

    def resolve(self, vendor: str, *, gsis_id: str | None = None,
                nfl_id: int | str | None = None,
                vendor_player_id: str | None = None,
                name: str | None = None, team: str | None = None,
                context: Any = None) -> Resolution:
        # For name-only vendors the raw name string is the vendor's identifier,
        # so each mangled variant is resolved once and cached in the map.
        vid = vendor_player_id if vendor_player_id is not None else name
        vid = None if vid is None else str(vid)

        res = self._try(vendor, vid, gsis_id, nfl_id, name, team)
        self.method_counts[res.resolved_by] += 1
        if res.ok and vid is not None and res.resolved_by != VENDOR_MAP \
                and (vendor, vid) not in self.vendor_map:
            self.new_map_entries[(vendor, vid)] = (res.player_sk, res.resolved_by, res.confidence)
        if not res.ok:
            key = (vendor, vid or "", res.reason or UNRESOLVED)
            q = self.quarantine.get(key)
            if q is None:
                q = self.quarantine[key] = QuarantineEntry(
                    vendor, vid or "", name, team, res.reason or UNRESOLVED,
                    res.candidates, example_context=context)
            q.occurrences += 1
        return res

    def _try(self, vendor, vid, gsis_id, nfl_id, name, team) -> Resolution:
        # 1. gsis_id exact
        if gsis_id and str(gsis_id) in self.by_gsis:
            return Resolution(self.by_gsis[str(gsis_id)], GSIS_ID, CONFIDENCE[GSIS_ID])
        # 2. nfl_id exact
        if nfl_id not in (None, ""):
            try:
                sk = self.by_nfl_id.get(int(float(nfl_id)))
            except (TypeError, ValueError):
                sk = None
            if sk is not None:
                return Resolution(sk, NFL_ID, CONFIDENCE[NFL_ID])
        # 3. existing vendor map entry
        if vid is not None and (vendor, vid) in self.vendor_map:
            sk, _method, conf = self.vendor_map[(vendor, vid)]
            return Resolution(sk, VENDOR_MAP, conf)
        cn = clean_player_name(name)
        if not cn:
            return Resolution(None, UNRESOLVED, reason=UNRESOLVED)
        # 4. clean_name + team
        if team:
            sks = self.by_name_team.get((cn, str(team).upper()), [])
            if len(sks) == 1:
                return Resolution(sks[0], NAME_TEAM, CONFIDENCE[NAME_TEAM])
            if len(sks) > 1:
                return Resolution(None, UNRESOLVED, reason="ambiguous", candidates=tuple(sks))
        # 5. clean_name alone, only if unique
        sks = self.by_name.get(cn, [])
        if len(sks) == 1:
            return Resolution(sks[0], NAME_UNIQUE, CONFIDENCE[NAME_UNIQUE])
        if len(sks) > 1:
            return Resolution(None, UNRESOLVED, reason="ambiguous", candidates=tuple(sks))
        # 6. give up
        return Resolution(None, UNRESOLVED, reason=UNRESOLVED)
