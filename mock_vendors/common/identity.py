"""Player identity defects shared by every service.

Each vendor identifies a player differently: GSIS ``00-00xxxxx``, integer
``nflId``, a vendor-specific ID, or a name only. On top of the service's
default style, a small share of records swap to another style and names get
mangled (suffix dropped, nickname, punctuation stripped, ``Last, First``,
upper-case, stray whitespace). Silver's crosswalk is what undoes this.
"""
from __future__ import annotations

import re

from .dirt import Dirt
from .roster import Player

STYLES = ("gsis", "nfl_id", "vendor", "name")
_SUFFIX_RE = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", re.IGNORECASE)


def split_suffix(full_name: str) -> tuple[str, str]:
    m = _SUFFIX_RE.search(full_name)
    if not m:
        return full_name, ""
    return full_name[: m.start()], m.group(1)


def name_variant(player: Player, dirt: Dirt) -> str:
    """Return the player's name, possibly mangled."""
    base, suffix = split_suffix(player.full_name)
    if not dirt.roll_p(float(dirt.identity_cfg.get("name_variant_prob", 0.0))):
        return player.full_name

    nick = dirt.identity_cfg.get("nickname_map", {})
    first, rest = base.split(" ", 1) if " " in base else (base, "")
    variant = dirt.choice(
        [
            "drop_suffix", "nickname", "football_name", "strip_punct",
            "last_first", "upper", "whitespace", "suffix_no_period", "initials",
        ]
    )
    if variant == "drop_suffix":
        return base
    if variant == "nickname":
        return f"{nick.get(first, first)} {rest}".strip() + (f" {suffix}" if suffix else "")
    if variant == "football_name" and player.football_name and player.football_name != first:
        return f"{player.football_name} {rest}".strip() + (f" {suffix}" if suffix else "")
    if variant == "strip_punct":
        return re.sub(r"[.'\-]", "", player.full_name)
    if variant == "last_first":
        return f"{rest}, {first}" if rest else base
    if variant == "upper":
        return player.full_name.upper()
    if variant == "whitespace":
        return f" {first}  {rest} ".replace("  ", "  ")
    if variant == "suffix_no_period" and suffix:
        return f"{base} {suffix.rstrip('.')}"
    if variant == "initials":
        return f"{first[0]}. {rest}" if rest else base
    return player.full_name


def player_ref(player: Player, vendor: str, dirt: Dirt, default_style: str, id_field: str, name_field: str) -> dict:
    """Return the identity fields to merge into a record.

    ``vendor`` picks the vendor-specific ID namespace; ``id_field``/``name_field``
    are the vendor's own key names (``athlete_id``, ``athleteId``, ``client_name`` ...).
    """
    style = default_style
    if dirt.roll_p(float(dirt.identity_cfg.get("style_swap_prob", 0.0))):
        style = dirt.choice([s for s in STYLES if s != default_style])

    name = name_variant(player, dirt)
    if style == "gsis":
        return {id_field: player.gsis_id, name_field: name}
    if style == "nfl_id":
        return {id_field: player.nfl_id, name_field: name}
    if style == "vendor":
        return {id_field: player.vendor_id(vendor), name_field: name}
    # name only: no id at all
    return {id_field: None, name_field: name}
