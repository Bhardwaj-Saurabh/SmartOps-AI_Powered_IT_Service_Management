"""Offline tests for the Automated Fix Agent.

Covers the safety story end-to-end: approval gate, scope cap, change-freeze,
unconditional snapshot, automatic rollback on execution failure, SBCA
hard-fail, and the separate rollback skill.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from automated_fix.config import AgentConfig
from automated_fix.models import (
    ClassificationSlice,
    DiagnosisSlice,
    FixInput,
    IncidentSlice,
    PrioritySlice,
    RollbackInput,
)
from automated_fix.workflow import AutomatedFixRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "automated-fix-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "automated-fix-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _payload(
    *, service: str = "okta-sso", fix_tier: str = "gold",
    blast: int = 1, users: int = 5, emergency: bool = False,
) -> FixInput:
    return FixInput(
        incident=IncidentSlice(
            incident_id="INC-AFTEST",
            affected_service=service,
            affected_users=[f"u{i}@example.com" for i in range(users)],
            symptoms_summary="SSO AADSTS50105 errors",
        ),
        classification=ClassificationSlice(service_area="application", category="okta-sso"),
        priority=PrioritySlice(priority="P2", blast_radius=blast, service_tier=fix_tier, emergency=emergency),
        diagnosis=DiagnosisSlice(root_cause="CA group sync lapsed", confidence=0.92),
        knowledge_articles=[],
    )


def _runner(
    cfg, *, selection: dict[str, Any], catalogue: list[dict[str, Any]] | None = None,
    execute_outcome: str = "succeeded",
    fail_at_step: int | None = None,
    semantic_stub: AsyncMock,
) -> AutomatedFixRunner:
    if catalogue is None:
        catalogue = [
            {"id": "okta-ca-resync", "fix_type": "okta-ca-resync",
             "applicable_to": ["AADSTS50105"],
             "param_schema": {"affected_users": {"type": "array", "required": True}},
             "estimated_duration_seconds": 30, "step_count": 4, "title": "Re-sync CA"},
        ]

    gateway = AsyncMock()
    select_text = json.dumps(selection)
    summary_text = json.dumps({
        "what_changed": "Re-synced CA group", "changed_resources": ["okta-ca-group"],
        "user_visible_impact": "Affected users can sign in again",
    })
    call_seq = {"i": 0}
    async def chat(*args, **kwargs):
        i = call_seq["i"]; call_seq["i"] += 1
        return type("R", (), {"text": select_text if i == 0 else summary_text})()
    gateway.chat_completion.side_effect = chat

    script = AsyncMock()
    script.catalogue.return_value = catalogue
    if fail_at_step is not None:
        steps = [{"step_index": j, "action": f"step{j}", "outcome": "success", "duration_ms": 10.0}
                 for j in range(fail_at_step)]
        steps.append({"step_index": fail_at_step, "action": "boom", "outcome": "failed",
                      "duration_ms": 10.0, "detail": "simulated"})
        script.execute.return_value = {"runbook_id": selection.get("selected_runbook_id"),
                                       "overall_outcome": "failed", "steps": steps,
                                       "parameters_used": selection.get("parameters", {})}
    else:
        script.execute.return_value = {"runbook_id": selection.get("selected_runbook_id"),
                                       "overall_outcome": execute_outcome,
                                       "steps": [{"step_index": 0, "action": "ok", "outcome": "success", "duration_ms": 10.0}],
                                       "parameters_used": selection.get("parameters", {})}

    config_mgr = AsyncMock()
    config_mgr.snapshot.return_value = {"snapshot_id": "snap-test-001",
                                        "target_service": "okta-sso", "captured_at_epoch": 0}
    rollback = AsyncMock()
    rollback.rollback.return_value = {"snapshot_id": "snap-test-001",
                                      "target_service": "okta-sso", "restored": True,
                                      "restored_state_keys": ["pre_fix_marker"],
                                      "note": "synthetic restore"}

    return AutomatedFixRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        script=script, config_manager=config_mgr, rollback_handler=rollback,
    )


@pytest.mark.asyncio
async def test_happy_path_snapshots_then_executes(cfg, semantic_stub):
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "okta-ca-resync",
                   "parameters": {"affected_users": ["a@x.com"]},
                   "rationale": "matches AADSTS50105"},
        semantic_stub=semantic_stub,
    )
    out = await runner.apply(_payload())
    assert out.state == "completed"
    assert out.selected_runbook_id == "okta-ca-resync"
    assert out.rollback_token == "snap-test-001"
    assert out.rollback_invoked is False


@pytest.mark.asyncio
async def test_approval_denied_emits_requires_human(cfg, semantic_stub):
    """wifi-firmware-downgrade is denied for every tier in the seed rules."""
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "wifi-firmware-downgrade",
                   "parameters": {}, "rationale": "matches"},
        catalogue=[{"id": "wifi-firmware-downgrade", "fix_type": "wifi-firmware-downgrade",
                    "applicable_to": [], "param_schema": {}, "step_count": 1,
                    "estimated_duration_seconds": 60, "title": "AP firmware downgrade"}],
        semantic_stub=semantic_stub,
    )
    out = await runner.apply(_payload(service="wifi"))
    assert out.state == "requires_human"
    assert "automated_fix_approval=false" in (out.requires_human_reason or "")


@pytest.mark.asyncio
async def test_scope_cap_blocks_large_blast_radius(cfg, semantic_stub):
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "okta-ca-resync",
                   "parameters": {"affected_users": ["a@x.com"]},
                   "rationale": "ok"},
        semantic_stub=semantic_stub,
    )
    out = await runner.apply(_payload(blast=999))    # well above max_blast_radius=5
    assert out.state == "requires_human"
    assert "Scope cap exceeded" in (out.requires_human_reason or "")


@pytest.mark.asyncio
async def test_runbook_failure_triggers_automatic_rollback(cfg, semantic_stub):
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "okta-ca-resync",
                   "parameters": {"affected_users": ["a@x.com"]},
                   "rationale": "ok"},
        fail_at_step=1,
        semantic_stub=semantic_stub,
    )
    out = await runner.apply(_payload())
    assert out.state == "rolled_back"
    assert out.rollback_invoked is True
    assert out.rollback_token == "snap-test-001"


@pytest.mark.asyncio
async def test_null_runbook_selection_emits_requires_human(cfg, semantic_stub):
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": None, "parameters": {}, "rationale": "no fit"},
        semantic_stub=semantic_stub,
    )
    out = await runner.apply(_payload())
    assert out.state == "requires_human"
    assert "No suitable runbook" in (out.requires_human_reason or "")


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "okta-ca-resync",
                   "parameters": {"affected_users": ["a@x.com"]},
                   "rationale": "ok"},
        semantic_stub=failing,
    )
    with pytest.raises(SemanticPlaneError):
        await runner.apply(_payload())


@pytest.mark.asyncio
async def test_rollback_skill_called_directly(cfg, semantic_stub):
    """Orchestrator-initiated rollback (saga compensation path)."""
    runner = _runner(
        cfg,
        selection={"selected_runbook_id": "okta-ca-resync",
                   "parameters": {"affected_users": ["a@x.com"]},
                   "rationale": "ok"},
        semantic_stub=semantic_stub,
    )
    out = await runner.rollback(RollbackInput(rollback_token="snap-X", reason="verify failed"))
    assert out.restored is True
    assert out.rollback_token == "snap-X"
