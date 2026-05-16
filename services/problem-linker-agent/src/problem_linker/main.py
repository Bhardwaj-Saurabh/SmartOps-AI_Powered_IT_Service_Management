"""FastAPI entrypoint for the Problem Linker Agent."""
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
from di_framework_core import AgentError, AuditType, SemanticPlaneError, TaskStatus
from gateway_client import GatewayClient
from observability import HealthCheck, TelemetryConfig, audit_span, init_telemetry
from oidc_client import build_default_provider
from semantic_client import CapabilityAdvertisement, SemanticClient

from problem_linker.config import AgentConfig
from problem_linker.models import LinkerInput
from problem_linker.tools import ClusteringTool, IncidentHistory
from problem_linker.workflow import LinkerRunner


_LOG = logging.getLogger("problem-linker-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/problem-linker-agent/configs/agent.yaml"
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
_history = IncidentHistory(_cfg.tools["incident_history"].url, _cfg.tools["incident_history"].timeout_seconds)
_clustering = ClusteringTool(_cfg.tools["clustering"].url, _cfg.tools["clustering"].timeout_seconds)

_runner = LinkerRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    incident_history=_history, clustering=_clustering,
)


async def link_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = LinkerInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid link_to_problem payload: {exc}", step=1) from exc

    with audit_span("plinker.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.artifacts.append(
        TaskArtifact(name="problem_link", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="problem-linker-agent",
    description="Detect recurring incident patterns and link to problem records",
    url=f"http://problem-linker-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["problem"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("link_to_problem", link_handler)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="problem-linker-agent", version=_cfg.version,
    probes={
        "ai_gateway":       lambda: _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness"),
        "sbca":             lambda: _probe_http(f"{_semantic._base_url.rstrip('/')}/health"),  # noqa: SLF001
        "incident_history": lambda: _probe_http(f"{_history.base_url}/health"),
        "clustering":       lambda: _probe_http(f"{_clustering.base_url}/health"),
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
                    name="link_to_problem",
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
            await _semantic.deregister("link_to_problem")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
