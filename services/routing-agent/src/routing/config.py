from __future__ import annotations

from pydantic import BaseModel, Field


class ModelCfg(BaseModel):
    alias: str
    temperature: float = 0.05
    max_tokens: int = 384


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


class MCPCfg(BaseModel):
    enabled: bool = False
    port: int = 8443
    tools: list[str] = Field(default_factory=list)


class CapabilityRegistryCfg(BaseModel):
    register_on_startup: bool = True
    deregister_on_shutdown: bool = True


class ToolEndpoint(BaseModel):
    url: str
    timeout_seconds: float = 5.0


class PromptCfg(BaseModel):
    rank_system_path: str
    rank_user_path: str


class SemanticQueries(BaseModel):
    routing_rules: str
    routing_priority_overrides: str
    load_balancing: str
    routing_llm_weight: str


class AuditCfg(BaseModel):
    cat_fields: list[str]
    pst_fields: list[str]


class KPIsCfg(BaseModel):
    business: list[str]
    technical: list[str]


class RetryCfg(BaseModel):
    attempts: int = 3
    backoff: str = "exponential"


class ResilienceCfg(BaseModel):
    tool_retry: RetryCfg
    llm_retry: RetryCfg
    sbca_failure: str = "hard_fail"


class AgentConfig(BaseModel):
    name: str
    version: str
    pattern: str
    pattern_kind: str
    model: ModelCfg
    oidc: OIDCCfg
    a2a: A2ACfg
    mcp: MCPCfg = MCPCfg()
    capability_registry: CapabilityRegistryCfg = CapabilityRegistryCfg()
    tools: dict[str, ToolEndpoint]
    prompts: PromptCfg
    semantic_queries: SemanticQueries
    audit: AuditCfg
    kpis: KPIsCfg
    resilience: ResilienceCfg
