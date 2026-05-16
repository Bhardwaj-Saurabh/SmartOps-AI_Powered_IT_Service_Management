"""FastAPI entrypoint for the Classification Agent.

Wires the spec-native A2A server + the 8-step parallel workflow. Anthropic
pattern: parallelization (workflow, not autonomous agent).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from a2a_server import (
    AgentCapabilities,
    AgentCard,
    AgentCardSpec,
    AgentSkill,
    DataPart,
    HandlerRegistry,
    KeycloakAuth,
    Message,
    Task,
    build_app,
)
from a2a_server.models import TaskArtifact, TaskStatusModel
from config_loader import load_yaml_as
from di_framework_core import (
    AgentError,
    AuditType,
    SemanticPlaneError,
    TaskStatus,
    current_correlation_id,
)
from gateway_client import GatewayClient
from observability import HealthCheck, TelemetryConfig, audit_span, init_telemetry
from oidc_client import build_default_provider
from semantic_client import CapabilityAdvertisement, SemanticClient

from classification.config import AgentConfig
from classification.models import IncidentInput
from classification.tools import HistoricalPatternMatcher, TaxonomyLookup
from classification.workflow import ClassificationRunner


_LOG = logging.getLogger("classification-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/classification-agent/configs/agent.yaml"
)
_cfg: AgentConfig = load_yaml_as(_CFG_PATH, AgentConfig)

_token_provider = build_default_provider()
_gateway = GatewayClient(
    base_url=os.environ.get("AI_GATEWAY_URL", "http://litellm:4000"),
    bearer_provider=_token_provider,
)
_semantic = SemanticClient(
    base_url=os.environ.get("SBCA_URL", "http://sbca:8444"),
    bearer_provider=_token_provider,
)
_taxonomy = TaxonomyLookup(_cfg.tools["taxonomy_lookup"].url, _cfg.tools["taxonomy_lookup"].timeout_seconds)
_history = HistoricalPatternMatcher(
    _cfg.tools["historical_pattern_matcher"].url, _cfg.tools["historical_pattern_matcher"].timeout_seconds
)
_runner = ClassificationRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic, taxonomy=_taxonomy, history=_history,
)


async def classify_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        incident = IncidentInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid classification payload: {exc}", step=1) from exc

    with audit_span("classify.run", audit_type=AuditType.PLATFORM):
        classification = await _runner.run(incident)

    task.metadata.di.confidence = classification.confidence
    task.artifacts.append(
        TaskArtifact(
            name="classification",
            parts=[DataPart(data=classification.model_dump(mode="json"))],
        )
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="classification-agent",
    description="Classify incidents by service area + category (Anthropic parallelization workflow)",
    url=f"http://classification-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["classification"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("incident_classification", classify_handler)


async def _probe_gateway() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_gateway.base_url.rstrip('/')}/health/liveliness")).status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_sbca() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_semantic._base_url.rstrip('/')}/health")).status_code == 200  # noqa: SLF001
    except httpx.HTTPError:
        return False


async def _probe_taxonomy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_taxonomy.base_url}/health")).status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_history() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_history.base_url}/health")).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="classification-agent",
    version=_cfg.version,
    probes={
        "ai_gateway": _probe_gateway,
        "sbca": _probe_sbca,
        "taxonomy_lookup": _probe_taxonomy,
        "historical_pattern_matcher": _probe_history,
    },
)
_auth = KeycloakAuth(
    realm_url=os.environ.get("KEYCLOAK_REALM_URL", "http://keycloak:8080/realms/smartops"),
    audience=_cfg.oidc.audience,
    dev_allow_unverified=os.environ.get("DEV_ALLOW_UNVERIFIED_JWT", "false").lower() == "true",
)

_agent_app = build_app(
    agent_card=AgentCardSpec(card=_card),
    registry=_handlers,
    health=_health,
    auth=_auth,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_telemetry(TelemetryConfig(service_name=_cfg.name, service_version=_cfg.version), app=app)
    if _cfg.capability_registry.register_on_startup:
        try:
            await _semantic.register(
                CapabilityAdvertisement(
                    name="incident_classification",
                    url=str(_card.url),
                    version=_cfg.version,
                    skills=[s.id for s in _cfg.a2a.skills],
                )
            )
            _LOG.info("Registered with Capability Registry")
        except SemanticPlaneError as exc:
            _LOG.warning("Capability registration deferred: %s", exc)
    yield
    if _cfg.capability_registry.deregister_on_shutdown:
        try:
            await _semantic.deregister("incident_classification")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
