"""FastAPI entrypoint for the Closure Workflow Orchestrator."""
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
    AuditType,
    SemanticPlaneError,
    TaskStatus,
    current_correlation_id,
)
from observability import HealthCheck, TelemetryConfig, audit_span, init_telemetry
from oidc_client import build_default_provider
from semantic_client import CapabilityAdvertisement, SemanticClient

from closure.config import OrchestratorConfig
from closure.workflow import ClosureRunner


_LOG = logging.getLogger("closure-workflow-orchestrator")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/closure-workflow-orchestrator/configs/agent.yaml"
)
_cfg: OrchestratorConfig = load_yaml_as(_CFG_PATH, OrchestratorConfig)

_token_provider = build_default_provider()
_semantic = SemanticClient(
    base_url=os.environ.get("SBCA_URL", "http://sbca:8444"),
    bearer_provider=_token_provider,
)
_runner = ClosureRunner(
    cfg=_cfg, registry_url=_cfg.capability_registry.registry_url, bearer_provider=_token_provider,
)


async def close_handler(message: Message, task: Task) -> Task:
    initial: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            initial = part.data
            break
    process_value = message.metadata.di.process or "i2r"
    with audit_span("closure.run", audit_type=AuditType.PLATFORM,
                   attributes={"di.process": process_value}):
        result = await _runner.run(
            process=process_value, initial_payload=initial, correlation_id=current_correlation_id(),
        )

    task.artifacts.append(
        TaskArtifact(name="closure_result", parts=[DataPart(data=result)])
    )
    state = result.get("chain_state")
    if state == "completed":
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    else:
        task.metadata.di.reason = f"Closure chain ended in {state}"
        task.metadata.di.failed_step = result.get("failed_step_index")
        task.status = TaskStatusModel(state=TaskStatus.FAILED, message=task.status.message)
    return task


_card = AgentCard(
    name="closure-workflow-orchestrator",
    description="Strategic sub-process orchestrator for incident closure (Notify + SLA in Stage 5a)",
    url=f"http://closure-workflow-orchestrator:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["orchestrator", "closure"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("close_incident", close_handler)


async def _probe_sbca() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_semantic._base_url.rstrip('/')}/health")).status_code == 200  # noqa: SLF001
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="closure-workflow-orchestrator", version=_cfg.version,
    probes={"sbca": _probe_sbca},
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
                    name="close_incident",
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
            await _semantic.deregister("close_incident")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
