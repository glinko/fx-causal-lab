from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator


class TimeQuality(StrEnum):
    EXACT = "exact_timestamp"
    DAY = "known_release_day"
    CONSERVATIVE = "inferred_conservative"
    UNKNOWN = "unknown"


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    observation_id: str
    indicator: str
    observation_period: str
    event_time: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    available_at: AwareDatetime | None = None
    ingested_at: AwareDatetime
    time_quality: TimeQuality = TimeQuality.UNKNOWN
    source: str
    source_url: str
    raw_payload_hash: str
    vintage_id: str
    revision_number: int = 0
    revision_of: str | None = None
    actual: float
    consensus: float | None = None
    previous: float | None = None
    revised_previous: float | None = None
    surprise: float | None = None
    normalized_surprise: float | None = None
    forecast_vintage: AwareDatetime | None = None
    unit: str

    @model_validator(mode="after")
    def check_availability(self):
        if self.time_quality == TimeQuality.UNKNOWN and self.available_at is not None:
            raise ValueError("Unknown availability must not acquire a fabricated timestamp")
        if self.time_quality != TimeQuality.UNKNOWN and self.available_at is None:
            raise ValueError("Known availability requires available_at")
        if self.published_at and self.available_at and self.available_at < self.published_at:
            raise ValueError("available_at precedes publication")
        if self.consensus is None and (self.surprise is not None or self.normalized_surprise is not None):
            raise ValueError("Surprise requires consensus")
        if self.consensus is not None and self.forecast_vintage is None:
            raise ValueError("Consensus requires a forecast vintage")
        return self


class PredictionWindow(BaseModel):
    mode: Literal["pre_event", "post_release", "reaction_confirmed"]
    prediction_time: AwareDatetime
    target_start: AwareDatetime
    reaction_end: AwareDatetime | None = None

    @model_validator(mode="after")
    def no_overlap(self):
        if self.target_start < self.prediction_time:
            raise ValueError("Target starts before prediction")
        if self.mode == "reaction_confirmed" and self.reaction_end is None:
            raise ValueError("Reaction-confirmed prediction requires reaction_end")
        if self.reaction_end and self.reaction_end > self.prediction_time:
            raise ValueError("Reaction window extends into future")
        return self


def as_of(records: list[Observation], prediction_time: datetime, strict: bool = True) -> list[Observation]:
    if prediction_time.tzinfo is None or prediction_time.utcoffset() is None:
        raise ValueError("prediction_time must be timezone-aware")
    eligible = [r for r in records if r.available_at is not None
                and r.available_at <= prediction_time
                and r.time_quality != TimeQuality.UNKNOWN
                and (not strict or r.time_quality == TimeQuality.EXACT)
                and (r.forecast_vintage is None or r.forecast_vintage <= prediction_time)]
    latest = {}
    for record in sorted(eligible, key=lambda r: (r.available_at, r.revision_number, r.vintage_id)):
        latest[(record.source, record.indicator, record.observation_period)] = record
    return list(latest.values())
