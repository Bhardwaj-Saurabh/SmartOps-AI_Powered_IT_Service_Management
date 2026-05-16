"""Offline tests for the SLA Monitor Agent."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from sla_monitor.config import AgentConfig
from sla_monitor.models import SLAInput, StateTransition
from sla_monitor.workflow import SLARunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "sla-monitor-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "sla-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _runner(cfg, *, raw_minutes: float, paused_minutes: float, currently_paused: bool,
            semantic_stub) -> SLARunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps({
            "narrative": "stub narrative", "recommended_action": "",
        })})()
    gateway.chat_completion.side_effect = chat

    clock = AsyncMock()
    clock.elapsed_24x7.return_value = {"started_at_epoch": 0, "end_epoch": 0,
                                       "elapsed_minutes": raw_minutes}
    clock.elapsed_business.return_value = clock.elapsed_24x7.return_value
    rules = AsyncMock()
    rules.pauses.return_value = {"paused_minutes": paused_minutes,
                                  "currently_paused": currently_paused, "pause_segments": []}

    return SLARunner(cfg=cfg, gateway=gateway, semantic=semantic_stub,
                     clock=clock, rules=rules)


@pytest.mark.asyncio
async def test_p2_silver_under_response_target(cfg, semantic_stub):
    """120 min P2/silver response target; 60 minutes elapsed → 50% consumed."""
    runner = _runner(cfg, raw_minutes=60.0, paused_minutes=0.0, currently_paused=False,
                     semantic_stub=semantic_stub)
    result = await runner.run(SLAInput(
        incident_id="INC-SLA-1", priority="P2", customer_tier="silver", region="UK",
        started_at_epoch=1_700_000_000,
        state_transitions=[StateTransition(state="new", at_epoch=1_700_000_000)],
    ))
    assert result.targets.response == 120
    assert result.response_consumed_pct == 50.0
    assert not result.response_breached
    assert not result.response_warning   # default warning is 75 for P2


@pytest.mark.asyncio
async def test_p1_gold_breaches_at_61_minutes(cfg, semantic_stub):
    """P1/gold resolve target is 60 min. 61 min elapsed → resolve breached."""
    runner = _runner(cfg, raw_minutes=61.0, paused_minutes=0.0, currently_paused=False,
                     semantic_stub=semantic_stub)
    result = await runner.run(SLAInput(
        incident_id="INC-SLA-2", priority="P1", customer_tier="gold", region="US",
        started_at_epoch=1_700_000_000,
        state_transitions=[StateTransition(state="new", at_epoch=1_700_000_000)],
    ))
    assert result.resolve_breached is True
    assert result.response_breached is True   # response target was 15 min


@pytest.mark.asyncio
async def test_pause_states_reduce_adjusted_elapsed(cfg, semantic_stub):
    runner = _runner(cfg, raw_minutes=120.0, paused_minutes=60.0, currently_paused=False,
                     semantic_stub=semantic_stub)
    result = await runner.run(SLAInput(
        incident_id="INC-SLA-3", priority="P2", customer_tier="silver", region="UK",
        started_at_epoch=1_700_000_000,
        state_transitions=[
            StateTransition(state="new", at_epoch=1_700_000_000),
            StateTransition(state="needs_clarification", at_epoch=1_700_000_000 + 1800),
            StateTransition(state="working", at_epoch=1_700_000_000 + 5400),
        ],
    ))
    assert result.elapsed_adjusted_minutes == 60.0
    assert result.response_consumed_pct == 50.0


@pytest.mark.asyncio
async def test_warning_triggers_below_breach(cfg, semantic_stub):
    """P2 default warning 75% — at 100/120 resolve target = 83%."""
    runner = _runner(cfg, raw_minutes=200.0, paused_minutes=0.0, currently_paused=False,
                     semantic_stub=semantic_stub)
    result = await runner.run(SLAInput(
        incident_id="INC-SLA-W", priority="P2", customer_tier="silver", region="UK",
        started_at_epoch=1_700_000_000,
        state_transitions=[StateTransition(state="new", at_epoch=1_700_000_000)],
    ))
    # P2 silver: response=120, resolve=480. 200 min → response breached, resolve warning.
    assert result.response_breached is True
    assert result.resolve_warning is True
    assert result.resolve_breached is False


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, raw_minutes=10.0, paused_minutes=0.0, currently_paused=False,
                     semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(SLAInput(
            incident_id="INC-X", priority="P2", customer_tier="silver", region="UK",
            started_at_epoch=1_700_000_000,
        ))
