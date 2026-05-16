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


class CapabilityRegistryCfg(BaseModel):
    register_on_startup: bool = True
    deregister_on_shutdown: bool = True
    registry_url: str


class SagaCfg(BaseModel):
    enabled: bool = True
    compensations: list[dict] = Field(default_factory=list)
    run_closure_on_failed_resolution_rule: str = "i2r_run_closure_on_failed_resolution"


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


class EscalationCfg(BaseModel):
    rule: str = "i2r_escalation_criteria"


class SemanticQueries(BaseModel):
    i2r_escalation_criteria: str
    i2r_run_closure_on_failed_resolution: str


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
    escalation: EscalationCfg = EscalationCfg()
    semantic_queries: SemanticQueries
