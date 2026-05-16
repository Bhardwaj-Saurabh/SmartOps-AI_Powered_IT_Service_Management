"""FastAPI entrypoint for the I2R Primary Orchestrator.

Drives the full Incident-to-Resolution business process by composing the
three sub-process orchestrators (Triage → Resolution → Closure) via A2A
capability discovery. This is the outermost authorising boundary of the
autonomous actuation path (EU AI Act HIGH-RISK — inherited from the
Resolution sub-process).
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
    AuditType,
    SemanticPlaneError,
    TaskStatus,
    current_correlation_id,
)
from observability import HealthCheck, TelemetryConfig, audit_span, init_telemetry
from oidc_client import build_default_provider
from semantic_client import CapabilityAdvertisement, SemanticClient

from i2r.config import OrchestratorConfig
from i2r.workflow import I2RRunner


_LOG = logging.getLogger("i2r-primary-orchestrator")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/i2r-primary-orchestrator/configs/agent.yaml"
)
_cfg: OrchestratorConfig = load_yaml_as(_CFG_PATH, OrchestratorConfig)

_token_provider = build_default_provider()
_semantic = SemanticClient(
    base_url=os.environ.get("SBCA_URL", "http://sbca:8444"),
    bearer_provider=_token_provider,
)
_runner = I2RRunner(
    cfg=_cfg,
    registry_url=_cfg.capability_registry.registry_url,
    bearer_provider=_token_provider,
    semantic=_semantic,
)


_I2R_STATE_TO_A2A = {
    "closed": TaskStatus.COMPLETED,
    "resolution_completed": TaskStatus.COMPLETED,
    "triage_needs_input": TaskStatus.INPUT_REQUIRED,
    "resolution_failed": TaskStatus.FAILED,
    "failed": TaskStatus.FAILED,
    "submitted": TaskStatus.FAILED,
    "triaged": TaskStatus.FAILED,
    "resolving": TaskStatus.FAILED,
}


async def handle_incident_handler(message: Message, task: Task) -> Task:
    initial: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            initial = part.data
            break
    process_value = message.metadata.di.process or "i2r"
    with audit_span("i2r.run", audit_type=AuditType.PLATFORM,
                   attributes={"di.process": process_value}):
        result = await _runner.run(
            process=process_value, initial_payload=initial,
            correlation_id=current_correlation_id(),
        )

    task.artifacts.append(
        TaskArtifact(name="i2r_result", parts=[DataPart(data=result)])
    )

    # Flat KPI envelope — composable surface for upstream consumers/dashboards.
    kpi_envelope: dict[str, Any] = {
        "i2r_state": result.get("i2r_state"),
        "duration_ms": result.get("duration_ms"),
        "step_count": len(result.get("steps") or []),
        "escalation_triggered": result.get("escalation_triggered", False),
        "failed_step_index": result.get("failed_step_index"),
        "per_step_latency_ms": [
            {"step": s.get("step"), "duration_ms": s.get("duration_ms")}
            for s in (result.get("steps") or [])
        ],
    }
    task.artifacts.append(
        TaskArtifact(name="i2r_kpis", parts=[DataPart(data=kpi_envelope)])
    )

    i2r_state = result.get("i2r_state") or "failed"
    a2a_state = _I2R_STATE_TO_A2A.get(i2r_state, TaskStatus.FAILED)
    if a2a_state == TaskStatus.FAILED:
        task.metadata.di.reason = f"I2R chain ended in {i2r_state}"
        task.metadata.di.failed_step = result.get("failed_step_index")
    task.status = TaskStatusModel(state=a2a_state, message=task.status.message)
    return task


_card = AgentCard(
    name="i2r-primary-orchestrator",
    description="Primary orchestrator for the Incident-to-Resolution business process — composes Triage, Resolution, and Closure sub-processes",
    url=f"http://i2r-primary-orchestrator:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[
        AgentSkill(id=s.id, name=s.name, description=s.description,
                   tags=["orchestrator", "i2r", "primary"])
        for s in _cfg.a2a.skills
    ],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("handle_incident", handle_incident_handler)


async def _probe_sbca() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(f"{_semantic._base_url.rstrip('/')}/health")).status_code == 200  # noqa: SLF001
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="i2r-primary-orchestrator", version=_cfg.version,
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
                    name="handle_incident",
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
            await _semantic.deregister("handle_incident")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
