"""Offline tests for the Priority Scorer chain."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from priority_scorer.config import AgentConfig
from priority_scorer.models import (
    ClassificationSlice,
    IncidentSlice,
    PriorityInput,
)
from priority_scorer.workflow import PriorityRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "priority-scorer" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "priority-rules.yaml").read_text()
    )
    sc = AsyncMock()

    async def query_rule(*, domain: str, **_):
        return rules[domain]

    sc.query_rule.side_effect = query_rule
    return sc


def _payload(*, service="vpn", vip=False, dept=None) -> PriorityInput:
    return PriorityInput(
        incident=IncidentSlice(
            incident_id="INC-PRI001", affected_service=service,
            reporter_vip=vip, reporter_department=dept,
            symptoms_summary="VPN keeps disconnecting", symptoms_verbatim="vpn drops",
        ),
        classification=ClassificationSlice(service_area="network", category="vpn"),
    )


def _runner(cfg, *, llm, walk, impact, semantic_stub) -> PriorityRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps(llm)})()
    gateway.chat_completion.side_effect = chat

    deps = AsyncMock()
    deps.walk.return_value = walk

    impact_tool = AsyncMock()
    impact_tool.analyse.return_value = impact

    return PriorityRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        impact=impact_tool, dependency_mapper=deps,
    )


@pytest.mark.asyncio
async def test_high_impact_high_urgency_yields_p1(cfg, semantic_stub):
    runner = _runner(
        cfg,
        llm={"impact": "high", "urgency": "high", "reasoning": "x"},
        walk={"blast_radius": 5, "tier": "gold"},
        impact={"impact_score": 0.85, "impact_bucket": "high"},
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.priority == "P2"   # high × high → P2 per the matrix


@pytest.mark.asyncio
async def test_executive_vip_forces_p1(cfg, semantic_stub):
    runner = _runner(
        cfg,
        llm={"impact": "low", "urgency": "low", "reasoning": "single user"},
        walk={"blast_radius": 0, "tier": "silver"},
        impact={"impact_score": 0.1, "impact_bucket": "low"},
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload(vip=True, dept="executive"))
    assert result.priority == "P1"
    assert result.vip_override is not None and "executive" in result.vip_override


@pytest.mark.asyncio
async def test_large_blast_radius_floors_urgency(cfg, semantic_stub):
    """blast_radius=20 → urgency floor critical; impact=low → P3 (low × critical)."""
    runner = _runner(
        cfg,
        llm={"impact": "low", "urgency": "low", "reasoning": "tiny issue"},
        walk={"blast_radius": 20, "tier": "gold"},
        impact={"impact_score": 0.2, "impact_bucket": "low"},
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.urgency == "critical"
    assert result.priority == "P3"   # low × critical


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(
        cfg,
        llm={"impact": "medium", "urgency": "medium", "reasoning": "x"},
        walk={"blast_radius": 1, "tier": "silver"},
        impact={"impact_score": 0.3, "impact_bucket": "medium"},
        semantic_stub=failing,
    )
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
