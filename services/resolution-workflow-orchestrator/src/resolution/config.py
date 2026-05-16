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


class SagaArtifactPredicate(BaseModel):
    """Fires when a step COMPLETES and its artifact field matches the predicate.
    This is how Verification reporting fix_verified=false triggers Automated
    Fix rollback — Verification itself returns COMPLETED (it reports a fact),
    so we need an artifact-content trigger rather than a state trigger."""
    step_index: int
    artifact: str
    field: str
    equals: bool | str | int | float | None = None


class SagaStepFailure(BaseModel):
    """Fires when the named step ends in failed / rejected / canceled."""
    step_index: int


class SagaTrigger(BaseModel):
    on_step_failure: SagaStepFailure | None = None
    on_artifact_predicate: SagaArtifactPredicate | None = None


class SagaAction(BaseModel):
    capability: str
    skill: str
    params_from_artifact: dict[str, str] = Field(default_factory=dict)
    """Map: param_key → ``<step_idx>.<artifact_name>.<field_path>`` (dot
    notation into the artifact's .data). Resolved at runtime from chain
    outputs."""
    reason: str = ""


class SagaCompensation(BaseModel):
    trigger: SagaTrigger
    action: SagaAction


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
