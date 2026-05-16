"""Offline tests for the Classification Agent.

Stubs LiteLLM, SBCA, taxonomy-lookup, historical-pattern-matcher.

Covers:
  * Happy path (LLM + history agree) → high confidence
  * History fallback when LLM confidence is low
  * Security keyword override forces the label regardless of LLM output
  * Taxonomy version drift → hard fail (no fallback)
  * SBCA failure → SemanticPlaneError (no fallback)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from di_framework_core import SemanticPlaneError
from classification.config import AgentConfig
from classification.models import IncidentInput
from classification.workflow import ClassificationRunner


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "classification-agent" / "configs" / "agent.yaml"


@pytest.fixture
def cfg() -> AgentConfig:
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def semantic_stub():
    """Stub that reads the on-disk YAML rules for classification."""
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "classification-rules.yaml").read_text()
    )
    sc = AsyncMock()

    async def query_rule(*, domain: str, context: dict[str, Any] | None = None, **_):
        return rules[domain]

    sc.query_rule.side_effect = query_rule
    sc.register = AsyncMock()
    sc.deregister = AsyncMock()
    return sc


@pytest.fixture
def taxonomy_stub():
    sc = AsyncMock()
    full = yaml.safe_load(
        (_REPO_ROOT / "tools" / "taxonomy-lookup" / "data" / "itsm_taxonomy.yaml").read_text()
    )

    async def full_taxonomy():
        return full

    async def validate(*, service_area: str, category: str | None):
        area = (full.get("service_areas") or {}).get(service_area)
        valid_area = area is not None
        valid_cat = bool(valid_area and (category is None or category in (area.get("categories") or [])))
        return {"service_area_valid": valid_area, "category_valid": valid_cat, "taxonomy_version": full["version"]}

    sc.full_taxonomy.side_effect = full_taxonomy
    sc.validate.side_effect = validate
    return sc


def _runner(cfg, *, llm_label, history_matches, semantic_stub, taxonomy_stub):
    gateway = AsyncMock()
    gateway.embedding.return_value.vectors = [[0.0] * cfg.embedding.vector_size]

    async def chat(*args, **kwargs):
        return type("R", (), {"text": json.dumps(llm_label)})()
    gateway.chat_completion.side_effect = chat

    history = AsyncMock()
    history.match.return_value = history_matches

    return ClassificationRunner(
        cfg=cfg, gateway=gateway, semantic=semantic_stub,
        taxonomy=taxonomy_stub, history=history,
    )


def _incident() -> IncidentInput:
    return IncidentInput(
        incident_id="INC-TEST001",
        affected_service="vpn",
        symptoms_summary="VPN disconnects every couple of minutes",
        symptoms_verbatim="vpn keeps dropping",
    )


@pytest.mark.asyncio
async def test_happy_path_llm_and_history_agree(cfg, semantic_stub, taxonomy_stub):
    runner = _runner(
        cfg,
        llm_label={"service_area": "network", "category": "vpn", "confidence": 0.85, "reasoning": "vpn keyword"},
        history_matches=[
            {"incident_id": "H1", "similarity": 0.91, "service_area": "network", "category": "vpn"},
            {"incident_id": "H2", "similarity": 0.86, "service_area": "network", "category": "vpn"},
        ],
        semantic_stub=semantic_stub, taxonomy_stub=taxonomy_stub,
    )
    result = await runner.run(_incident())
    assert result.service_area == "network"
    assert result.category == "vpn"
    assert result.confidence > 0.7
    assert result.override_reason is None
    assert result.taxonomy_version == "2026.05"


@pytest.mark.asyncio
async def test_history_fallback_when_llm_low_confidence(cfg, semantic_stub, taxonomy_stub):
    runner = _runner(
        cfg,
        llm_label={"service_area": "endpoint", "category": "hardware", "confidence": 0.2, "reasoning": "unsure"},
        history_matches=[
            {"incident_id": "H1", "similarity": 0.93, "service_area": "network", "category": "vpn"},
            {"incident_id": "H2", "similarity": 0.88, "service_area": "network", "category": "vpn"},
        ],
        semantic_stub=semantic_stub, taxonomy_stub=taxonomy_stub,
    )
    result = await runner.run(_incident())
    assert result.service_area == "network"
    assert result.category == "vpn"


@pytest.mark.asyncio
async def test_security_keyword_override(cfg, semantic_stub, taxonomy_stub):
    runner = _runner(
        cfg,
        llm_label={"service_area": "endpoint", "category": "hardware", "confidence": 0.92, "reasoning": "n/a"},
        history_matches=[],
        semantic_stub=semantic_stub, taxonomy_stub=taxonomy_stub,
    )
    incident = IncidentInput(
        incident_id="INC-SEC001",
        symptoms_summary="user reports phishing email with credential theft attempt",
        symptoms_verbatim="phishing — credential theft",
    )
    result = await runner.run(incident)
    assert result.service_area == "security"
    assert result.category == "malware"
    assert result.override_reason == "security_keyword_override"


@pytest.mark.asyncio
async def test_sbca_failure_hard_fails(cfg, taxonomy_stub):
    failing_sc = AsyncMock()
    failing_sc.query_rule.side_effect = SemanticPlaneError("SBCA down")
    runner = _runner(
        cfg,
        llm_label={"service_area": "network", "category": "vpn", "confidence": 0.9, "reasoning": "x"},
        history_matches=[],
        semantic_stub=failing_sc, taxonomy_stub=taxonomy_stub,
    )
    with pytest.raises(SemanticPlaneError):
        await runner.run(_incident())


@pytest.mark.asyncio
async def test_taxonomy_version_drift_hard_fails(cfg, semantic_stub, monkeypatch):
    """If SBCA expects v2099.99 and the sidecar serves v2026.05, refuse."""
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "classification-rules.yaml").read_text()
    )
    drifted = dict(rules)
    drifted["classification_taxonomy_version"] = {"expected": "9999.99"}

    sc = AsyncMock()

    async def query(*, domain: str, **_):
        return drifted[domain]

    sc.query_rule.side_effect = query

    full = yaml.safe_load(
        (_REPO_ROOT / "tools" / "taxonomy-lookup" / "data" / "itsm_taxonomy.yaml").read_text()
    )
    taxonomy = AsyncMock()

    async def vfull():
        return full

    async def validate(*, service_area: str, category: str | None):
        area = (full["service_areas"]).get(service_area) or {}
        return {"service_area_valid": area != {}, "category_valid": (category in (area.get("categories") or [])), "taxonomy_version": full["version"]}

    taxonomy.full_taxonomy.side_effect = vfull
    taxonomy.validate.side_effect = validate

    runner = _runner(
        cfg,
        llm_label={"service_area": "network", "category": "vpn", "confidence": 0.9, "reasoning": "x"},
        history_matches=[],
        semantic_stub=sc, taxonomy_stub=taxonomy,
    )
    from di_framework_core import AgentError
    with pytest.raises(AgentError) as exc:
        await runner.run(_incident())
    assert "version drift" in str(exc.value).lower()
