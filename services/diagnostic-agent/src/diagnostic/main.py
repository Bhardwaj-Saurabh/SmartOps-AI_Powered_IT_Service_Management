"""FastAPI entrypoint for the Diagnostic Agent.

Anthropic pattern: evaluator-optimizer (agent — LLM-driven iteration count).
Exposes both A2A (port 8444) for orchestrated use AND MCP (port 8443) for
on-call engineers per the PRD. Phase 1 MCP is announced in the Agent Card
but the MCP transport itself ships in a later turn.
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

from diagnostic.config import AgentConfig
from diagnostic.models import DiagnosticInput
from diagnostic.tools import LogAggregator, MetricsQuery, TopologyWalker
from diagnostic.workflow import DiagnosticRunner


_LOG = logging.getLogger("diagnostic-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/diagnostic-agent/configs/agent.yaml"
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
_logs = LogAggregator(_cfg.tools["log_aggregator"].url, _cfg.tools["log_aggregator"].timeout_seconds)
_metrics = MetricsQuery(_cfg.tools["metrics_query"].url, _cfg.tools["metrics_query"].timeout_seconds)
_topology = TopologyWalker(_cfg.tools["topology_walker"].url, _cfg.tools["topology_walker"].timeout_seconds)

_runner = DiagnosticRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    log_aggregator=_logs, metrics_query=_metrics, topology_walker=_topology,
)


async def diagnose_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = DiagnosticInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid diagnostic payload: {exc}", step=1) from exc

    with audit_span("diagnose.run", audit_type=AuditType.PLATFORM):
        diag = await _runner.run(inp)

    task.metadata.di.confidence = diag.confidence
    task.artifacts.append(
        TaskArtifact(name="diagnosis", parts=[DataPart(data=diag.model_dump(mode="json"))])
    )

    if diag.state == "completed":
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    elif diag.state == "low_confidence":
        # Still completed-state on the A2A wire — downstream consumers decide
        # whether to accept by reading di.confidence + the artifact.
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    else:
        task.metadata.di.reason = "Diagnostic could not reach minimum acceptance confidence"
        task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    return task


_card = AgentCard(
    name="diagnostic-agent",
    description="Iterative root-cause analysis (Anthropic evaluator-optimizer agent)",
    url=f"http://diagnostic-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["diagnostic"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("root_cause_analysis", diagnose_handler)


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


async def _probe_logs() -> bool:
    return await _probe_http(f"{_logs.base_url}/health")


async def _probe_metrics() -> bool:
    return await _probe_http(f"{_metrics.base_url}/health")


async def _probe_topology() -> bool:
    return await _probe_http(f"{_topology.base_url}/health")


_health = HealthCheck(
    service="diagnostic-agent", version=_cfg.version,
    probes={
        "ai_gateway": _probe_gateway, "sbca": _probe_sbca,
        "log_aggregator": _probe_logs, "metrics_query": _probe_metrics, "topology_walker": _probe_topology,
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
                    name="root_cause_analysis",
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
            await _semantic.deregister("root_cause_analysis")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
