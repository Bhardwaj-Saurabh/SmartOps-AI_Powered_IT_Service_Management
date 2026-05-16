"""FastAPI entrypoint for the Resolution Documenter Agent."""
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

from resolution_documenter.config import AgentConfig
from resolution_documenter.models import DocumenterInput
from resolution_documenter.tools import (
    DocumentFormatter,
    KnowledgeBaseSearch,
    KnowledgeBaseWriter,
)
from resolution_documenter.workflow import DocumenterRunner


_LOG = logging.getLogger("resolution-documenter-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/resolution-documenter-agent/configs/agent.yaml"
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
_formatter = DocumentFormatter(_cfg.tools["document_formatter"].url, _cfg.tools["document_formatter"].timeout_seconds)
_kb_writer = KnowledgeBaseWriter(_cfg.tools["knowledge_base_writer"].url, _cfg.tools["knowledge_base_writer"].timeout_seconds)
_kb_search = KnowledgeBaseSearch(_cfg.tools["knowledge_base"].url, _cfg.tools["knowledge_base"].timeout_seconds)

_runner = DocumenterRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    formatter=_formatter, kb_writer=_kb_writer, kb_search=_kb_search,
)


async def doc_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = DocumenterInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid document_resolution payload: {exc}", step=1) from exc

    with audit_span("doc.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.artifacts.append(
        TaskArtifact(name="documentation", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="resolution-documenter-agent",
    description="Generate structured resolution notes + create/update KB articles (chain workflow)",
    url=f"http://resolution-documenter-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["documentation"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("document_resolution", doc_handler)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="resolution-documenter-agent", version=_cfg.version,
    probes={
        "ai_gateway":           lambda: _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness"),
        "sbca":                 lambda: _probe_http(f"{_semantic._base_url.rstrip('/')}/health"),  # noqa: SLF001
        "document_formatter":   lambda: _probe_http(f"{_formatter.base_url}/health"),
        "knowledge_base_writer": lambda: _probe_http(f"{_kb_writer.base_url}/health"),
        "knowledge_base":       lambda: _probe_http(f"{_kb_search.base_url}/health"),
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
                    name="document_resolution",
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
            await _semantic.deregister("document_resolution")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
