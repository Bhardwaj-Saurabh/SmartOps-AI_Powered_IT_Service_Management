"""Pydantic schema for the Resolution Workflow Orchestrator config."""
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
    compose_inputs: dict[str, str] | None = None
    """Either ``forward_field`` (single-artifact passthrough) OR
    ``compose_inputs`` (composite). Keys in ``compose_inputs`` become top-
    level fields of the next message's data payload. Values are references:

      ``input.<key>``                — caller's initial payload
      ``<step_idx>.<artifact_name>`` — a prior step's artifact
    """


class CapabilityRegistryCfg(BaseModel):
    register_on_startup: bool = True
    deregister_on_shutdown: bool = True
    registry_url: str


class SagaCompensation(BaseModel):
    """Stage 4a stub. Stage 4b populates this with per-step rollback hooks
    keyed on which downstream step's failure triggers the compensation."""
    on_failure_of_step: int
    capability: str
    skill: str


class SagaCfg(BaseModel):
    enabled: bool = False
    compensations: list[SagaCompensation] = Field(default_factory=list)


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
    saga: SagaCfg = SagaCfg()
    audit: AuditCfg
    kpis: KPIsCfg
    resilience: ResilienceCfg
