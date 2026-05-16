"""FastAPI entrypoint for the Communication Agent."""
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

from communication.config import AgentConfig
from communication.models import CommunicationInput
from communication.tools import EmailSender, SlackPoster, SmsGateway
from communication.workflow import CommunicationRunner


_LOG = logging.getLogger("communication-agent")
_CFG_PATH = os.environ.get(
    "AGENT_CONFIG_PATH", "/app/services/communication-agent/configs/agent.yaml"
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
_email = EmailSender(_cfg.tools["email_sender"].url, _cfg.tools["email_sender"].timeout_seconds)
_slack = SlackPoster(_cfg.tools["slack_poster"].url, _cfg.tools["slack_poster"].timeout_seconds)
_sms = SmsGateway(_cfg.tools["sms_gateway"].url, _cfg.tools["sms_gateway"].timeout_seconds)

_runner = CommunicationRunner(
    cfg=_cfg, gateway=_gateway, semantic=_semantic,
    email=_email, slack=_slack, sms=_sms,
)


async def send_handler(message: Message, task: Task) -> Task:
    payload: dict[str, Any] = {}
    for part in message.parts:
        if isinstance(part, DataPart):
            payload = part.data
            break
    try:
        inp = CommunicationInput.model_validate(payload)
    except Exception as exc:
        raise AgentError(f"Invalid send_status_update payload: {exc}", step=1) from exc

    with audit_span("comm.run", audit_type=AuditType.PLATFORM):
        result = await _runner.run(inp)

    task.artifacts.append(
        TaskArtifact(name="communications_sent", parts=[DataPart(data=result.model_dump(mode="json"))])
    )
    task.status = TaskStatusModel(state=TaskStatus.COMPLETED, message=task.status.message)
    return task


_card = AgentCard(
    name="communication-agent",
    description="Generate + send audience-tailored incident updates (prompt-chaining workflow; MCP-enabled per PRD)",
    url=f"http://communication-agent:{_cfg.a2a.port}",
    version=_cfg.version,
    capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
    skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=["communication"]) for s in _cfg.a2a.skills],
    securitySchemes={
        "keycloak": {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{os.environ.get('KEYCLOAK_REALM_URL', '').rstrip('/')}/.well-known/openid-configuration",
        }
    },
    security=[{"keycloak": ["agent"]}],
)
_handlers = HandlerRegistry.empty()
_handlers.register("send_status_update", send_handler)


async def _probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            return (await c.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


_health = HealthCheck(
    service="communication-agent", version=_cfg.version,
    probes={
        "ai_gateway":  lambda: _probe_http(f"{_gateway.base_url.rstrip('/')}/health/liveliness"),
        "sbca":        lambda: _probe_http(f"{_semantic._base_url.rstrip('/')}/health"),  # noqa: SLF001
        "email_sender": lambda: _probe_http(f"{_email.base_url}/health"),
        "slack_poster": lambda: _probe_http(f"{_slack.base_url}/health"),
        "sms_gateway":  lambda: _probe_http(f"{_sms.base_url}/health"),
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
                    name="send_status_update",
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
            await _semantic.deregister("send_status_update")
        except SemanticPlaneError:
            pass


_agent_app.fastapi.router.lifespan_context = _lifespan
app = _agent_app.fastapi
