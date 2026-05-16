"""Offline tests for the Knowledge Search Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from knowledge_search.config import AgentConfig
from knowledge_search.models import (
    ClassificationSlice,
    DiagnosisSlice,
    IncidentSlice,
    KnowledgeInput,
)
from knowledge_search.workflow import KnowledgeRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "knowledge-search-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "knowledge-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def q(*, domain: str, **_):
        return rules[domain]
    sc.query_rule.side_effect = q
    return sc


def _runner(cfg, *, vector_hits, keyword_hits, llm_ranked, semantic_stub) -> KnowledgeRunner:
    gateway = AsyncMock()
    gateway.embedding.return_value.vectors = [[0.0] * cfg.embedding.vector_size]
    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps({
            "ranked": llm_ranked,
            "applicability_summary": "top pick most directly addresses the diagnosed cause",
        })})()
    gateway.chat_completion.side_effect = chat

    kb = AsyncMock()
    kb.search.return_value = keyword_hits
    emb = AsyncMock()
    emb.search.return_value = vector_hits

    return KnowledgeRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        knowledge_base=kb, embedding_search=emb,
    )


def _payload() -> KnowledgeInput:
    return KnowledgeInput(
        incident=IncidentSlice(incident_id="INC-K001", affected_service="okta-sso", symptoms_summary="SSO returns AADSTS50105"),
        classification=ClassificationSlice(service_area="application", category="okta-sso"),
        diagnosis=DiagnosisSlice(root_cause="CA group sync lapse"),
        limit=5,
    )


@pytest.mark.asyncio
async def test_vector_and_keyword_merge_then_rerank(cfg, semantic_stub):
    runner = _runner(
        cfg,
        vector_hits=[
            {"article_id": "KB-002", "title": "Okta AADSTS50105", "service": "okta-sso", "category": "okta-sso", "similarity": 0.9, "updated_at_days_ago": 30, "effectiveness_score": 0.9},
            {"article_id": "KB-001", "title": "VPN MTU", "service": "vpn", "category": "vpn", "similarity": 0.4, "updated_at_days_ago": 14, "effectiveness_score": 0.9},
        ],
        keyword_hits=[
            {"article_id": "KB-002", "title": "Okta AADSTS50105", "service": "okta-sso", "category": "okta-sso", "keyword_score": 0.8, "updated_at_days_ago": 30, "effectiveness_score": 0.9, "excerpt": "..."},
        ],
        llm_ranked=[
            {"article_id": "KB-002", "relevance_score": 0.95, "reasoning": "matches diagnosed cause"},
        ],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.articles[0].article_id == "KB-002"
    assert result.articles[0].relevance_score >= 0.9


@pytest.mark.asyncio
async def test_stale_articles_are_flagged_but_returned(cfg, semantic_stub):
    """An article older than max_days_for_recommendation gets is_stale=True."""
    runner = _runner(
        cfg,
        vector_hits=[
            {"article_id": "KB-OLD", "title": "old article", "service": "vpn", "category": "vpn",
             "similarity": 0.95, "updated_at_days_ago": 500, "effectiveness_score": 0.8},
        ],
        keyword_hits=[],
        llm_ranked=[{"article_id": "KB-OLD", "relevance_score": 0.7, "reasoning": "still relevant despite age"}],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert any(a.is_stale for a in result.articles)
    assert result.stale_flagged_count >= 1


@pytest.mark.asyncio
async def test_below_min_score_drops_results(cfg, semantic_stub):
    """combined_score < SBCA min_score → article filtered out."""
    runner = _runner(
        cfg,
        vector_hits=[
            {"article_id": "KB-LOW", "title": "barely related", "service": "x", "category": "y",
             "similarity": 0.2, "updated_at_days_ago": 30, "effectiveness_score": 0.5},
        ],
        keyword_hits=[
            {"article_id": "KB-LOW", "title": "barely related", "service": "x", "category": "y",
             "keyword_score": 0.1, "updated_at_days_ago": 30, "effectiveness_score": 0.5, "excerpt": "..."},
        ],
        llm_ranked=[],
        semantic_stub=semantic_stub,
    )
    result = await runner.run(_payload())
    assert result.articles == []


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg):
    failing = AsyncMock()
    failing.query_rule.side_effect = SemanticPlaneError("sbca down")
    runner = _runner(cfg, vector_hits=[], keyword_hits=[], llm_ranked=[], semantic_stub=failing)
    with pytest.raises(SemanticPlaneError):
        await runner.run(_payload())
