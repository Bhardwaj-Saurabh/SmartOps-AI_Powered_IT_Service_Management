"""Offline tests for the I2R Primary Orchestrator runner.

Covers:
  * happy path Triage → Resolution → Closure chain composition via the flat
    summary artifacts each sub-orchestrator emits
  * SBCA-gated early escalation (matches blast radius / priority / VIP dept)
  * Triage INPUT_REQUIRED short-circuit
  * Resolution failure + SBCA-gated closure-on-failure (runs closure anyway)
  * Resolution failure + SBCA says don't run closure → chain stops
  * Hard-fail on SBCA error during escalation rule lookup
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from a2a_server.models import (
    DataPart,
    Task,
    TaskArtifact,
    TaskStatusModel,
)
from di_framework_core import SemanticPlaneError, TaskStatus
from i2r.config import OrchestratorConfig
from i2r.workflow import I2RRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "i2r-primary-orchestrator" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> OrchestratorConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, OrchestratorConfig)


def _task(state: TaskStatus, artifacts: list[TaskArtifact] | None = None) -> Task:
    return Task(
        contextId="ctx",
        status=TaskStatusModel(state=state, message=None),
        artifacts=artifacts or [],
    )


def _completed(artifact_name: str, data: dict[str, Any]) -> Task:
    return _task(TaskStatus.COMPLETED,
                 [TaskArtifact(name=artifact_name, parts=[DataPart(data=data)])])


def _triage_summary(*, priority: str = "P2", blast_radius: int = 1,
                    reporter_department: str = "sales") -> dict[str, Any]:
    return {
        "incident": {"incident_id": "INC-I2R-1", "affected_service": "okta-sso",
                     "symptoms_summary": "SSO down", "reporter": "alice@example.sales",
                     "reporter_department": reporter_department},
        "classification": {"service_area": "application", "category": "okta-sso"},
        "priority": {"priority": priority, "service_tier": "silver",
                     "blast_radius": blast_radius},
        "routing": {"queue": "iam-tier2"},
    }


def _resolution_summary(*, fix_verified: bool = True) -> dict[str, Any]:
    return {
        "diagnosis": {"root_cause": "CA group sync lapsed"},
        "knowledge": {"top_runbooks": ["okta-ca-resync"]},
        "fix_result": {"state": "completed", "selected_runbook_id": "okta-ca-resync",
                       "what_changed": "Re-synced CA group"},
        "verification": {"fix_verified": fix_verified, "confidence": 0.9},
    }


def _closure_summary() -> dict[str, Any]:
    return {
        "communications_sent": {"audiences_reached": ["end_user"]},
        "sla_status": {"response_breached": False, "resolve_breached": False},
        "documentation": {"incident_note": "fixed"},
        "problem_link": {"decision": "below_threshold"},
    }


def _initial() -> dict[str, Any]:
    return {
        "trigger": "resolution",
        "current_state": "new",
        "region": "UK",
        "started_at_epoch": 1_700_000_000,
        "state_transitions": [{"state": "new", "at_epoch": 1_700_000_000}],
        "raw_email": {"subject": "okta down", "body": "..."},
    }


@pytest.fixture
def semantic_stub():
    """Default SBCA: no escalation criteria match anything strict; closure-on-failure default true."""
    sc = AsyncMock()

    async def q(*, domain: str, **_):
        if domain == "i2r_escalation_criteria":
            return {"priorities": ["P1"], "blast_radius_min": 8,
                    "vip_reporter_departments": ["executive", "board"]}
        if domain == "i2r_run_closure_on_failed_resolution":
            return {"default": True}
        return {}
    sc.query_rule.side_effect = q
    return sc


def _wire_capability_clients(monkeypatch, *, clients: dict[str, AsyncMock]) -> None:
    async def fake_from_capability(name, **kwargs):
        if name not in clients:
            raise AssertionError(f"unexpected capability lookup: {name}")
        return clients[name]
    monkeypatch.setattr("i2r.workflow.A2AClient.from_capability", fake_from_capability)


def test_resolve_ref_dot_walks(cfg, semantic_stub):
    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    initial = {"trigger": "resolution", "priority": {"service_tier": "gold"}}
    outputs = [{"artifacts": [{"name": "triage_summary",
                               "data": {"priority": {"priority": "P1", "blast_radius": 9}}}]}]
    assert runner._resolve_ref("input.trigger", initial, outputs) == "resolution"  # noqa: SLF001
    assert runner._resolve_ref("input.priority.service_tier", initial, outputs) == "gold"  # noqa: SLF001
    assert runner._resolve_ref("0.triage_summary.priority.priority", initial, outputs) == "P1"  # noqa: SLF001
    assert runner._resolve_ref("0.triage_summary.does_not_exist", initial, outputs) is None  # noqa: SLF001


def test_should_escalate_matches_priority(cfg, semantic_stub):
    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    rule = {"priorities": ["P1"], "blast_radius_min": 8, "vip_reporter_departments": ["board"]}
    assert runner._should_escalate(_triage_summary(priority="P1"), rule)  # noqa: SLF001
    assert not runner._should_escalate(_triage_summary(priority="P3"), rule)  # noqa: SLF001


def test_should_escalate_matches_blast_radius(cfg, semantic_stub):
    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    rule = {"priorities": ["P1"], "blast_radius_min": 8, "vip_reporter_departments": []}
    assert runner._should_escalate(_triage_summary(priority="P2", blast_radius=10), rule)  # noqa: SLF001
    assert not runner._should_escalate(_triage_summary(priority="P2", blast_radius=3), rule)  # noqa: SLF001


def test_should_escalate_matches_vip(cfg, semantic_stub):
    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    rule = {"priorities": [], "blast_radius_min": 99,
            "vip_reporter_departments": ["executive", "board"]}
    assert runner._should_escalate(  # noqa: SLF001
        _triage_summary(priority="P3", blast_radius=1, reporter_department="Board"), rule,
    )


@pytest.mark.asyncio
async def test_happy_path_chain(cfg, semantic_stub, monkeypatch):
    triage = AsyncMock(); triage.message_send.return_value = _completed(
        "triage_summary", _triage_summary())
    resolve = AsyncMock(); resolve.message_send.return_value = _completed(
        "resolution_summary", _resolution_summary())
    close = AsyncMock(); close.message_send.return_value = _completed(
        "closure_summary", _closure_summary())

    _wire_capability_clients(monkeypatch, clients={
        "triage_incident": triage,
        "resolve_incident": resolve,
        "close_incident": close,
    })

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    assert result["i2r_state"] == "closed"
    assert len(result["steps"]) == 3
    assert result["escalation_triggered"] is False
    assert result["failed_step_index"] is None

    # Resolution gets the rolled-up composite from Triage's flat summary.
    resolve_payload = resolve.message_send.await_args.kwargs["parts"][0]["data"]
    assert resolve_payload["incident"]["incident_id"] == "INC-I2R-1"
    assert resolve_payload["priority"]["priority"] == "P2"

    # Closure gets composite from both Triage AND Resolution summaries + raw input.
    close_payload = close.message_send.await_args.kwargs["parts"][0]["data"]
    assert close_payload["incident"]["incident_id"] == "INC-I2R-1"
    assert close_payload["diagnosis"]["root_cause"] == "CA group sync lapsed"
    assert close_payload["verification"]["fix_verified"] is True
    assert close_payload["region"] == "UK"
    assert close_payload["trigger"] == "resolution"


@pytest.mark.asyncio
async def test_escalation_fires_when_p1(cfg, semantic_stub, monkeypatch):
    triage = AsyncMock(); triage.message_send.return_value = _completed(
        "triage_summary", _triage_summary(priority="P1", blast_radius=5))
    resolve = AsyncMock(); resolve.message_send.return_value = _completed(
        "resolution_summary", _resolution_summary())
    close = AsyncMock(); close.message_send.return_value = _completed(
        "closure_summary", _closure_summary())
    comm = AsyncMock(); comm.message_send.return_value = _completed(
        "communications_sent", {"audiences_reached": ["oncall"]})

    _wire_capability_clients(monkeypatch, clients={
        "triage_incident": triage,
        "resolve_incident": resolve,
        "close_incident": close,
        "send_status_update": comm,
    })

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    assert result["i2r_state"] == "closed"
    assert result["escalation_triggered"] is True

    # Communication called with trigger=escalation between Triage and Resolution.
    comm_payload = comm.message_send.await_args.kwargs["parts"][0]["data"]
    assert comm_payload["trigger"] == "escalation"
    assert comm_payload["priority"]["priority"] == "P1"


@pytest.mark.asyncio
async def test_triage_input_required_short_circuits(cfg, semantic_stub, monkeypatch):
    triage = AsyncMock(); triage.message_send.return_value = _task(TaskStatus.INPUT_REQUIRED)
    resolve = AsyncMock()
    close = AsyncMock()

    _wire_capability_clients(monkeypatch, clients={
        "triage_incident": triage,
        "resolve_incident": resolve,
        "close_incident": close,
    })

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    assert result["i2r_state"] == "triage_needs_input"
    assert len(result["steps"]) == 1
    resolve.message_send.assert_not_called()
    close.message_send.assert_not_called()


@pytest.mark.asyncio
async def test_resolution_fails_closure_runs_when_sbca_says_so(cfg, semantic_stub, monkeypatch):
    triage = AsyncMock(); triage.message_send.return_value = _completed(
        "triage_summary", _triage_summary())
    resolve = AsyncMock(); resolve.message_send.return_value = _task(TaskStatus.FAILED)
    close = AsyncMock(); close.message_send.return_value = _completed(
        "closure_summary", _closure_summary())

    _wire_capability_clients(monkeypatch, clients={
        "triage_incident": triage,
        "resolve_incident": resolve,
        "close_incident": close,
    })

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=semantic_stub)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    # SBCA default: run_closure_on_failed_resolution = true.
    assert len(result["steps"]) == 3
    close.message_send.assert_awaited()
    # Closure ran but the I2R chain is still considered closed (closure succeeded).
    # We track the resolution failure via failed_step_index = None? No — we set it
    # when closure was skipped. Since closure ran, the chain ends in "closed".
    assert result["i2r_state"] == "closed"


@pytest.mark.asyncio
async def test_resolution_fails_closure_skipped_when_sbca_says_no(cfg, monkeypatch):
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        if domain == "i2r_escalation_criteria":
            return {"priorities": ["P1"], "blast_radius_min": 99,
                    "vip_reporter_departments": []}
        if domain == "i2r_run_closure_on_failed_resolution":
            return {"default": False}
        return {}
    sc.query_rule.side_effect = q

    triage = AsyncMock(); triage.message_send.return_value = _completed(
        "triage_summary", _triage_summary())
    resolve = AsyncMock(); resolve.message_send.return_value = _task(TaskStatus.FAILED)
    close = AsyncMock()

    _wire_capability_clients(monkeypatch, clients={
        "triage_incident": triage,
        "resolve_incident": resolve,
        "close_incident": close,
    })

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=sc)
    result = await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")

    assert result["i2r_state"] == "resolution_failed"
    assert result["failed_step_index"] == 1
    close.message_send.assert_not_called()


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg, monkeypatch):
    sc = AsyncMock()
    sc.query_rule.side_effect = SemanticPlaneError("sbca down")

    triage = AsyncMock(); triage.message_send.return_value = _completed(
        "triage_summary", _triage_summary())

    _wire_capability_clients(monkeypatch, clients={"triage_incident": triage})

    runner = I2RRunner(cfg=cfg, registry_url="http://sbca:8444",
                       bearer_provider=None, semantic=sc)
    with pytest.raises(SemanticPlaneError):
        await runner.run(process="i2r", initial_payload=_initial(), correlation_id="cid")
