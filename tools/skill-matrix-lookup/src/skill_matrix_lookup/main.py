"""Skill matrix lookup sidecar.

Given a (service_area, category) tuple, returns:
  * the required skills for that incident class
  * each team's match score (sum of competencies over required skills,
    normalised by the number of required skills)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="skill-matrix-lookup", version="0.1.0")

_MATRIX_PATH = os.environ.get("SKILL_MATRIX_PATH", "/app/data/skill_matrix.yaml")
_matrix: dict[str, Any] = yaml.safe_load(Path(_MATRIX_PATH).read_text()) or {}


class ScoreRequest(BaseModel):
    service_area: str
    category: str
    candidate_team_ids: list[str] = Field(default_factory=list)
    """If empty, score against every team in the matrix."""


class TeamScore(BaseModel):
    team_id: str
    match_score: float
    matched_skills: list[str]
    missing_skills: list[str]


class ScoreResponse(BaseModel):
    required_skills: list[str]
    team_scores: list[TeamScore]


@app.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest) -> ScoreResponse:
    key = f"{req.service_area}/{req.category}"
    required = (_matrix.get("required_skills_by_category") or {}).get(key, [])
    competencies: dict[str, dict[str, float]] = _matrix.get("team_competencies") or {}
    team_ids = req.candidate_team_ids or list(competencies.keys())

    out: list[TeamScore] = []
    for tid in team_ids:
        team_skills = competencies.get(tid) or {}
        matched: list[str] = []
        missing: list[str] = []
        total = 0.0
        for sk in required:
            score_val = float(team_skills.get(sk, 0.0))
            if score_val > 0.0:
                matched.append(sk)
                total += score_val
            else:
                missing.append(sk)
        norm = (total / len(required)) if required else 0.0
        out.append(TeamScore(team_id=tid, match_score=norm, matched_skills=matched, missing_skills=missing))

    return ScoreResponse(required_skills=required, team_scores=out)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "skill-matrix-lookup", "version": "0.1.0"}
