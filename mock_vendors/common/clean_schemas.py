"""Pydantic models describing what each service's records look like when
every dirt probability is 0.0. Used by tests: clean config => every emitted
record validates against these.

Date-driven behaviors still happen with dirt off (AMS schema drift, Likert
scale change), so the AMS record is a union of its v1 and v2 shapes.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

ISO_Z = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
NAIVE_LOCAL = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
DATE_US = r"^\d{2}/\d{2}/\d{4}$"
ISO_EASTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-0[45]:00$"
ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class CatapultSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(pattern=UUID_RE)
    player_id: str = Field(pattern=r"^cat_[0-9a-f]{8}$")
    session_ts: str = Field(pattern=ISO_Z)
    session_type: Literal["practice", "walkthrough", "game"]
    duration_min: int = Field(ge=30, le=150)
    total_distance: float = Field(ge=400, le=13000)
    high_speed_distance: float = Field(ge=0, le=2700)
    sprint_count: int = Field(ge=0, le=40)
    accel_count: int = Field(ge=10, le=80)
    decel_count: int = Field(ge=10, le=80)
    player_load: float = Field(ge=200, le=900)
    distance_unit: Literal["m"]
    speed_unit: Literal["m/s"]
    max_speed: float = Field(ge=6, le=10.5)


class ForcedeckTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str = Field(pattern=UUID_RE)
    athlete_gsis_id: str = Field(pattern=r"^00-00\d{5}$")
    test_ts: int = Field(ge=1_500_000_000, le=2_500_000_000)
    test_type: Literal["CMJ", "IMTP", "DJ"]
    rep: int = Field(ge=1, le=3)
    jump_height_cm: Optional[float] = Field(default=None, ge=20, le=70)
    peak_force: float = Field(ge=1500, le=6500)
    force_unit: Literal["N"]
    rfd: float = Field(ge=4000, le=12000)
    asymmetry_pct: float = Field(ge=-15, le=15)
    device_id: str = Field(pattern=r"^FD-\d{4}$")


class WellnessSurveyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_id: str = Field(pattern=UUID_RE)
    player_id: int
    name: str
    submitted_at: str = Field(pattern=NAIVE_LOCAL)
    sleep_hours: float = Field(ge=4.0, le=10.0)
    sleep_quality: int = Field(ge=1, le=10)
    soreness: int = Field(ge=1, le=10)
    fatigue: int = Field(ge=1, le=10)
    stress: int = Field(ge=1, le=10)
    mood: int = Field(ge=1, le=10)
    srpe: Optional[int] = Field(default=None, ge=0, le=10)


class WellnessSurveyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_id: str = Field(pattern=UUID_RE)
    athlete_id: int
    first_name: str
    last_name: str
    submitted_at: str = Field(pattern=NAIVE_LOCAL)
    sleep_hours: float = Field(ge=4.0, le=10.0)
    sleep_quality: int = Field(ge=1, le=10)
    soreness: int = Field(ge=1, le=10)
    fatigue: int = Field(ge=1, le=10)
    stress: int = Field(ge=1, le=10)
    mood: int = Field(ge=1, le=10)
    srpe: Optional[int] = Field(default=None, ge=0, le=10)
    scale_max: Literal[5, 10]


WellnessSurvey = Union[WellnessSurveyV2, WellnessSurveyV1]


class NutritionMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str = Field(pattern=UUID_RE)
    player_name: str
    team: str = Field(pattern=r"^[A-Z]{2,3}$")
    measured_on: str = Field(pattern=DATE_US)
    method: Literal["DEXA", "BIA", "scale"]
    weight: float = Field(ge=150, le=420)
    weight_unit: Literal["lb"]
    body_fat_pct: Optional[float] = Field(default=None, ge=4, le=32)
    lean_mass: float = Field(ge=110, le=340)
    hydration_status: Optional[Literal["hydrated", "mild", "dehydrated"]] = None


class EmrInjury(BaseModel):
    model_config = ConfigDict(extra="forbid")

    injury_id: str = Field(pattern=UUID_RE)
    player: str = Field(pattern=r"^.+, .+$")
    team: str = Field(pattern=r"^[A-Z]{2,3}$")
    event_date: str = Field(pattern=ISO_EASTERN)
    body_part: Literal["hamstring", "knee", "ankle", "shoulder", "concussion"]
    side: Optional[Literal["L", "R"]] = None
    injury_type: Literal["strain", "sprain", "contusion", "fracture",
                         "concussion", "illness"]
    severity: Literal["minor", "moderate", "major"]
    expected_rtp: Optional[str] = Field(default=None, pattern=ISO_DATE)
    opened_at: str = Field(pattern=ISO_EASTERN)


class EmrStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    update_id: str = Field(pattern=UUID_RE)
    injury_id: str = Field(pattern=UUID_RE)
    player: str = Field(pattern=r"^.+, .+$")
    practice_status: Literal["DNP", "LP", "FP"]
    game_status: Optional[Literal["Out", "Doubtful", "Questionable"]] = None
    note: Optional[str] = None
    updated_at: str = Field(pattern=ISO_EASTERN)


CLEAN_MODELS = {
    "catapult_svc": {"sessions": CatapultSession},
    "forcedeck_svc": {"tests": ForcedeckTest},
    "ams_wellness_svc": {"surveys": WellnessSurvey},
    "nutrition_svc": {"measurements": NutritionMeasurement},
    "emr_svc": {"injuries": EmrInjury, "status_updates": EmrStatusUpdate},
}
