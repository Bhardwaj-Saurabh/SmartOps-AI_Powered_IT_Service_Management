"""Team directory connector sidecar."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="team-directory-connector", version="0.1.0")

_TEAMS_PATH = os.environ.get("TEAMS_PATH", "/app/data/teams.yaml")
_teams: dict[str, Any] = yaml.safe_load(Path(_TEAMS_PATH).read_text()) or {}


class LookupRequest(BaseModel):
    team_ids: list[str]


class TeamRecord(BaseModel):
    team_id: str
    matched: bool
    description: str | None = None
    available: bool = False
    queue_depth: int = 0
    on_call: str | None = None


class LookupResponse(BaseModel):
    teams: list[TeamRecord]


@app.post("/lookup", response_model=LookupResponse)
async def lookup(req: LookupRequest) -> LookupResponse:
    table = (_teams.get("teams") or {})
    out: list[TeamRecord] = []
    for team_id in req.team_ids:
        entry = table.get(team_id)
        if entry is None:
            out.append(TeamRecord(team_id=team_id, matched=False))
        else:
            out.append(TeamRecord(
                team_id=team_id,
                matched=True,
                description=entry.get("description"),
                available=bool(entry.get("available", True)),
                queue_depth=int(entry.get("queue_depth", 0)),
                on_call=entry.get("on_call"),
            ))
    return LookupResponse(teams=out)


@app.get("/all", response_model=LookupResponse)
async def all_teams() -> LookupResponse:
    table = (_teams.get("teams") or {})
    return LookupResponse(teams=[
        TeamRecord(
            team_id=k,
            matched=True,
            description=v.get("description"),
            available=bool(v.get("available", True)),
            queue_depth=int(v.get("queue_depth", 0)),
            on_call=v.get("on_call"),
        )
        for k, v in table.items()
    ])


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "team-directory-connector", "version": "0.1.0", "team_count": len(_teams.get("teams") or {})}
