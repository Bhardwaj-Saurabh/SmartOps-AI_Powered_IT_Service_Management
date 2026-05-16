"""FastAPI entrypoint for the Priority Scorer.

Anthropic pattern: prompt chaining (workflow). 10-step deterministic chain
with one LLM call (step 3) for impact/urgency narrative analysis.
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
)
from gateway_client import GatewayClient
from observability import HealthCheck, TelemetryConfig, audit_span, init_telemetry
from oidc_client import build_default_provider
from semantic_client import CapabilityAdvertisement, SemanticClient

from priority_scorer.config import AgentConfig
from priority_scorer.models import PriorityInput
from priority_scorer.tools import ImpactAnalyser, ServiceDependencyMapper
from priority_scorer.workflow import PriorityRunner


_LOG = logging.getLogger("priority-scorer")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/priority-scorer/configs/agent.yaml"
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
_impact = ImpactAnalyser(_cfg.tools["impact_analyser"].url, _cfg.tools["impact_analyser"].timeout_seconds)
_deps = ServiceDependencyMapper(
    _cfg.tools["service_dependency_mapper"].url,
    _cfg.tools["service_dependency_mapper"].timeout_seconds,
)
_runner = PriorityRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic, impact=_impact, dependency_mapper=_deps,
)


async def priority_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = PriorityInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid priority payload: {exc}", step=1) from exc

    with audit_span("priority.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.artifacts.append(
        TaskArtifact(name="priority", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="priority-scorer",
    description="Compute incident priority P1–P4 from impact + urgency (Anthropic prompt-chaining workflow)",
    url=f"http://priority-scorer:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["priority"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("priority_scoring", priority_handler)


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


async def _probe_impact() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_impact.base_url}/health")).status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_deps() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_deps.base_url}/health")).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="priority-scorer",
    version=_cfg.version,
    probes={
        "ai_gateway": _probe_gateway, "sbca": _probe_sbca,
        "impact_analyser": _probe_impact, "service_dependency_mapper": _probe_deps,
    },
)
_auth = KeycloakAuth(
    realm_url=os.environ.get("KEYCLOAK_REALM_URL", "http://keycloak:8080/realms/smartops"),
    audience=_cfg.oidc.audience,
    dev_allow_unverified=os.environ.get("DEV_ALLOW_UNVERIFIED_JWT", "false").lower() == "true",
)
_agent_app = build_app(
    agent_card=AgentCardSpec(card=_card),
    registry=_handlers, health=_health, auth=_auth,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_telemetry(TelemetryConfig(service_name=_cfg.name, service_version=_cfg.version), app=app)
    if _cfg.capability_registry.register_on_startup:
        try:
            await _semantic.register(
                CapabilityAdvertisement(
                    name="priority_scoring",
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
            await _semantic.deregister("priority_scoring")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
