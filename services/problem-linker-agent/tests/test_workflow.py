"""Offline tests for the Problem Linker Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from problem_linker.config import AgentConfig
from problem_linker.models import (
    ClassificationSlice,
    DiagnosisSlice,
    IncidentSlice,
    LinkerInput,
)
from problem_linker.workflow import LinkerRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "problem-linker-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "problem-linker-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _payload(*, service_area="application", category="okta-sso", signature="okta-ca-sync") -> LinkerInput:
    return LinkerInput(
        incident=IncidentSlice(incident_id="INC-PL-1", affected_service="okta-sso",
                               symptoms_summary="AADSTS50105"),
        classification=ClassificationSlice(service_area=service_area, category=category),
        diagnosis=DiagnosisSlice(root_cause="CA group sync lapsed"),
        similarity_signature=signature,
    )


def _runner(cfg, *, history_incidents: list[dict[str, Any]], open_problems: list[dict[str, Any]],
            llm_assessment: dict[str, Any] | None = None,
            semantic_stub: AsyncMock) -> LinkerRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps(llm_assessment or {
            "is_systemic": False, "confidence": 0.4,
            "reasoning": "stub", "recommended_problem_title": "",
            "recurrence_pattern": "", "scope_note": "",
        })})()
    gateway.chat_completion.side_effect = chat

    history = AsyncMock()
    history.query.return_value = {"incidents": history_incidents, "open_problems": open_problems}

    clustering = AsyncMock()
    # Synthesise clusters from the input (group by signature).
    async def cluster_call(*, incidents):
        by_sig: dict[str, list[dict]] = {}
        for inc in incidents:
            by_sig.setdefault(inc.get("similarity_signature", ""), []).append(inc)
        out = []
        for sig, members in by_sig.items():
            size = len(members)
            cohesion = 0.4 if size <= 1 else (0.7 if size == 2 else min(1.0, 0.85 + (size - 3) * 0.03))
            out.append({
                "signature": sig,
                "incident_ids": [m["incident_id"] for m in members],
                "size": size, "cohesion": cohesion,
                "distinct_reporter_departments": [],
                "distinct_services": [],
            })
        return {"clusters": out}
    clustering.cluster.side_effect = cluster_call

    return LinkerRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        incident_history=history, clustering=clustering,
    )


@pytest.mark.asyncio
async def test_signature_matches_open_problem_links(cfg, semantic_stub):
    runner = _runner(
        cfg,
        history_incidents=[
            {"incident_id": "H1", "similarity_signature": "okta-ca-sync", "service_area": "application",
             "category": "okta-sso", "affected_service": "okta-sso", "root_cause": "x",
             "reporter_department": "sales", "closed_at_days_ago": 5},
        ],
        open_problems=[
            {"problem_id": "PRB-001", "title": "Okta CA group lapses",
             "similarity_signature": "okta-ca-sync", "linked_incidents": ["H1"], "state": "open"},
        ],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.decision == "linked"
    assert result.linked_problem_id == "PRB-001"


@pytest.mark.asyncio
async def test_cluster_meets_threshold_and_llm_says_systemic(cfg, semantic_stub):
    runner = _runner(
        cfg,
        history_incidents=[
            {"incident_id": f"H{i}", "similarity_signature": "ca-group-sync-lapsed",
             "service_area": "application", "category": "okta-sso", "affected_service": "okta-sso",
             "root_cause": "x", "reporter_department": d, "closed_at_days_ago": 5}
            for i, d in enumerate(["sales", "finance", "operations"])
        ],
        open_problems=[],
        llm_assessment={
            "is_systemic": True, "confidence": 0.85,
            "reasoning": "three departments same signature in 5 days",
            "recommended_problem_title": "Okta CA group sync lapses",
            "recurrence_pattern": "Weekly", "scope_note": "Cross-department",
        },
        semantic_stub=semantic_stub,
    )
    payload = _payload(signature="ca-group-sync-lapsed")
    result = await runner.run(payload)
    assert result.decision == "new_problem_recommended"
    assert "Okta CA group" in (result.recommended_problem_title or "")


@pytest.mark.asyncio
async def test_below_threshold_returns_below_threshold(cfg, semantic_stub):
    runner = _runner(
        cfg,
        history_incidents=[
            {"incident_id": "H1", "similarity_signature": "rare-thing", "service_area": "application",
             "category": "okta-sso", "affected_service": "okta-sso", "root_cause": "x",
             "reporter_department": "finance", "closed_at_days_ago": 10},
        ],
        open_problems=[],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload(signature="rare-thing"))
    assert result.decision == "below_threshold"


@pytest.mark.asyncio
async def test_not_eligible_category_short_circuits(cfg, semantic_stub):
    runner = _runner(
        cfg,
        history_incidents=[
            {"incident_id": f"H{i}", "similarity_signature": "sig", "service_area": "collaboration",
             "category": "email", "affected_service": "exchange", "root_cause": "x",
             "reporter_department": "ops", "closed_at_days_ago": 5}
            for i in range(5)
        ],
        open_problems=[],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload(service_area="collaboration", category="email", signature="sig"))
    assert result.decision == "not_eligible"


@pytest.mark.asyncio
async def test_no_history_returns_no_history(cfg, semantic_stub):
    runner = _runner(cfg, history_incidents=[], open_problems=[], semantic_stub=semantic_stub)
    result = await runner.run(_payload())
    assert result.decision == "no_history"


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, history_incidents=[], open_problems=[], semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
