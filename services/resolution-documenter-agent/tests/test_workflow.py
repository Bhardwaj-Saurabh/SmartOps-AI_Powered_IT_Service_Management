"""Offline tests for the Resolution Documenter Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from resolution_documenter.config import AgentConfig
from resolution_documenter.models import (
    ClassificationSlice,
    DiagnosisSlice,
    DocumenterInput,
    FixResultSlice,
    IncidentSlice,
    VerificationSlice,
)
from resolution_documenter.workflow import DocumenterRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "resolution-documenter-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "documentation-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _payload() -> DocumenterInput:
    return DocumenterInput(
        incident=IncidentSlice(incident_id="INC-DOC-1", affected_service="okta-sso",
                               symptoms_summary="AADSTS50105 across users"),
        classification=ClassificationSlice(service_area="application", category="okta-sso"),
        diagnosis=DiagnosisSlice(root_cause="CA group sync lapsed", cause_type="configuration", confidence=0.92),
        fix_result=FixResultSlice(selected_runbook_id="okta-ca-resync",
                                   rollback_token="snap-X", what_changed="Re-synced CA group",
                                   changed_resources=["okta-ca-group"]),
        verification=VerificationSlice(fix_verified=True, reasoning="all signals pass"),
    )


def _runner(cfg, *, search_hits: list[dict[str, Any]], semantic_stub: AsyncMock) -> DocumenterRunner:
    gateway = AsyncMock()
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps({
            "title": "Okta CA group lapse — re-sync",
            "root_cause": "Conditional Access group SSO-Eligible missed daily sync",
            "fix_summary": "Re-added affected users; forced directory sync",
            "prevention": "Add a sync-failure alert",
            "validation": "User can sign in to Salesforce via SSO",
            "symptoms_seen_by_user": "AADSTS50105 error page",
            "applicable_services": ["okta-sso", "salesforce"],
            "applicable_keywords": ["AADSTS50105", "okta", "ca-group"],
        })})()
    gateway.chat_completion.side_effect = chat

    formatter = AsyncMock()
    formatter.render.return_value = {"template_id": "resolution-note-auth", "markdown": "# rendered"}
    writer = AsyncMock()
    writer.create.return_value = {"article_id": "KB-NEW-1234"}
    writer.update.return_value = {"article_id": "KB-EXISTING-9"}
    search = AsyncMock()
    search.search.return_value = search_hits

    return DocumenterRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        formatter=formatter, kb_writer=writer, kb_search=search,
    )


@pytest.mark.asyncio
async def test_high_effectiveness_match_updates_existing(cfg, semantic_stub):
    runner = _runner(
        cfg,
        search_hits=[{"article_id": "KB-EXISTING-9", "effectiveness_score": 0.85,
                       "title": "Okta SSO AADSTS50105"}],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.decision == "updated"
    assert result.article_id == "KB-EXISTING-9"


@pytest.mark.asyncio
async def test_no_close_match_creates_draft(cfg, semantic_stub):
    runner = _runner(cfg, search_hits=[], semantic_stub=semantic_stub)
    result = await runner.run(_payload())
    # publish_automatically=false in the seed rules → drafted
    assert result.decision == "drafted"
    assert result.article_is_draft is True
    assert result.article_id == "KB-NEW-1234"


@pytest.mark.asyncio
async def test_middle_band_match_creates_draft_for_review(cfg, semantic_stub):
    """Effectiveness between create-below and update-above thresholds → draft for review."""
    runner = _runner(
        cfg,
        search_hits=[{"article_id": "KB-OK-5", "effectiveness_score": 0.55, "title": "Old article"}],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.decision == "drafted"
    assert result.note_title.startswith("Okta")


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, search_hits=[], semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
