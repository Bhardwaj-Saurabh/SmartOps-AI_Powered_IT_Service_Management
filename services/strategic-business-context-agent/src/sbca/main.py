"""SBCA stub entrypoint — A2A agent serving business rules + Capability Registry.

Phase 1; replaceable by a full governance store later. Not an Anthropic
"agent" — this is a deterministic rule lookup service with no LLM.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from a2a_server import (
    AgentCapabilities,
    AgentCard,
    AgentCardSpec,
    AgentSkill,
    KeycloakAuth,
    build_app,
)
from observability import HealthCheck, TelemetryConfig, init_telemetry

from sbca.handlers import build_registry
from sbca.registry import CapabilityRegistry
from sbca.rules import Rules


_RULES_DIR = os.environ.get("SBCA_RULES_DIR", "/app/configs/semantic-plane")
_CAPS_FILE = os.environ.get("SBCA_CAPABILITIES_SEED", "/app/configs/capabilities.yaml")
_REALM_URL = os.environ.get("KEYCLOAK_REALM_URL", "http://keycloak:8080/realms/smartops")
_AUDIENCE = os.environ.get("OIDC_AUDIENCE", "agent-sbca")
_DEV_BYPASS = os.environ.get("DEV_ALLOW_UNVERIFIED_JWT", "false").lower() == "true"


_rules = Rules(_RULES_DIR)

_seed: list[dict] = []
_caps_path = Path(_CAPS_FILE)
if _caps_path.exists():
    _raw = yaml.safe_load(_caps_path.read_text()) or {}
    _seed = list(_raw.get("capabilities", []))
_registry = CapabilityRegistry(seed=_seed)


async def _probe_rules_loaded() -> bool:
    return bool(_rules.domains())


_card = AgentCard(
    name="strategic-business-context-agent",
    description="Serves business rules + capability registry over A2A",
    url="http://sbca:8444",
    version="0.1.0",
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(
            id="semantic.query_rule",
            name="Query business rule",
            description="Return the value of a named rule, optionally scoped by context",
            tags=["governance", "policy"],
        ),
        AgentSkill(
            id="capability_registry.register",
            name="Register a capability",
            description="Add or update a capability advertisement",
            tags=["registry"],
        ),
        AgentSkill(
            id="capability_registry.deregister",
            name="Deregister a capability",
            description="Remove a capability advertisement",
            tags=["registry"],
        ),
        AgentSkill(
            id="capability_registry.lookup",
            name="Look up a capability",
            description="Return the advertisement for a named capability",
            tags=["registry"],
        ),
    ],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{_REALM_URL}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)


_handler_registry = build_registry(_rules, _registry)
_auth = KeycloakAuth(realm_url=_REALM_URL, audience=_AUDIENCE, dev_allow_unverified=_DEV_BYPASS)
_health = HealthCheck(
    service="strategic-business-context-agent",
    version="0.1.0",
    probes={"rules_loaded": _probe_rules_loaded},
)

_agent_app = build_app(
    agent_card=AgentCardSpec(card=_card),
    registry=_handler_registry,
    health=_health,
    auth=_auth,
)
app = _agent_app.fastapi


@app.post("/admin/reload", include_in_schema=False)
async def _reload() -> dict:
    """Convenience for editing semantic-plane YAML in dev. Forces re-read."""
    _rules.reload()
    return {"reloaded": True, "domains": _rules.domains()}


init_telemetry(TelemetryConfig(service_name="strategic-business-context-agent"), app=app)
