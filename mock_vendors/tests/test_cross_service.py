"""Cross-service guarantees: shared sim calendar and correlated signals."""

from datetime import date, timedelta

from mock_vendors.ams_wellness_svc.app import build_vendor as build_ams
from mock_vendors.catapult_svc.app import build_vendor as build_catapult
from mock_vendors.common import simulation as sim
from mock_vendors.common.sim_clock import day_type
from mock_vendors.emr_svc.app import build_vendor as build_emr
from mock_vendors.forcedeck_svc.app import build_vendor as build_forcedeck
from mock_vendors.nutrition_svc.app import build_vendor as build_nutrition
from mock_vendors.testing import make_settings

SEED = "gridironops"
SEASON_START = date(2026, 7, 20)


def test_all_services_agree_on_sim_date(tmp_path):
    vendors = []
    for name, builder in [
        ("catapult_svc", build_catapult),
        ("forcedeck_svc", build_forcedeck),
        ("ams_wellness_svc", build_ams),
        ("nutrition_svc", build_nutrition),
        ("emr_svc", build_emr),
    ]:
        v = builder(make_settings(name, tmp_path))
        v.catch_up()
        vendors.append(v)
    dates = {v.current_sim_date() for v in vendors}
    assert len(dates) == 1


def test_soreness_tracks_prior_day_load():
    """Wellness drivers must rise after heavy Catapult days (same sim world)."""
    days = [SEASON_START + timedelta(days=i) for i in range(45)]
    after_game, after_off = [], []
    for gsis in [f"00-00{i:05d}" for i in range(40)]:
        for d in days:
            drv = sim.wellness_drivers(SEED, gsis, d)["soreness"]
            prev = d - timedelta(days=1)
            if day_type(prev) == "game":
                after_game.append(drv)
            elif day_type(prev) == "off":
                after_off.append(drv)
    assert sum(after_game) / len(after_game) > sum(after_off) / len(after_off) + 1.0


def test_injury_risk_rises_with_acwr_and_asymmetry():
    """Empirically: injuries land disproportionately on flagged player-days."""
    days = [SEASON_START + timedelta(days=i) for i in range(150)]
    flagged_hits = flagged_days = base_hits = base_days = 0
    for gsis in [f"00-00{i:05d}" for i in range(60)]:
        for d in days:
            if day_type(d) not in ("practice", "game"):
                continue
            flagged = (
                sim.acwr(SEED, gsis, d - timedelta(days=3)) > 1.5
                or sim.asymmetry(SEED, gsis, d, SEASON_START) > 10
            )
            hit = sim.injury_on(SEED, gsis, d, SEASON_START) is not None
            if flagged:
                flagged_days += 1
                flagged_hits += hit
            else:
                base_days += 1
                base_hits += hit
    assert flagged_days > 0 and base_days > 0
    flagged_rate = flagged_hits / flagged_days
    base_rate = base_hits / max(1, base_days)
    assert flagged_rate > base_rate * 2
