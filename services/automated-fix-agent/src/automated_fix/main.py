"""FastAPI entrypoint for the Automated Fix Agent.

HIGH-RISK AI System under EU AI Act Annex III. The safety controls
(SBCA-gated approval, scope cap, change-freeze, unconditional snapshot,
automatic rollback-on-error, separate rollback skill for Saga
compensation) are all in workflow.py and discussed in
docs/eu-ai-act-risk-assessment.md.
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

from automated_fix.config import AgentConfig
from automated_fix.models import FixInput, RollbackInput
from automated_fix.tools import (
    ConfigurationManager,
    RollbackHandler,
    ScriptExecutor,
)
from automated_fix.workflow import AutomatedFixRunner


_LOG = logging.getLogger("automated-fix-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/automated-fix-agent/configs/agent.yaml"
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
_script = ScriptExecutor(_cfg.tools["script_executor"].url, _cfg.tools["script_executor"].timeout_seconds)
_config_mgr = ConfigurationManager(_cfg.tools["configuration_manager"].url, _cfg.tools["configuration_manager"].timeout_seconds)
_rollback = RollbackHandler(_cfg.tools["rollback_handler"].url, _cfg.tools["rollback_handler"].timeout_seconds)

_runner = AutomatedFixRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    script=_script, config_manager=_config_mgr, rollback_handler=_rollback,
)


async def apply_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = FixInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid apply_automated_fix payload: {exc}", step=1) from exc

    with audit_span("fix.apply.run", audit_type=AuditType.PLATFORM):
        outcome = await _runner.apply(inp)

    task.artifacts.append(
        TaskArtifact(name="fix_result", parts=[DataPart(data=outcome.model_dump(mode="json"))])
    )

    if outcome.state == "completed":
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    elif outcome.state == "requires_human":
        task.metadata.di.requires_human = True
        task.metadata.di.reason = outcome.requires_human_reason
        task.status = TaskStatusModel(state=TaskStatus.INPUT_REQUIRED, message=task.status.message)
    elif outcome.state == "rolled_back":
        task.metadata.di.reason = "Runbook failed mid-execution; rolled back to snapshot"
        task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    else:  # failed
        task.metadata.di.reason = "Runbook failed and rollback also failed"
        task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    return task


async def rollback_handler_a2a(message: Message, task: Task) -> Task:
    """Saga compensation entry point. Called by the Resolution Orchestrator
    when Verification reports the fix didn't work."""
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = RollbackInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid rollback payload: {exc}", step=1) from exc

    with audit_span("fix.rollback.run", audit_type=AuditType.PLATFORM):
        outcome = await _runner.rollback(inp)

    task.artifacts.append(
        TaskArtifact(name="rollback_result", parts=[DataPart(data=outcome.model_dump(mode="json"))])
    )
    if outcome.restored:
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    else:
        task.metadata.di.reason = "Rollback did not restore configuration"
        task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    return task


_card = AgentCard(
    name="automated-fix-agent",
    description="Execute pre-approved remediation runbooks with snapshot+rollback (EU AI Act high-risk)",
    url=f"http://automated-fix-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["fix", "high-risk"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("apply_automated_fix", apply_handler)
_handlers.register("rollback", rollback_handler_a2a)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="automated-fix-agent", version=_cfg.version,
    probes={
        "ai_gateway":            lambda: _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness"),
        "sbca":                  lambda: _probe_http(f"{_semantic._base_url.rstrip('/')}/health"),  # noqa: SLF001
        "script_executor":       lambda: _probe_http(f"{_script.base_url}/health"),
        "configuration_manager": lambda: _probe_http(f"{_config_mgr.base_url}/health"),
        "rollback_handler":      lambda: _probe_http(f"{_rollback.base_url}/health"),
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
                    name="automated_fix",
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
            await _semantic.deregister("automated_fix")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
