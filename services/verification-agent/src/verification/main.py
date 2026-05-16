"""FastAPI entrypoint for the Verification Agent."""
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

from verification.config import AgentConfig
from verification.models import VerificationInput
from verification.tools import (
    ComparisonTool,
    HealthCheckRunner,
    MetricsQuery,
    SyntheticMonitor,
)
from verification.workflow import VerificationRunner


_LOG = logging.getLogger("verification-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/verification-agent/configs/agent.yaml"
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
_health = HealthCheckRunner(_cfg.tools["health_check_runner"].url, _cfg.tools["health_check_runner"].timeout_seconds)
_synthetic = SyntheticMonitor(_cfg.tools["synthetic_monitor"].url, _cfg.tools["synthetic_monitor"].timeout_seconds)
_comparison = ComparisonTool(_cfg.tools["comparison_tool"].url, _cfg.tools["comparison_tool"].timeout_seconds)
_metrics = MetricsQuery(_cfg.tools["metrics_query"].url, _cfg.tools["metrics_query"].timeout_seconds)

_runner = VerificationRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    health=_health, synthetic=_synthetic, comparison=_comparison, metrics=_metrics,
)


async def verify_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = VerificationInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid verify_resolution payload: {exc}", step=1) from exc

    with audit_span("verify.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.metadata.di.confidence = result.confidence
    task.artifacts.append(
        TaskArtifact(name="verification", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    # The orchestrator's saga reads result.fix_verified directly from the
    # artifact — we ALWAYS return COMPLETED here so the chain reaches the
    # saga decision. Saga inspects the artifact and decides whether to roll
    # back. This matches the EU AI Act §14 design — verification reports a
    # fact; the orchestrator decides action.
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="verification-agent",
    description="Verify an applied fix actually resolved the symptoms (parallelization workflow)",
    url=f"http://verification-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["verification"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("verify_resolution", verify_handler)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


_health_check = HealthCheck(
    service="verification-agent", version=_cfg.version,
    probes={
        "ai_gateway":          lambda: _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness"),
        "sbca":                lambda: _probe_http(f"{_semantic._base_url.rstrip('/')}/health"),  # noqa: SLF001
        "health_check_runner": lambda: _probe_http(f"{_health.base_url}/health"),
        "synthetic_monitor":   lambda: _probe_http(f"{_synthetic.base_url}/health"),
        "comparison_tool":     lambda: _probe_http(f"{_comparison.base_url}/health"),
    },
)
_auth = KeycloakAuth(
    realm_url=os.environ.get("KEYCLOAK_REALM_URL", "http://keycloak:8080/realms/smartops"),
    audience=_cfg.oidc.audience,
    dev_allow_unverified=os.environ.get("DEV_ALLOW_UNVERIFIED_JWT", "false").lower() == "true",
)
_agent_app = build_app(
    agent_card=AgentCardSpec(card=_card),
    registry=_handlers, health=_health_check, auth=_auth,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_telemetry(TelemetryConfig(service_name=_cfg.name, service_version=_cfg.version), app=app)
    if _cfg.capability_registry.register_on_startup:
        try:
            await _semantic.register(
                CapabilityAdvertisement(
                    name="verify_resolution",
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
            await _semantic.deregister("verify_resolution")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
