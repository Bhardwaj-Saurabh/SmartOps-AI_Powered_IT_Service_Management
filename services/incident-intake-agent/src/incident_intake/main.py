"""FastAPI entrypoint for the Incident Intake Agent.

Wires:
  * a2a_server (Agent Card + JSON-RPC + JWT + DI envelope) on port 8444
  * /health + /ready that probe AI Gateway, SBCA, Qdrant
  * Capability Registry register/deregister on startup/shutdown
  * 12-step workflow via the IntakeRunner

Anthropic pattern: prompt chaining (workflow, not autonomous agent).
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
    TextPart,
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
from semantic_client import CapabilityAdvertisement, SemanticClient

from incident_intake.config import AgentConfig
from incident_intake.models import RawInput
from incident_intake.oidc import build_default_provider
from incident_intake.tools import (
    EmailParser,
    FormNormaliser,
    QdrantTool,
    SlackConnector,
    ToolSidecar,
)
from incident_intake.workflow import IntakeRunner


_LOG = logging.getLogger("incident-intake-agent")
_CFG_PATH = os.environ.get("AGENT_CONFIG_PATH", "/app/services/incident-intake-agent/configs/agent.yaml")
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


_email = EmailParser(ToolSidecar(_cfg.tools["email_parser"].url, _cfg.tools["email_parser"].timeout_seconds))
_slack = SlackConnector(ToolSidecar(_cfg.tools["slack_connector"].url, _cfg.tools["slack_connector"].timeout_seconds))
_form = FormNormaliser(ToolSidecar(_cfg.tools["form_normaliser"].url, _cfg.tools["form_normaliser"].timeout_seconds))
_qdrant = QdrantTool(
    url=os.environ.get("QDRANT_URL", "http://qdrant:6333"),
    collection=_cfg.embedding.qdrant_collection,
    vector_size=_cfg.embedding.vector_size,
)

_runner = IntakeRunner(
    cfg=_cfg,
    gateway=_gateway,
    semantic=_semantic,
    email_parser=_email,
    slack_connector=_slack,
    form_normaliser=_form,
    qdrant=_qdrant,
)


# ─── A2A handlers ──────────────────────────────────────────────────────
async def submit_incident_handler(message: Message, task: Task) -> Task:
    """A2A capability: submit_incident."""
    raw_payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            raw_payload = part.data
            break
    try:
        raw = RawInput.model_validate(raw_payload)
    except Exception as exc:
        raise AgentError(f"Invalid submit_incident payload: {exc}", step=1) from exc

    with audit_span("intake.run", audit_type=AuditType.PLATFORM):
        incident = await _runner.run(raw, correlation_id=current_correlation_id())

    artifact_state = "completed-needs-clarification" if incident.state == "needs_clarification" else "completed"
    task.artifacts.append(
        TaskArtifact(
            name="incident",
            parts=[
                DataPart(data=incident.model_dump(mode="json")),
                TextPart(text=incident.clarification_questions or ""),
            ],
        )
    )

    if incident.state == "needs_clarification":
        task.metadata.di.requires_human = True
        task.metadata.di.reason = "Missing required incident fields"
        task.status = TaskStatusModel(state=TaskStatus.INPUT_REQUIRED, message=task.status.message)
    else:
        task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


async def check_duplicate_handler(message: Message, task: Task) -> Task:
    """A2A capability: check_duplicate. Reuses step 5 with a pre-extracted incident."""
    data: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            data = part.data
            break
    if "summary" not in data:
        raise AgentError("check_duplicate requires field 'summary'", step=5)
    # Run only the embedding + nearest-neighbour search; SBCA still gates the threshold.
    threshold_rule = await _semantic.query_rule(domain=_cfg.semantic_queries.duplicate_threshold)
    similarity_min = float(threshold_rule["similarity"])
    await _qdrant.ensure_collection()
    embed = await _gateway.embedding(model=_cfg.embedding.alias, input=data["summary"])
    vector = embed.vectors[0] if embed.vectors else []
    if not vector:
        raise AgentError("Empty embedding", step=5)
    nearest = await _qdrant.nearest(vector=vector, limit=5)
    task.artifacts.append(
        TaskArtifact(
            name="duplicates",
            parts=[
                DataPart(
                    data={
                        "threshold": similarity_min,
                        "results": nearest,
                    }
                )
            ],
        )
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


# ─── A2A app construction ─────────────────────────────────────────────
_card = AgentCard(
    name="incident-intake-agent",
    description="Extract structured incident data from multi-channel inputs (Anthropic prompt-chaining workflow)",
    url=f"http://incident-intake-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["intake"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)

_handlers = HandlerRegistry.empty()
_handlers.register("submit_incident", submit_incident_handler)
_handlers.register("check_duplicate", check_duplicate_handler)


async def _probe_gateway() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{_gateway.base_url.rstrip('/')}/health/liveliness")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_sbca() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{_semantic._base_url.rstrip('/')}/health")  # noqa: SLF001 — internal probe
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def _probe_qdrant() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{_qdrant.url.rstrip('/')}/readyz")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="incident-intake-agent",
    version=_cfg.version,
    probes={"ai_gateway": _probe_gateway, "sbca": _probe_sbca, "qdrant": _probe_qdrant},
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
    # Capability registration — best-effort. SBCA might still be starting up.
    if _cfg.capability_registry.register_on_startup:
        try:
            await _semantic.register(
                CapabilityAdvertisement(
                    name="incident_intake",
                    url=str(_card.url),
                    version=_cfg.version,
                    skills=[s.id for s in _cfg.a2a.skills],
                )
            )
            _LOG.info("Registered with Capability Registry")
        except SemanticPlaneError as exc:
            _LOG.warning("Capability registration failed (will be retried on next startup): %s", exc)
    yield
    if _cfg.capability_registry.deregister_on_shutdown:
        try:
            await _semantic.deregister("incident_intake")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
