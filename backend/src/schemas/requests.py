from datetime import date, datetime, time
from pydantic import BaseModel, ConfigDict, Field
from src.constants.enums import Review

class Request(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

class Credentials(Request):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

class Upload(Request):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=160)
    csv_text: str = Field(min_length=1, max_length=2_000_000)

class RunRequest(Request):
    dataset_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)

class ReviewRequest(Request):
    review: Review
    note: str = Field(min_length=1, max_length=500)
    decision: str = Field(default='', max_length=120)

class ScheduleRequest(RunRequest):
    recipient: str = Field(min_length=1, max_length=100)
    first_at: datetime
    repeat_daily: bool = False


# ---- 巡查排班 ----

class PoolUpsert(Request):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    trigger_date: date | None = None
    source_run_id: int | None = Field(default=None, gt=0)
    dwell_min: int = Field(default=10, ge=0, le=600)
    enabled: bool = True


class EdgeUpsert(Request):
    node_a: str = Field(min_length=1, max_length=40)
    node_b: str = Field(min_length=1, max_length=40)
    travel_min: int = Field(ge=1, le=1440)
    blocked: bool = False
    note: str | None = Field(default=None, max_length=200)


class ShiftRequest(Request):
    shift_date: date
    team: str = Field(min_length=1, max_length=64)
    start_clock: time
    end_clock: time
    note: str | None = Field(default=None, max_length=200)


class ReplanRequest(Request):
    shift_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=200)


class CheckinRequest(Request):
    shift_id: int = Field(gt=0)
    pool_code: str | None = Field(default=None, max_length=40)
    kind: str = Field(pattern='^(arrive|depart|return)$')
    event_at: datetime
    note: str | None = Field(default=None, max_length=200)
