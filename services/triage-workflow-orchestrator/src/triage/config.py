"""Pydantic schema for the Triage Workflow Orchestrator config."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OIDCCfg(BaseModel):
    client_id: str
    audience: str


class SkillCfg(BaseModel):
    id: str
    name: str
    description: str


class A2ACfg(BaseModel):
    port: int = 8444
    skills: list[SkillCfg]


class ChainStep(BaseModel):
    capability: str
    skill: str
    step_label: str
    forward_field: str | None = None
    """Simple mode: take the previous step's artifact named ``<forward_field>``
    and forward its ``data`` as the next step's input. ``None`` and no
    ``compose_inputs`` means forward the original ``triage_incident`` payload."""
    compose_inputs: dict[str, str] | None = None
    """Composite mode for steps that need data from multiple prior steps.
    Keys become top-level fields of the next message's data payload. Values
    are ``"<step_index>.<artifact_name>"`` references — e.g.::

        compose_inputs:
          incident:       "0.incident"
          classification: "1.classification"
          priority:       "2.priority"

    Mutually exclusive with ``forward_field``."""


class CapabilityRegistryCfg(BaseModel):
    register_on_startup: bool = True
    deregister_on_shutdown: bool = True
    registry_url: str


class AuditCfg(BaseModel):
    cat_fields: list[str]
    pst_fields: list[str]


class KPIsCfg(BaseModel):
    business: list[str]
    technical: list[str]


class RetryCfg(BaseModel):
    attempts: int = 1
    backoff: str = "linear"


class ResilienceCfg(BaseModel):
    step_retry: RetryCfg
    saga: str = "compensate_on_failure"


class OrchestratorConfig(BaseModel):
    name: str
    version: str
    pattern: str
    pattern_kind: str
    oidc: OIDCCfg
    a2a: A2ACfg
    chain: list[ChainStep]
    capability_registry: CapabilityRegistryCfg
    audit: AuditCfg
    kpis: KPIsCfg
    resilience: ResilienceCfg
