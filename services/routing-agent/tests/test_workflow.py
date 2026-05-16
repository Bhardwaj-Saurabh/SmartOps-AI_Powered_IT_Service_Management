"""Offline tests for the Routing Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import AgentError, SemanticPlaneError
from routing.config import AgentConfig
from routing.models import (
    ClassificationSlice,
    IncidentSlice,
    PrioritySlice,
    RoutingInput,
)
from routing.workflow import RoutingRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "routing-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "routing-rules.yaml").read_text()
    )
    sc = AsyncMock()

    async def query_rule(*, domain: str, **_):
        return rules[domain]

    sc.query_rule.side_effect = query_rule
    return sc


def _payload(*, service_area="network", category="vpn", priority="P3") -> RoutingInput:
    return RoutingInput(
        incident=IncidentSlice(incident_id="INC-RT001", affected_service="vpn", symptoms_summary="vpn drops"),
        classification=ClassificationSlice(service_area=service_area, category=category),
        priority=PrioritySlice(priority=priority),
    )


def _runner(cfg, *, directory_rows, skill_rows, llm_ranked, semantic_stub) -> RoutingRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps({"ranked": llm_ranked})})()
    gateway.chat_completion.side_effect = chat

    directory = AsyncMock()
    directory.lookup.return_value = directory_rows
    skill_matrix = AsyncMock()
    skill_matrix.score.return_value = {"required_skills": ["x"], "team_scores": skill_rows}

    return RoutingRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        directory=directory, skill_matrix=skill_matrix,
    )


@pytest.mark.asyncio
async def test_routing_picks_high_skill_match(cfg, semantic_stub):
    runner = _runner(
        cfg,
        directory_rows=[
            {"team_id": "network-ops",      "matched": True, "available": True,  "queue_depth": 3},
            {"team_id": "platform-engineering", "matched": True, "available": True, "queue_depth": 5},
        ],
        skill_rows=[
            {"team_id": "network-ops",          "match_score": 0.95, "matched_skills": ["networking", "vpn-tunnels"], "missing_skills": []},
            {"team_id": "platform-engineering", "match_score": 0.45, "matched_skills": ["networking"],                  "missing_skills": ["vpn-tunnels"]},
        ],
        llm_ranked=[
            {"team_id": "network-ops", "score": 0.9, "reasoning": "best fit"},
            {"team_id": "platform-engineering", "score": 0.5, "reasoning": "partial fit"},
        ],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.assigned_team == "network-ops"


@pytest.mark.asyncio
async def test_overloaded_team_excluded(cfg, semantic_stub):
    """A team above max_queue_depth (8) drops out of consideration."""
    runner = _runner(
        cfg,
        directory_rows=[
            {"team_id": "network-ops", "matched": True, "available": True, "queue_depth": 20},
            {"team_id": "platform-engineering", "matched": True, "available": True, "queue_depth": 1},
        ],
        skill_rows=[
            {"team_id": "network-ops", "match_score": 0.95, "matched_skills": ["a"], "missing_skills": []},
            {"team_id": "platform-engineering", "match_score": 0.45, "matched_skills": ["a"], "missing_skills": []},
        ],
        llm_ranked=[
            {"team_id": "platform-engineering", "score": 0.6, "reasoning": "only available"},
        ],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.assigned_team == "platform-engineering"
    excluded = next(c for c in result.candidate_ranking if c.team_id == "network-ops")
    assert excluded.excluded_reason is not None and "queue_depth" in excluded.excluded_reason


@pytest.mark.asyncio
async def test_p1_pulls_in_priority_overrides(cfg, semantic_stub):
    """P1 should add incident-commander/secops/network-ops to the candidate pool."""
    runner = _runner(
        cfg,
        directory_rows=[
            {"team_id": "network-ops", "matched": True, "available": True, "queue_depth": 1},
            {"team_id": "platform-engineering", "matched": True, "available": True, "queue_depth": 1},
            {"team_id": "incident-commander", "matched": True, "available": True, "queue_depth": 0},
            {"team_id": "secops", "matched": True, "available": True, "queue_depth": 0},
        ],
        skill_rows=[
            {"team_id": "network-ops", "match_score": 0.9, "matched_skills": ["a"], "missing_skills": []},
            {"team_id": "platform-engineering", "match_score": 0.4, "matched_skills": ["a"], "missing_skills": []},
            {"team_id": "incident-commander", "match_score": 0.0, "matched_skills": [], "missing_skills": ["a"]},
            {"team_id": "secops", "match_score": 0.3, "matched_skills": ["a"], "missing_skills": []},
        ],
        llm_ranked=[
            {"team_id": "incident-commander", "score": 0.95, "reasoning": "P1"},
            {"team_id": "network-ops",        "score": 0.5,  "reasoning": "fit"},
        ],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload(priority="P1"))
    team_ids = {c.team_id for c in result.candidate_ranking}
    assert {"incident-commander", "secops"}.issubset(team_ids)


@pytest.mark.asyncio
async def test_no_candidates_raises(cfg, semantic_stub):
    """If routing_rules has no entry for the area, the agent fails the step."""
    runner = _runner(
        cfg,
        directory_rows=[], skill_rows=[], llm_ranked=[],
        semantic_stub=semantic_stub,
    )
    with pytest.raises(AgentError):
        await runner.run(_payload(service_area="unknown_area", category="x"))
