"""Configurable data-quality defect injectors.

Every defect takes a probability from mock_vendors/dirt_config.yaml, so a
demo can be dialed from surgically clean (all 0.0) to filthy. All functions
take an explicit random.Random so the dirt itself is deterministic per
(seed, player, day) — restarting a container regenerates the same dirt.

The dirt is the product. Never "fix" these.
"""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ABSENT = object()  # sentinel: leave the unit field out entirely

M_PER_YD = 0.9144
LBF_PER_N = 0.2248089431
KG_PER_LB = 0.45359237
MPH_PER_MS = 2.2369362921


class DirtConfig:
    def __init__(self, raw: dict):
        self.raw = raw or {}

    @classmethod
    def load(cls, path: Path | str) -> "DirtConfig":
        p = Path(path)
        if not p.exists():
            return cls({})
        return cls(yaml.safe_load(p.read_text()) or {})

    def p(self, service: str, defect: str) -> float:
        entry = (self.raw.get(service) or {}).get(defect)
        if entry is None:
            return 0.0
        if isinstance(entry, dict):
            return float(entry.get("p", 0.0))
        return float(entry)

    def opt(self, service: str, defect: str, key: str, default: Any = None) -> Any:
        entry = (self.raw.get(service) or {}).get(defect)
        if isinstance(entry, dict):
            return entry.get(key, default)
        return default


# ---------------------------------------------------------------- duplicates

def duplicate(record: dict, p: float, rng: random.Random,
              reround_field: str | None = None) -> list[dict]:
    """Return [record] or 2-3 copies; one copy may have a field re-rounded."""
    if rng.random() >= p:
        return [record]
    copies = [dict(record) for _ in range(rng.randint(2, 3))]
    if reround_field and rng.random() < 0.6:
        victim = rng.choice(copies[1:])
        val = victim.get(reround_field)
        if isinstance(val, float):
            victim[reround_field] = round(val, rng.choice([0, 1]))
    return copies


# ------------------------------------------------------------ field removal

def drop_field(record: dict, field: str, p: float, rng: random.Random) -> dict:
    """Remove the key entirely (absent, not null)."""
    if field in record and rng.random() < p:
        record = dict(record)
        del record[field]
    return record


def null_field(record: dict, field: str, p: float, rng: random.Random) -> dict:
    if field in record and rng.random() < p:
        record = dict(record)
        record[field] = None
    return record


# ------------------------------------------------------------------- names

_NICKNAMES = {
    "Mitchell": "Mitch", "Michael": "Mike", "Christopher": "Chris",
    "Matthew": "Matt", "Joshua": "Josh", "Nicholas": "Nick",
    "Cameron": "Cam", "Zachary": "Zach", "Benjamin": "Ben",
    "Alexander": "Alex", "Jonathan": "Jon", "William": "Will",
    "Anthony": "Tony", "Daniel": "Dan", "Joseph": "Joe",
    "Thomas": "Tom", "Kenneth": "Ken", "Patrick": "Pat",
}
_SUFFIXES = ["Jr.", "Sr.", "II", "III", "IV"]
_SUFFIX_RE = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$")


def mangle_name(name: str, p: float, rng: random.Random) -> str:
    """Randomly apply one of: suffix add/remove, de-period initials,
    nickname, 'Last, First' flip, uppercase."""
    if rng.random() >= p:
        return name
    move = rng.choice(["suffix", "periods", "nickname", "flip", "upper"])
    if move == "suffix":
        if _SUFFIX_RE.search(name):
            return _SUFFIX_RE.sub("", name)
        return f"{name} {rng.choice(_SUFFIXES)}"
    if move == "periods":
        if "." in name:
            return name.replace(".", "")
        parts = name.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) <= 3 and parts[0].isalpha() and parts[0].isupper():
            return f"{'.'.join(parts[0])}. {parts[1]}"
        return name.upper() if rng.random() < 0.5 else name
    if move == "nickname":
        first, _, rest = name.partition(" ")
        if first in _NICKNAMES and rest:
            return f"{_NICKNAMES[first]} {rest}"
        return name
    if move == "flip":
        m = _SUFFIX_RE.search(name)
        suffix = m.group(1) if m else ""
        core = _SUFFIX_RE.sub("", name)
        first, _, last = core.partition(" ")
        if last:
            return f"{last}{' ' + suffix if suffix else ''}, {first}"
        return name
    return name.upper()


# -------------------------------------------------------------- timestamps

def mangle_timestamp(ts: datetime, target_format: str, rng: random.Random,
                     tz_dirt_p: float = 0.0) -> str | int:
    """Render `ts` in the service's declared format. With probability
    tz_dirt_p, strip the tz or shift it by a wrong offset first."""
    if tz_dirt_p and rng.random() < tz_dirt_p:
        move = rng.choice(["strip", "wrong_offset"])
        if move == "strip":
            ts = ts.replace(tzinfo=None)
        else:
            ts = ts.replace(tzinfo=timezone(timedelta(hours=rng.choice([0, -7, 1]))))

    if target_format == "iso_z":
        if ts.tzinfo is None:
            return ts.strftime("%Y-%m-%dT%H:%M:%S")
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if target_format == "epoch":
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())
    if target_format == "naive_local":
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    if target_format == "naive_local_wrong_tz":
        return ts.strftime("%Y-%m-%d %H:%M:%S") + "+00:00"
    if target_format == "date_us":
        return ts.strftime("%m/%d/%Y")
    if target_format == "iso_eastern":
        return ts.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + ts.strftime("%z")[-2:]
    raise ValueError(f"unknown timestamp format {target_format}")


# ------------------------------------------------------------------- units

_CONVERSIONS = {
    ("m", "yd"): 1 / M_PER_YD,
    ("yd", "m"): M_PER_YD,
    ("m/s", "mph"): MPH_PER_MS,
    ("mph", "m/s"): 1 / MPH_PER_MS,
    ("N", "lbf"): LBF_PER_N,
    ("lbf", "N"): 1 / LBF_PER_N,
    ("lb", "kg"): KG_PER_LB,
    ("kg", "lb"): 1 / KG_PER_LB,
}


def swap_unit(value: float, from_unit: str, to_unit: str, p: float,
              rng: random.Random,
              wrong_label_p: float = 0.4, omit_label_p: float = 0.3):
    """Maybe convert `value` to the other unit. Returns (value, unit_label)
    where unit_label is the correct unit, the WRONG (stale) unit, or ABSENT.

    When no swap happens the correct from_unit label is returned.
    """
    if rng.random() >= p:
        return value, from_unit
    converted = round(value * _CONVERSIONS[(from_unit, to_unit)], 1)
    roll = rng.random()
    if roll < wrong_label_p:
        return converted, from_unit          # value converted, label stale
    if roll < wrong_label_p + omit_label_p:
        return converted, ABSENT             # value converted, label gone
    return converted, to_unit                # honestly labeled


def mislabel_unit(unit: str, wrong_unit: str, p: float, rng: random.Random,
                  wrong_label_p: float = 0.4, omit_label_p: float = 0.3):
    """Maybe mangle only the LABEL: the value stays in `unit`, but the export
    sometimes writes `wrong_unit` or omits the label entirely. Returns the
    label (correct, wrong, or ABSENT); the caller's value is untouched.
    """
    if rng.random() >= p:
        return unit
    roll = rng.random()
    if roll < wrong_label_p:
        return wrong_unit                    # value untouched, label wrong
    if roll < wrong_label_p + omit_label_p:
        return ABSENT                        # value untouched, label gone
    return unit


def outlier(value: float, p: float, rng: random.Random,
            replacement: tuple[float, float] | float) -> float:
    """Replace with a physiologically impossible value."""
    if rng.random() >= p:
        return value
    if isinstance(replacement, tuple):
        return round(rng.uniform(*replacement), 1)
    return float(replacement)


# --------------------------------------------------------------- late data

def late_emit_days(p: float, max_days: int, rng: random.Random) -> int:
    """0 = on time; otherwise 1..max_days simulated days late."""
    if rng.random() >= p:
        return 0
    return rng.randint(1, max_days)


# ------------------------------------------------------------ schema drift

def schema_drift(record: dict, drift_date: date, event_date: date,
                 rename_map: dict[str, str],
                 split_fields: dict[str, tuple[str, str]]) -> tuple[dict, str]:
    """After drift_date, rename keys and split full-name fields.
    Returns (record, schema_version)."""
    if event_date < drift_date:
        return record, "v1"
    out = {}
    for key, val in record.items():
        if key in split_fields:
            first_key, last_key = split_fields[key]
            first, _, last = str(val).partition(" ")
            out[first_key] = first
            out[last_key] = last or first
        else:
            out[rename_map.get(key, key)] = val
    return out, "v2"


def put_unit(record: dict, field: str, label) -> dict:
    """Attach a unit label produced by swap_unit/mislabel_unit, honoring ABSENT."""
    if label is ABSENT:
        record.pop(field, None)
    else:
        record[field] = label
    return record
