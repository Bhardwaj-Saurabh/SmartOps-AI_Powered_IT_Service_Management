"""Business-hours-aware elapsed-minute calculator.

Two endpoints:
  POST /elapsed_24x7         — raw wall-clock minutes between two timestamps
  POST /elapsed_business     — minutes that fall inside the supplied
                              business-hours window per weekday

Business-hours windows are passed in by the caller — the agent owns the
SBCA rule and slices it for the timezone it cares about, so this tool
stays stateless.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="clock-timer-service", version="0.1.0")


class Elapsed24Request(BaseModel):
    started_at_epoch: int
    now_at_epoch: int | None = None


class ElapsedResponse(BaseModel):
    started_at_epoch: int
    end_epoch: int
    elapsed_minutes: float


class ElapsedBusinessRequest(BaseModel):
    started_at_epoch: int
    now_at_epoch: int | None = None
    timezone: str = "UTC"
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    """ISO weekdays: 1 = Monday, 7 = Sunday."""
    start: str = "00:00"   # "HH:MM"
    end: str = "23:59"


def _now() -> int:
    return int(datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).timestamp())


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


@app.post("/elapsed_24x7", response_model=ElapsedResponse)
async def elapsed_24x7(req: Elapsed24Request) -> ElapsedResponse:
    end = req.now_at_epoch or _now()
    return ElapsedResponse(
        started_at_epoch=req.started_at_epoch, end_epoch=end,
        elapsed_minutes=max(0.0, (end - req.started_at_epoch) / 60.0),
    )


@app.post("/elapsed_business", response_model=ElapsedResponse)
async def elapsed_business(req: ElapsedBusinessRequest) -> ElapsedResponse:
    try:
        tz = ZoneInfo(req.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown timezone: {req.timezone}") from exc
    end_epoch = req.now_at_epoch or _now()
    if end_epoch <= req.started_at_epoch:
        return ElapsedResponse(started_at_epoch=req.started_at_epoch, end_epoch=end_epoch,
                               elapsed_minutes=0.0)
    bh_start = _parse_hhmm(req.start)
    bh_end = _parse_hhmm(req.end)
    weekdays = set(req.weekdays)

    total_minutes = 0.0
    cursor = datetime.fromtimestamp(req.started_at_epoch, tz=tz)
    end_dt = datetime.fromtimestamp(end_epoch, tz=tz)

    # Iterate day by day in the agent's timezone, intersecting each window.
    safety = 0
    while cursor < end_dt and safety < 400:
        safety += 1
        if cursor.isoweekday() in weekdays:
            day_start = cursor.replace(hour=bh_start.hour, minute=bh_start.minute, second=0, microsecond=0)
            day_end = cursor.replace(hour=bh_end.hour, minute=bh_end.minute, second=0, microsecond=0)
            window_start = max(cursor, day_start)
            window_end = min(end_dt, day_end)
            if window_end > window_start:
                total_minutes += (window_end - window_start).total_seconds() / 60.0
        # Advance to next midnight
        next_day = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = next_day

    return ElapsedResponse(
        started_at_epoch=req.started_at_epoch, end_epoch=end_epoch,
        elapsed_minutes=round(total_minutes, 2),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "clock-timer-service", "version": "0.1.0"}
