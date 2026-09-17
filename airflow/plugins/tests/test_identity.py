"""Unit tests for identity.clean_player_name and identity.PlayerResolver.

The clean-name fixtures are real (name, merge_name) pairs from nflverse's
`import_ids()` crosswalk, so "mirrors nflverse" is asserted, not claimed.
"""

from __future__ import annotations

import pytest

from identity import (
    GSIS_ID, NAME_TEAM, NAME_UNIQUE, NFL_ID, UNRESOLVED, VENDOR_MAP,
    PlayerResolver, clean_player_name, split_name,
)

# ------------------------------------------------------------ clean names

# (nflverse `name`, nflverse `merge_name`) — pulled verbatim from the crosswalk.
NFLVERSE_PAIRS = [
    ("A'Shawn Robinson", "ashawn robinson"),
    ("O.J. Brigance", "oj brigance"),
    ("Briean Boddy-Calhoun", "briean boddy-calhoun"),
    ("Ja'Gared Davis", "jagared davis"),
    ("Connor O'Toole", "connor otoole"),
    ("Marvin Harrison Jr.", "marvin harrison"),
    ("C.J. Uzomah", "cj uzomah"),
    ("T.J. McDonald", "tj mcdonald"),
    ("Del'Shawn Phillips", "delshawn phillips"),
    ("James O'Shaughnessy", "james oshaughnessy"),
    ("James-Michael Johnson", "james-michael johnson"),
    ("Rueben Bain Jr.", "rueben bain"),
    ("Ricky Jean-Francois", "ricky jean-francois"),
    ("Ted Ginn Jr.", "ted ginn"),
    ("Kenneth Walker III", "kenneth walker"),
    ("Calvin Austin III", "calvin austin"),
    ("Amon-Ra St. Brown", "amon-ra st brown"),
    ("R. w. Mcquarters", "r w mcquarters"),
    ("Patrick Mahomes", "patrick mahomes"),
]


@pytest.mark.parametrize("name,merge_name", NFLVERSE_PAIRS)
def test_matches_nflverse_merge_name(name, merge_name):
    # nicknames=False is the pure nflverse-mechanics mode.
    assert clean_player_name(name, nicknames=False) == merge_name


def test_nflverse_alias_database_is_not_mirrored():
    # nflverse also applies a hand-curated per-player alias table
    # ("Joshua Palmer" -> "josh palmer", "Sauce Gardner" -> "ahmad gardner").
    # That is not reproducible generically; we canonicalise nicknames the
    # other way instead, and both sides of every join use the same function.
    assert clean_player_name("Joshua Palmer") == clean_player_name("Josh Palmer") == "joshua palmer"


@pytest.mark.parametrize("variant", [
    "Odell Beckham Jr.", "Odell Beckham Jr", "Odell Beckham", "ODELL BECKHAM JR.",
    "Beckham Jr., Odell",        # EMR style: suffix before the comma
    "Beckham, Odell",            # EMR with suffix stripped
    "  Odell   Beckham  III ",   # whitespace + a different suffix
])
def test_suffix_flip_case_whitespace_all_collapse(variant):
    assert clean_player_name(variant) == "odell beckham"


@pytest.mark.parametrize("variant", ["A.J. Brown", "AJ Brown", "a.j. brown", "BROWN, A.J.", "AJ BROWN"])
def test_initials_collapse(variant):
    assert clean_player_name(variant) == "aj brown"


@pytest.mark.parametrize("nick,formal", [
    ("Mitch Trubisky", "Mitchell Trubisky"),
    ("Mike Evans", "Michael Evans"),
    ("Josh Allen", "Joshua Allen"),
    ("Ken Walker III", "Kenneth Walker"),
    ("Pat Freiermuth", "Patrick Freiermuth"),
    ("Trubisky, Mitch", "Mitchell Trubisky"),
])
def test_nickname_map_applies_to_first_token_only(nick, formal):
    assert clean_player_name(nick) == clean_player_name(formal)


def test_nickname_map_does_not_touch_last_names():
    # "Will" as a surname must not become "William".
    assert clean_player_name("Marcus Will") == "marcus will"


def test_first_token_never_treated_as_suffix():
    # A player whose first name happens to be a roman numeral / suffix token.
    assert clean_player_name("V Smith") == "v smith"


def test_hyphens_and_accents():
    assert clean_player_name("Felix Anudike-Uzomah") == "felix anudike-uzomah"
    assert clean_player_name("Anudike-Uzomah, Felix") == "felix anudike-uzomah"
    assert clean_player_name("José Ramírez") == "jose ramirez"


def test_empty_and_none():
    assert clean_player_name(None) == ""
    assert clean_player_name("") == ""
    assert clean_player_name("   ") == ""


def test_split_name():
    assert split_name("odell beckham") == ("odell", "beckham")
    assert split_name("amon-ra st brown") == ("amon-ra", "st brown")
    assert split_name("cher") == ("cher", "")


# --------------------------------------------------------------- resolver

PLAYERS = [
    {"player_sk": 1, "gsis_id": "00-0033873", "nfl_id": 44822, "full_name": "Patrick Mahomes", "team": "KC"},
    {"player_sk": 2, "gsis_id": "00-0030506", "nfl_id": 40011, "full_name": "Travis Kelce", "team": "KC"},
    # Same clean name on two teams: resolvable with team, ambiguous without.
    {"player_sk": 3, "gsis_id": "00-0000003", "nfl_id": 3, "full_name": "Josh Allen", "team": "BUF"},
    {"player_sk": 4, "gsis_id": "00-0000004", "nfl_id": 4, "full_name": "Joshua Allen", "team": "JAX"},
    {"player_sk": 5, "gsis_id": "00-0000005", "nfl_id": None, "full_name": "Odell Beckham Jr.", "team": "LAR"},
    # Roster player_name is the football name; the EMR uses the legal first name.
    {"player_sk": 6, "gsis_id": "00-0033949", "nfl_id": None, "full_name": "Joshua Dobbs", "team": "DET",
     "alt_names": ["Robert Dobbs"]},
]


def resolver(vendor_map=()):
    return PlayerResolver(PLAYERS, vendor_map)


def test_rule1_gsis_wins_even_with_wrong_name():
    r = resolver().resolve("forcedeck", gsis_id="00-0033873", name="Nobody Real")
    assert (r.player_sk, r.resolved_by, r.confidence) == (1, GSIS_ID, 1.0)


def test_rule2_nfl_id_accepts_int_str_and_float_strings():
    for nfl_id in (40011, "40011", "40011.0", 40011.0):
        r = resolver().resolve("ams_wellness", nfl_id=nfl_id)
        assert (r.player_sk, r.resolved_by) == (2, NFL_ID)


def test_rule3_vendor_map_beats_name_matching():
    vm = [{"vendor": "catapult", "vendor_player_id": "cat_deadbeef", "player_sk": 2,
           "resolved_by": NAME_UNIQUE, "confidence": 0.75}]
    r = resolver(vm).resolve("catapult", vendor_player_id="cat_deadbeef", name="Patrick Mahomes")
    assert (r.player_sk, r.resolved_by, r.confidence) == (2, VENDOR_MAP, 0.75)


def test_rule4_name_plus_team_disambiguates():
    r = resolver().resolve("nutrition", name="Allen, Josh", team="buf")
    assert (r.player_sk, r.resolved_by) == (3, NAME_TEAM)
    r = resolver().resolve("nutrition", name="JOSHUA ALLEN", team="JAX")
    assert (r.player_sk, r.resolved_by) == (4, NAME_TEAM)


def test_rule4_falls_through_to_rule5_when_team_is_wrong_but_name_unique():
    # Traded player: team in the vendor feed is stale, but the name is unique league-wide.
    r = resolver().resolve("nutrition", name="Travis Kelce", team="DEN")
    assert (r.player_sk, r.resolved_by) == (2, NAME_UNIQUE)


def test_aliases_resolve_legal_first_name():
    r = resolver().resolve("emr", name="DOBBS, ROBERT", team="DET")
    assert (r.player_sk, r.resolved_by) == (6, NAME_TEAM)
    r = resolver().resolve("nutrition", name="Joshua Dobbs", team="DET")
    assert (r.player_sk, r.resolved_by) == (6, NAME_TEAM)


def test_rule5_unique_name_without_team():
    r = resolver().resolve("catapult", vendor_player_id="cat_1", name="Beckham Jr., Odell")
    assert (r.player_sk, r.resolved_by) == (5, NAME_UNIQUE)


def test_rule6_ambiguous_name_is_quarantined_with_candidates():
    res = resolver()
    r = res.resolve("catapult", vendor_player_id="cat_2", name="Josh Allen", context={"bronze_id": 9})
    assert not r.ok
    assert r.resolved_by == UNRESOLVED
    assert r.reason == "ambiguous"
    assert set(r.candidates) == {3, 4}
    q = res.quarantine[("catapult", "cat_2", "ambiguous")]
    assert q.occurrences == 1 and q.raw_name == "Josh Allen" and q.example_context == {"bronze_id": 9}
    assert ("catapult", "cat_2") not in res.new_map_entries


def test_rule6_unknown_name_is_quarantined_and_counted_once_per_identifier():
    res = resolver()
    for _ in range(3):
        r = res.resolve("nutrition", name="Zed Nobody", team="KC")
    assert r.reason == "unresolved"
    assert len(res.quarantine) == 1
    assert res.quarantine[("nutrition", "Zed Nobody", "unresolved")].occurrences == 3


def test_successful_name_resolution_is_pinned_for_next_run():
    res = resolver()
    res.resolve("nutrition", name="MAHOMES, PATRICK", team="KC")
    assert res.new_map_entries[("nutrition", "MAHOMES, PATRICK")] == (1, NAME_TEAM, 0.9)
    # Feed the learned map back in: the same string now resolves by the map.
    vm = [{"vendor": v, "vendor_player_id": i, "player_sk": sk, "resolved_by": m, "confidence": c}
          for (v, i), (sk, m, c) in res.new_map_entries.items()]
    r2 = resolver(vm).resolve("nutrition", name="MAHOMES, PATRICK", team="KC")
    assert (r2.player_sk, r2.resolved_by) == (1, VENDOR_MAP)


def test_id_matches_are_not_pinned_to_the_map_when_no_vendor_id():
    res = resolver()
    res.resolve("forcedeck", gsis_id="00-0033873")
    assert res.new_map_entries == {}


def test_method_counts_feed_the_scorecard():
    res = resolver()
    res.resolve("forcedeck", gsis_id="00-0033873")
    res.resolve("ams_wellness", nfl_id=40011)
    res.resolve("nutrition", name="Odell Beckham", team="LAR")
    res.resolve("nutrition", name="Nobody", team="LAR")
    assert dict(res.method_counts) == {GSIS_ID: 1, NFL_ID: 1, NAME_TEAM: 1, UNRESOLVED: 1}
