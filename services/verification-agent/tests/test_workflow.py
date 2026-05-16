"""Offline tests for the Verification Agent."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from verification.config import AgentConfig
from verification.models import (
    ClassificationSlice,
    FixResultSlice,
    IncidentSlice,
    PrioritySlice,
    VerificationInput,
)
from verification.workflow import VerificationRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "verification-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "verification-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _payload() -> VerificationInput:
    return VerificationInput(
        incident=IncidentSlice(incident_id="INC-V001", affected_service="okta-sso",
                               symptoms_summary="AADSTS50105 errors"),
        classification=ClassificationSlice(service_area="application", category="okta-sso"),
        priority=PrioritySlice(priority="P2"),
        fix_result=FixResultSlice(state="completed", selected_runbook_id="okta-ca-resync",
                                  rollback_token="snap-X", what_changed="Re-synced CA group"),
        scenario_ids=["okta-saml-roundtrip"],
    )


def _runner(cfg, *, health_passed: bool, synthetic_passed: bool, comparison_improved: bool,
            llm_verdict: dict, semantic_stub: AsyncMock) -> VerificationRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps(llm_verdict)})()
    gateway.chat_completion.side_effect = chat

    health = AsyncMock()
    health.run.return_value = {"overall_passed": health_passed, "probes": [{"name": "x", "passed": health_passed}]}
    synthetic = AsyncMock()
    synthetic.replay.return_value = {"overall_passed": synthetic_passed, "results": [{"scenario_id": "x", "passed": synthetic_passed}]}
    comparison = AsyncMock()
    comparison.compare.return_value = {
        "overall_improved": comparison_improved,
        "improved_count": 2 if comparison_improved else 0,
        "regressed_count": 0,
        "metrics": [],
    }
    metrics = AsyncMock()
    metrics.query.return_value = {"baseline": {"x": 10.0}, "at_incident": {"x": 1.0}, "deltas": {"x": -90.0}}

    return VerificationRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        health=health, synthetic=synthetic, comparison=comparison, metrics=metrics,
    )


@pytest.mark.asyncio
async def test_all_signals_pass_emits_verified(cfg, semantic_stub):
    runner = _runner(
        cfg,
        health_passed=True, synthetic_passed=True, comparison_improved=True,
        llm_verdict={"fix_verified": True, "confidence": 0.9, "reasoning": "all clear", "residual_concerns": []},
        semantic_stub=semantic_stub,
    )
    out = await runner.run(_payload())
    assert out.fix_verified is True
    assert out.confidence >= 0.7


@pytest.mark.asyncio
async def test_deterministic_floor_overrides_optimistic_llm(cfg, semantic_stub):
    """LLM says fix_verified=true but health+synthetic+comparison all fail
    → final fix_verified=false. The orchestrator's saga then rolls back."""
    runner = _runner(
        cfg,
        health_passed=False, synthetic_passed=False, comparison_improved=False,
        llm_verdict={"fix_verified": True, "confidence": 0.85, "reasoning": "looks ok to me", "residual_concerns": []},
        semantic_stub=semantic_stub,
    )
    out = await runner.run(_payload())
    assert out.fix_verified is False


@pytest.mark.asyncio
async def test_low_llm_confidence_blocks_verification(cfg, semantic_stub):
    runner = _runner(
        cfg,
        health_passed=True, synthetic_passed=True, comparison_improved=True,
        llm_verdict={"fix_verified": True, "confidence": 0.3, "reasoning": "unsure", "residual_concerns": ["maybe flaky"]},
        semantic_stub=semantic_stub,
    )
    out = await runner.run(_payload())
    assert out.fix_verified is False   # below min_to_emit_verified


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(
        cfg,
        health_passed=True, synthetic_passed=True, comparison_improved=True,
        llm_verdict={"fix_verified": True, "confidence": 0.9, "reasoning": "", "residual_concerns": []},
        semantic_stub=failing,
    )
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
