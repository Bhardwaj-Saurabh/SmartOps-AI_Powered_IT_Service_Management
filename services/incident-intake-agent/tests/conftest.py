"""Pytest fixtures for offline tests.

Tests do not touch Compose. The few that exercise httpx use respx; LLM /
Qdrant interactions are stubbed at the agent's collaborator interfaces.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from incident_intake.config import AgentConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CFG_PATH = _REPO_ROOT / "services" / "incident-intake-agent" / "configs" / "agent.yaml"


@pytest.fixture
def agent_config() -> AgentConfig:
    import os
    os.environ.setdefault("EMAIL_PARSER_URL", "http://email-parser:9001")
    os.environ.setdefault("SLACK_CONNECTOR_URL", "http://slack-connector:9002")
    os.environ.setdefault("FORM_NORMALISER_URL", "http://form-normaliser:9003")
    from config_loader import load_yaml_as
    return load_yaml_as(_CFG_PATH, AgentConfig)


@pytest.fixture
def sample_email_raw() -> str:
    data = json.loads((_REPO_ROOT / "tools" / "email-parser" / "data" / "sample_emails.json").read_text())
    return data[0]["raw"]   # "vpn_outage"


@pytest.fixture
def stub_semantic() -> Any:
    """Returns a SemanticClient stub whose query_rule honours the YAML on disk."""
    import yaml
    rules = yaml.safe_load(
        (_REPO_ROOT / "configs" / "semantic-plane" / "intake-rules.yaml").read_text()
    )
    sc = AsyncMock()
    async def query(*, domain: str, context: dict[str, Any] | None = None, **_) -> Any:
        rule = rules[domain]
        if isinstance(rule, dict) and "by_service_area" in rule and context:
            sa = (context or {}).get("service_area")
            if sa and sa in rule.get("by_service_area", {}):
                return rule["by_service_area"][sa]
            return rule.get("default", rule)
        return rule
    sc.query_rule.side_effect = query
    sc.register = AsyncMock()
    sc.deregister = AsyncMock()
    return sc
