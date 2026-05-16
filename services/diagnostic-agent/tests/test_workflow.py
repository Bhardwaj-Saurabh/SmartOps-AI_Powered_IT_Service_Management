"""Offline tests for the Diagnostic Agent's evaluator-optimizer loop."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from diagnostic.config import AgentConfig
from diagnostic.models import (
    ClassificationSlice,
    DiagnosticInput,
    IncidentSlice,
)
from diagnostic.workflow import DiagnosticRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "diagnostic-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "diagnostic-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _runner(cfg, *, hypotheses, evaluator_scores, semantic_stub) -> DiagnosticRunner:
    """``hypotheses`` is a list (one per iteration). ``evaluator_scores`` likewise."""
    gateway = AsyncMock()
    call = {"chat": 0}

    async def chat(*args, **kwargs):
        # Even-indexed calls are generator, odd are evaluator (alternating per iter)
        i = call["chat"]
        call["chat"] += 1
        iter_idx = i // 2
        if i % 2 == 0:
            return type("R", (), {"text": json.dumps(hypotheses[min(iter_idx, len(hypotheses) - 1)])})()
        return type("R", (), {"text": json.dumps(evaluator_scores[min(iter_idx, len(evaluator_scores) - 1)])})()
    gateway.chat_completion.side_effect = chat

    logs = AsyncMock()
    logs.search.return_value = {"entries": [{"t": -5, "level": "ERROR", "source": "x", "message": "y"}], "summary": {"ERROR": 1}}
    metrics = AsyncMock()
    metrics.query.return_value = {"baseline": {"a": 1}, "at_incident": {"a": 50}, "deltas": {"a": 4900.0}}
    topology = AsyncMock()
    topology.walk.return_value = {"upstream_layers": [["dns"]], "tier": "gold"}

    return DiagnosticRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        log_aggregator=logs, metrics_query=metrics, topology_walker=topology,
    )


def _payload(service: str = "salesforce", symptoms: str = "auth failures across the org") -> DiagnosticInput:
    return DiagnosticInput(
        incident=IncidentSlice(incident_id="INC-D001", affected_service=service, symptoms_summary=symptoms),
        classification=ClassificationSlice(service_area="application", category="salesforce"),
    )


@pytest.mark.asyncio
async def test_known_issue_short_circuit(cfg, semantic_stub):
    """A symptom matching the SBCA known_issues table emits immediately with high confidence."""
    runner = _runner(
        cfg,
        hypotheses=[],   # should never be used
        evaluator_scores=[],
        semantic_stub=semantic_stub,
    )
    payload = _payload(service="vpn", symptoms="reports of MTU mismatch and disconnects")
    diag = await runner.run(payload)
    assert diag.matched_known_issue is True
    assert diag.iterations == 0
    assert diag.confidence > 0.9
    assert diag.workaround is not None


@pytest.mark.asyncio
async def test_high_confidence_converges_in_one_iteration(cfg, semantic_stub):
    runner = _runner(
        cfg,
        hypotheses=[{
            "candidate_causes": [{
                "cause": "AAD CA group sync lapsed",
                "cause_type": "configuration",
                "evidence": ["AADSTS50105 in logs"],
                "validation_idea": "check group membership",
            }],
            "best_index": 0,
        }],
        evaluator_scores=[{"confidence": 0.9, "supported": True, "reasoning": "evidence aligned"}],
        semantic_stub=semantic_stub,
    )
    diag = await runner.run(_payload())
    assert diag.state == "completed"
    assert diag.iterations == 1
    assert diag.confidence >= 0.75
    assert "AAD" in (diag.root_cause or "")


@pytest.mark.asyncio
async def test_low_confidence_iterates_then_emits_low_confidence(cfg, semantic_stub):
    """Below min_to_emit but above min_to_accept → state=low_confidence."""
    runner = _runner(
        cfg,
        hypotheses=[
            {"candidate_causes": [{"cause": "guess A", "cause_type": "external", "evidence": ["e1"], "validation_idea": "v"}], "best_index": 0},
            {"candidate_causes": [{"cause": "guess B", "cause_type": "external", "evidence": ["e2"], "validation_idea": "v"}], "best_index": 0},
            {"candidate_causes": [{"cause": "guess C", "cause_type": "external", "evidence": ["e3"], "validation_idea": "v"}], "best_index": 0},
        ],
        evaluator_scores=[
            {"confidence": 0.4, "supported": False, "reasoning": "weak"},
            {"confidence": 0.55, "supported": True, "reasoning": "stronger"},
            {"confidence": 0.6, "supported": True, "reasoning": "still not great"},
        ],
        semantic_stub=semantic_stub,
    )
    diag = await runner.run(_payload())
    assert diag.iterations == 3
    assert diag.state == "low_confidence"
    assert 0.4 <= diag.confidence < 0.75


@pytest.mark.asyncio
async def test_below_min_to_accept_emits_failed(cfg, semantic_stub):
    runner = _runner(
        cfg,
        hypotheses=[
            {"candidate_causes": [{"cause": "weak", "cause_type": "external", "evidence": ["x"], "validation_idea": "y"}], "best_index": 0},
        ] * 3,
        evaluator_scores=[{"confidence": 0.1, "supported": False, "reasoning": "nothing matches"}] * 3,
        semantic_stub=semantic_stub,
    )
    diag = await runner.run(_payload())
    assert diag.state == "failed"
    assert diag.confidence < 0.4


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, hypotheses=[], evaluator_scores=[], semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
