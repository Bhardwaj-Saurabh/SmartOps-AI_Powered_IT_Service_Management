"""Synthetic monitor sidecar.

Replays canned user-scenario flows. Returns pre/post outcomes from
scenarios.yaml so the verification path is deterministic across runs.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="synthetic-monitor", version="0.1.0")

_PATH = os.environ.get("SCENARIOS_PATH", "/app/data/scenarios.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_PATH).read_text()) or {}


class ReplayRequest(BaseModel):
    scenario_ids: list[str]
    after_fix: bool = False


class ScenarioResult(BaseModel):
    scenario_id: str
    matched: bool
    passed: bool
    detail: dict[str, Any]


class ReplayResponse(BaseModel):
    results: list[ScenarioResult]
    overall_passed: bool


@app.post("/replay", response_model=ReplayResponse)
async def replay(req: ReplayRequest) -> ReplayResponse:
    out: list[ScenarioResult] = []
    table = _corpus.get("scenarios") or {}
    for sid in req.scenario_ids:
        entry = table.get(sid)
        if entry is None:
            out.append(ScenarioResult(scenario_id=sid, matched=False, passed=False, detail={"reason": "unknown scenario"}))
            continue
        outcome = entry.get("post_fix" if req.after_fix else "pre_fix") or {}
        out.append(ScenarioResult(
            scenario_id=sid, matched=True,
            passed=bool(outcome.get("passed", False)),
            detail=outcome,
        ))
    return ReplayResponse(results=out, overall_passed=bool(out) and all(r.passed for r in out))


@app.get("/scenarios")
async def list_scenarios() -> dict[str, Any]:
    return {"scenarios": list((_corpus.get("scenarios") or {}).keys())}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "synthetic-monitor", "version": "0.1.0",
            "scenario_count": len(_corpus.get("scenarios") or {})}
