"""FastAPI entrypoint for the Knowledge Search Agent."""
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

from knowledge_search.config import AgentConfig
from knowledge_search.models import KnowledgeInput
from knowledge_search.tools import EmbeddingSearch, KnowledgeBase
from knowledge_search.workflow import KnowledgeRunner


_LOG = logging.getLogger("knowledge-search-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/knowledge-search-agent/configs/agent.yaml"
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
_kb = KnowledgeBase(_cfg.tools["knowledge_base"].url, _cfg.tools["knowledge_base"].timeout_seconds)
_emb = EmbeddingSearch(_cfg.tools["embedding_search"].url, _cfg.tools["embedding_search"].timeout_seconds)

_runner = KnowledgeRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    knowledge_base=_kb, embedding_search=_emb,
)


async def search_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = KnowledgeInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid knowledge_search payload: {exc}", step=1) from exc

    with audit_span("knowledge.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.artifacts.append(
        TaskArtifact(name="knowledge", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="knowledge-search-agent",
    description="Find KB articles relevant to an incident (Anthropic parallelization workflow)",
    url=f"http://knowledge-search-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["knowledge"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("knowledge_search", search_handler)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_gateway() -> bool:
    return await _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness")


async def _probe_sbca() -> bool:
    return await _probe_http(f"{_semantic._base_url.rstrip('/')}/health")  # noqa: SLF001


async def _probe_kb() -> bool:
    return await _probe_http(f"{_kb.base_url}/health")


async def _probe_emb() -> bool:
    return await _probe_http(f"{_emb.base_url}/health")


_health = HealthCheck(
    service="knowledge-search-agent", version=_cfg.version,
    probes={
        "ai_gateway": _probe_gateway, "sbca": _probe_sbca,
        "knowledge_base": _probe_kb, "embedding_search": _probe_emb,
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
                    name="knowledge_search",
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
            await _semantic.deregister("knowledge_search")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
