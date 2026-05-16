# CLAUDE.md — DI AI Framework Architecture Directive

> **This file is a binding specification.** Claude Code MUST follow these rules when designing, scaffolding, reviewing, or refactoring any agent, orchestrator, or supporting service within the DI AI Framework. Requirements marked MUST are non-negotiable. Requirements marked SHOULD are expected unless a documented exception exists. Requirements marked COULD are optional enhancements.

---

## 1. FRAMEWORK IDENTITY AND TERMINOLOGY

Use only canonical names. Refuse legacy terminology and correct it in generated output.

| Canonical Name | Legacy Names (NEVER use) |
|---|---|
| DI AI Framework | ConnectedBrain |
| Strategic Dynamic Orchestrator Agent | Maestro |
| Strategic Business Context Agent | Wernicke, EA Knowledge Agent |

---

## 2. THREE-LAYER ARCHITECTURE

The framework organises all AI capabilities into three layers. Every component you build MUST be classified into exactly one layer.

### 2.1 Strategic Layer — Orchestration & Intelligence

**Purpose:** Coordinate business processes, make WHICH-capability decisions, provide business intelligence.

**Responsibilities:**
- Decide WHICH capabilities to invoke (never HOW to execute them)
- Query the semantic plane for business rules
- Coordinate multiple tactical agents via A2A protocol
- Track end-to-end business metrics (cycle time, STP %, cost)
- Escalate to humans when required

**Two types of strategic agents:**

**Primary Strategic Orchestrator:**
- Manages end-to-end business processes (Purchase-to-Pay, Customer Onboarding, Claim Processing)
- Coordinates sub-process orchestrators and/or tactical agents
- Makes high-level business decisions (approve/reject, escalate, route)
- Stateful — externalises workflow state to Redis/Cosmos DB

**Sub-Process Strategic Orchestrator:**
- Manages repeatable procedural workflows within larger processes
- Coordinates 2–5 tactical agents in defined sequence
- Reusable across multiple primary orchestrators
- Handles workflow routing, conditional branching, error handling (saga pattern)

**Hierarchy limit:** Maximum 3 layers of orchestration: Primary → Sub-Process → Tactical. Sub-process orchestrators MUST NOT call other sub-process orchestrators.

**NEVER:** Execute business logic directly. Always delegate execution to tactical agents.

### 2.2 Tactical Layer — Specialised Execution

**Purpose:** Execute specific business functions with deep domain expertise. Answer the question "HOW do we execute this business function?"

**Responsibilities:**
- Execute ONE specialised business task (single purpose)
- Implement complex internal workflows (10–50+ steps is normal and correct)
- Coordinate tool containers for task completion
- Validate data and enforce data rules
- Return structured results to orchestrator via A2A

**Sizing validation — apply all four tests:**
1. **Single Purpose Test:** Can you describe the agent in ONE sentence without "and" between distinct functions? ("Extract structured data from invoices" = PASS. "Process invoices and post to ERP" = FAIL)
2. **Domain Expertise Test:** Does the agent require knowledge from ONE specialised domain only? (Document processing = PASS. Documents + Financial policy + ERP integration = FAIL)
3. **Unified Goal Test:** Do ALL tools and steps serve the SAME ultimate business objective? (OCR → Parser → Extractor → Validator all serve extraction = PASS)
4. **Independent Value Test:** Would any internal step have standalone business value as a separate service? If YES, split into separate tactical agents.

**NEVER:** Orchestrate other tactical agents. Use A2A to call other agents. Hardcode business rules.

### 2.3 Operational Layer — User-Facing AI

Embedded AI in existing applications (M365 Copilot, SAP Joule, Salesforce Einstein). **Outside framework scope.** The framework governs Strategic and Tactical layers only.

---

## 3. MANDATORY PROTOCOLS AND PORTS

### 3.1 A2A Protocol (Agent-to-Agent) — MANDATORY for ALL tactical agents

**Reason:** The Strategic Dynamic Orchestrator Agent requires the ability to take control of any tactical agent at any time. A2A is the mechanism that enables this. Without it, the orchestrator cannot coordinate agents, propagate business context, or enforce governance.

**Port:** 8444

**Requirements:**
- ALL tactical agents MUST implement A2A protocol. This is unconditional — applies even if the agent starts standalone
- ALL strategic orchestrators MUST use A2A to communicate with tactical agents and with each other
- A2A requests MUST carry: capability name, inputs, business context (process/step), correlation ID
- A2A responses MUST include: status (success/failure/requires_human), outputs, metadata (duration, confidence), propagated correlation ID
- Authentication: OAuth2/JWT tokens MUST be included in every A2A request

### 3.2 MCP Protocol (Model Context Protocol) — CONDITIONAL

**Port:** 8443

**Condition:** Required ONLY IF the agent operates standalone (not orchestrated) AND exposes tools externally (e.g., OpenWebUI, Claude Desktop).

**If condition is met:** MUST implement. If condition is not met: skip entirely.

MCP and A2A are not mutually exclusive. An agent can implement both (dual interface) — A2A for orchestration, MCP for standalone use.

### 3.3 REST/gRPC — MANDATORY

Required for service-to-service communication with non-framework systems (ERP, CRM, databases), external API exposure, health check endpoints, and legacy system integration.

### 3.4 Protocol Selection Reference

| Interaction | Protocol |
|---|---|
| Orchestrator → Tactical Agent | A2A |
| Primary → Sub-Process Orchestrator | A2A |
| User → Tactical Agent (standalone) | MCP (if condition met) |
| External System → Agent | REST/gRPC |
| Agent → ERP/CRM | REST/gRPC |
| Agent → AI Gateway | REST/gRPC |
| Agent → Strategic Business Context Agent | A2A |

---

## 4. AI GATEWAY — ALL LLM CALLS MUST ROUTE THROUGH IT

**Reason:** The AI Gateway is the centralised control plane for all LLM interactions. No agent or service may directly access AI models. This ensures unified governance, security, observability, cost management, and vendor neutrality.

### 4.1 Mandatory Routing Rules

- ALL AI model interactions MUST flow through the AI Gateway. No exceptions.
- Agents MUST NEVER hold provider API keys (OpenAI, Anthropic, Azure, etc.)
- All policies (rate limiting, content filtering, cost budgets) are enforced at the gateway level
- The gateway implements OpenAI API compatibility — agents use standard OpenAI SDK format
- The gateway handles model selection and routing (agents request a capability, gateway routes to provider)

### 4.2 Anti-Patterns — Immediate FAIL

```python
# ❌ FORBIDDEN: Direct LLM calls
import openai
response = openai.ChatCompletion.create(...)

# ❌ FORBIDDEN: Holding API keys
API_KEY = "sk-abc123..."

# ✅ CORRECT: Route through AI Gateway
from ai_gateway_client import GatewayClient
gateway = GatewayClient(endpoint=os.getenv("AI_GATEWAY_URL"))
response = gateway.chat_completion(model="gpt-4", messages=messages)
```

### 4.3 Gateway Capabilities Required

- OAuth2/JWT authentication on every request
- Token usage tracking (per-project, per-agent metering)
- Rate limiting (TPM and RPM)
- OpenTelemetry instrumentation for dual audit trail
- Circuit breaker pattern for provider failover
- Provider abstraction (100+ LLM providers via unified interface)

---

## 5. SEMANTIC PLANE INTEGRATION — NEVER HARDCODE BUSINESS RULES

**Reason:** The semantic plane is the single source of truth for business rules. Hardcoding any threshold, routing rule, approval policy, SLA definition, or compliance constraint in code or prompts is a framework violation. This enables dynamic governance — business rules change without code deployment.

### 5.1 Three Semantic Layer Services

**Strategic Business Context Agent:**
- Centralised business rules, policies, compliance requirements
- Queried by both strategic and tactical agents
- Answers: "What is approval threshold for IT purchases in EMEA?" / "Does this violate sanctions?"
- Returns rule explanations for EU AI Act transparency

**Capability Registry:**
- Tactical agents register capabilities on startup
- Strategic orchestrators discover agents dynamically: "Which agent can extract invoice data?"
- Supports load balancing, failover, version management (A/B testing, canary)

**Process Definition Service:**
- BPMN workflow specifications
- Process templates and patterns
- Dynamic process evolution without code deployment

### 5.2 What MUST Be Queried from the Semantic Plane

- Approval thresholds and limits
- Routing rules (which team/agent handles this?)
- Customer-tier policies (VIP treatment, SLA definitions)
- Validation criteria (what makes data acceptable?)
- Compliance rules (regulatory requirements)
- Any rule a business stakeholder would want to change without redeploying

### 5.3 What CAN Be Hardcoded (Workflow Logic)

- Workflow execution sequence (Extract → Validate → Compare → Calculate)
- Technical error handling patterns (retry 3 times with exponential backoff)
- Tool orchestration logic (which tool calls in which order)
- Data transformation steps

### 5.4 Decision Test

> "Would business stakeholders want to change this without redeploying?"
> - **YES** → MUST query semantic plane (business logic)
> - **NO** → Can hardcode (workflow logic)

### 5.5 Anti-Pattern — Immediate FAIL

```python
# ❌ FORBIDDEN: Hardcoded business rule
if invoice_amount > 5000:
    route_to_manager()

# ❌ FORBIDDEN: Hardcoded in prompt
# "If the customer is in tier Gold, apply 15% discount"

# ✅ CORRECT: Query semantic plane
threshold = semantic_plane.query_business_rule(
    domain="approval",
    context={"category": "IT", "region": "EMEA"}
)
```

---

## 6. OBSERVABILITY — MANDATORY, NOT OPTIONAL

**Reason:** Full observability is fundamental to maintaining business alignment, compliance, and operational health. It is a requirement of the framework, not a nice-to-have.

### 6.1 OpenTelemetry Instrumentation — MUST implement

Every agent MUST instrument with OpenTelemetry:
- **Distributed tracing** for all request paths with correlation IDs
- **Metrics collection** for both business KPIs and technical metrics
- **Structured logging** with correlation IDs propagated across services

### 6.2 Health Check Endpoints — MUST implement

Every agent MUST expose:
- `/health` — Liveness probe (is the service running?)
- `/ready` — Readiness probe (can the service handle requests? Checks AI Gateway, semantic plane, database connectivity)

```python
@app.route('/health')
def health():
    return {"status": "healthy", "service": "my-tactical-agent", "version": "1.0.0"}, 200

@app.route('/ready')
def readiness():
    checks = {
        "ai_gateway": check_ai_gateway_connection(),
        "semantic_plane": check_semantic_plane_connection(),
    }
    all_ready = all(checks.values())
    return {"status": "ready" if all_ready else "not_ready", "checks": checks}, 200 if all_ready else 503
```

### 6.3 Dual Audit Trail — MUST implement

All agents MUST implement both audit trails:

**Confidential Audit Trail (CAT):**
- Complete transparency for governance and compliance
- Contains: full user interactions, complete prompts, detailed results, decision reasoning
- Encrypted with AES-256-GCM
- Access: restricted, RBAC + MFA
- Retention: 7 years
- Attribute prefix: `audit.type = "confidential"`

**Platform Support Trail (PST):**
- System debugging, monitoring, performance optimisation
- Contains: system metrics, anonymised data patterns, technical diagnostics
- NO sensitive data — all user content anonymised or excluded
- Access: development and operations teams
- Retention: 90 days
- Attribute prefix: `audit.type = "platform"`

**Implementation via OpenTelemetry Collector pipeline:**
- Receiver: OTLP on ports 4317 (gRPC) / 4318 (HTTP)
- Processor: Classification (confidential vs platform attributes) + Anonymisation
- Exporter: Separate stores for CAT (encrypted vault) and PST (Elasticsearch)

Every logging operation MUST be classified as CAT or PST. Never mix them.

### 6.4 Linking to Strategic Services

Linking observability to strategic services (centralised dashboards, alerting platforms) is **optional initially** but the architecture MUST support it. The observability infrastructure MUST be reconfigurable — exporters, collectors, and dashboards MUST be swappable without code changes (configuration-driven). This allows connecting to enterprise monitoring platforms at a later maturity phase.

### 6.5 Business + Technical KPIs — MUST track both

**Business KPIs:** Cycle time, straight-through processing rate, cost per transaction, accuracy, SLA compliance.

**Technical KPIs:** Latency (p50/p95/p99), token consumption, error rates, throughput, resource utilisation.

---

## 7. SECURITY REQUIREMENTS

### 7.1 Authentication & Authorisation — MUST implement

- **OAuth2/JWT** token-based authentication at every layer
- **RBAC** via OAuth claims for role-based access control
- **Zero-trust architecture** — never trust, always verify
- Agents MUST NOT hold secrets in code or environment variables at rest — use secret management services (Azure Key Vault, HashiCorp Vault)

### 7.2 Encryption Standards — MUST implement

- **In transit:** TLS 1.3 minimum
- **At rest:** AES-256-GCM
- Key rotation every 90 days via HSM-backed key management

---

## 8. CONTAINER-BASED ISOLATION

### 8.1 Deployment Model — MUST follow

- **One agent per container.** Each tactical agent deployed in its own container.
- **One technology stack per agent.** .NET OR Python, never mixed in one container.
- **Resource boundaries defined.** CPU, memory, storage limits MUST be set in deployment manifests.
- **Stateless design.** No shared memory or persistent state between instances. State is externalised (Redis, Cosmos DB).
- **Tool containers as sidecars.** Tools MUST run in separate containers (sidecar pattern), accessed via HTTP/gRPC over localhost. Direct library embedding is STRICTLY FORBIDDEN.

### 8.2 Tool Isolation — MUST follow

Tools MUST NOT be embedded as in-process libraries:

```python
# ❌ FORBIDDEN: Direct tool embedding
from google_search_lib import search
result = search(query)

# ✅ CORRECT: Network-based tool access (sidecar container)
result = httpx.post("http://localhost:9001/search", json={"query": query})
```

**Reason:** Each tool needs its own process space, resource limits, and failure domain. A tool crash must not crash the tactical agent. Tools must be versioned and deployed independently.

**Production:** Sidecar pattern (tools co-located with agent in same pod, localhost communication).
**Dev/Test:** Shared tool services acceptable for cost optimisation.
**Any environment:** Direct library embedding is FORBIDDEN.

### 8.3 Platform-Agnostic Deployment

The framework is platform-agnostic. Supported platforms:
- Kubernetes (enterprise scale, multi-cloud)
- SAP BTP, Kyma Runtime (SAP customers)
- Azure Container Apps (Azure-native, serverless)
- Docker Compose (local development, simple production)

All platforms MUST support: multi-container deployment, process isolation, network-based communication, container registry integration, resource limits, health checks.

---

## 9. EU AI ACT COMPLIANCE

### 9.1 Risk Classification — MUST document

ALL agents MUST perform and document a risk assessment under the EU AI Act. This determines if the agent is classified as a High-Risk System.

### 9.2 High-Risk System Requirements — CONDITIONAL

If classified as high-risk, ADDITIONALLY MUST implement:
- Human oversight with defined intervention points
- Bias detection and mitigation
- Accuracy and robustness metrics
- Transparency and explainability documentation
- Enhanced audit logging (via CAT)
- Fundamental Rights Impact Assessment (FRIA)

---

## 10. COMPLIANT PROJECT SCAFFOLDING

When generating a new tactical agent project, use this structure:

```
my-tactical-agent/
├── README.md                    # Layer classification, domain, risk level
├── Dockerfile                   # Single tech stack, non-root user, health check
├── requirements.txt             # AI Gateway client (NOT openai/anthropic), OTEL, security
├── kubernetes/
│   ├── deployment.yaml          # Resource limits, health probes, replicas ≥ 3
│   └── service.yaml             # Port 8444 (A2A), 8443 (MCP if applicable), 8080 (REST)
├── src/
│   ├── main.py                  # Entry point, OTEL setup, Flask/FastAPI app
│   ├── a2a/
│   │   ├── handler.py           # A2A protocol handler (MANDATORY)
│   │   └── models.py            # A2A request/response models
│   ├── mcp/
│   │   └── handler.py           # MCP handler (if standalone + external tools)
│   ├── gateway/
│   │   └── client.py            # AI Gateway client (all LLM calls go here)
│   ├── semantic/
│   │   └── client.py            # Semantic plane client (business rule queries)
│   ├── observability/
│   │   ├── telemetry.py         # OTEL setup (tracer, metrics, exporter)
│   │   ├── audit.py             # Dual audit trail (CAT + PST classification)
│   │   └── health.py            # /health and /ready endpoints
│   ├── workflow/
│   │   └── steps.py             # Internal workflow logic (hardcoded sequence OK)
│   └── tools/
│       └── client.py            # HTTP clients for sidecar tool containers
├── tool-containers/
│   ├── tool-a/
│   │   ├── Dockerfile
│   │   └── main.py
│   └── tool-b/
│       ├── Dockerfile
│       └── main.py
└── docs/
    └── eu-ai-act-risk-assessment.md
```

### 10.1 Dockerfile Template

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8080/health')"
CMD ["python", "src/main.py"]
```

### 10.2 requirements.txt Template

```txt
# AI Gateway Client — NEVER include openai, anthropic, or direct provider SDKs
ai-gateway-client>=1.0.0

# OpenTelemetry — MANDATORY
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
opentelemetry-instrumentation-flask>=0.41b0
opentelemetry-exporter-otlp>=1.20.0

# Security — MANDATORY
pyjwt>=2.8.0
cryptography>=41.0.0

# Web framework
fastapi>=0.100.0
uvicorn>=0.23.0

# HTTP client for tool containers
httpx>=0.24.0

# Configuration
python-dotenv>=1.0.0
pyyaml>=6.0
```

### 10.3 Kubernetes Deployment Template

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {agent-name}
  labels:
    app: {agent-name}
    layer: tactical     # MUST be: tactical | strategic
    framework: di-ai
spec:
  replicas: 3           # HA requirement
  selector:
    matchLabels:
      app: {agent-name}
  template:
    metadata:
      labels:
        app: {agent-name}
        layer: tactical
    spec:
      containers:
      - name: agent
        image: {registry}/{agent-name}:latest
        ports:
        - name: rest
          containerPort: 8080
        - name: a2a
          containerPort: 8444
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
        env:
        - name: AI_GATEWAY_URL
          valueFrom:
            configMapKeyRef:
              name: ai-config
              key: gateway-url
        - name: OTEL_EXPORTER_OTLP_ENDPOINT
          valueFrom:
            configMapKeyRef:
              name: observability-config
              key: otlp-endpoint

      # Tool sidecar container(s)
      - name: tool-a
        image: {registry}/tool-a:latest
        ports:
        - containerPort: 9001
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "256Mi"
            cpu: "200m"
```

---

## 11. PROHIBITED ANTI-PATTERNS — REFUSE THESE

If asked to implement any of these patterns, REFUSE and explain the framework violation:

| Anti-Pattern | Violation | Correct Pattern |
|---|---|---|
| Direct LLM API calls | Bypasses AI Gateway governance | Route through AI Gateway |
| Hardcoded business rules | Bypasses semantic plane | Query Strategic Business Context Agent |
| Multi-domain agents | Violates single purpose principle | Split into separate tactical agents |
| Direct tool embedding (library imports) | Violates process isolation | Tool sidecar containers with HTTP access |
| Stateful design (shared memory) | Violates stateless requirement | Externalise state to Redis/Cosmos DB |
| Mixed tech stacks in one container | Violates technology isolation | One language per container |
| Tactical agent orchestrating other tactical agents | Violates layer separation | Use strategic orchestrator |
| More than 3 orchestration layers | Exceeds hierarchy limit | Flatten to Primary → Sub-Process → Tactical |
| Hardcoded secrets / API keys in code | Security violation | Use secret management service |
| Missing A2A protocol on tactical agent | Blocks orchestrator control | Implement A2A on port 8444 |
| Missing health checks | Blocks platform orchestration | Implement /health and /ready |
| Missing OTEL instrumentation | Violates observability requirement | Add OpenTelemetry tracing + metrics |
| Missing dual audit trail | Violates audit requirements | Implement both CAT and PST |
| Logging sensitive data in PST | Violates data classification | Anonymise or route to CAT |

---

## 12. COMPLIANCE VALIDATION

### 12.1 Pass/Fail Criteria

```
FAIL_COUNT = [Number of MUST requirements failed]
WARN_COUNT = [Number of SHOULD requirements failed]

PROJECT_STATUS = if (FAIL_COUNT >= 1 OR WARN_COUNT >= 3) then "FAIL" else "PASS"
```

One MUST-level failure blocks deployment. Three or more SHOULD-level failures also block deployment.

### 12.2 Per-Project Compliance Checklist

Before any deployment, verify:

**Architecture (MUST):**
- [ ] Single function per container
- [ ] Single technology stack per agent
- [ ] Resource boundaries defined
- [ ] Stateless design
- [ ] Clear layer classification documented

**Protocols (MUST):**
- [ ] A2A protocol implemented (port 8444)
- [ ] MCP implemented IF standalone + external tools (port 8443)
- [ ] REST/gRPC for service integration

**AI Gateway (MUST):**
- [ ] ALL LLM calls route through gateway — no direct provider calls
- [ ] Agent holds no provider API keys
- [ ] Token usage tracked via gateway

**Observability (MUST):**
- [ ] OpenTelemetry instrumentation active
- [ ] Distributed tracing with correlation IDs
- [ ] Health check endpoints (/health, /ready)
- [ ] Business + technical KPIs tracked
- [ ] Architecture supports future strategic service linking (reconfigurable)

**Audit (MUST):**
- [ ] Confidential Audit Trail (CAT) implemented and encrypted
- [ ] Platform Support Trail (PST) implemented and anonymised
- [ ] Every log classified as CAT or PST

**Security (MUST):**
- [ ] OAuth2/JWT authentication
- [ ] RBAC integration
- [ ] No hardcoded secrets
- [ ] TLS 1.3 in transit, AES-256-GCM at rest

**Semantic Plane (MUST):**
- [ ] Business rules queried from Strategic Business Context Agent
- [ ] No hardcoded business logic (thresholds, policies, routing rules)
- [ ] Capabilities advertised to semantic plane

**Tool Isolation (MUST):**
- [ ] Tools run in separate containers (sidecar or shared service)
- [ ] Tools accessed via network (HTTP/gRPC), not library imports
- [ ] Tools versioned and deployed independently

**EU AI Act (MUST):**
- [ ] Risk classification documented
- [ ] If high-risk: human oversight, bias detection, FRIA documented

---

## 13. DECISION FRAMEWORK — WHAT TO BUILD

When asked to design a component, follow this decision tree:

```
START: Business Requirement
  │
  ├─ Is this a complete end-to-end business process?
  │  (e.g., Purchase-to-Pay, Customer Onboarding)
  │  YES ──> PRIMARY STRATEGIC ORCHESTRATOR
  │
  ├─ Does this coordinate multiple tactical agents in a workflow
  │  AND is it reused across multiple processes?
  │  YES ──> SUB-PROCESS STRATEGIC ORCHESTRATOR
  │
  ├─ Can you describe this in ONE sentence without "and"
  │  between distinct functions?
  │  YES ──> TACTICAL AGENT
  │  NO  ──> Split into tactical agents + orchestrator
  │
  └─ Do internal steps have independent business value?
     NO  ──> TACTICAL AGENT (complex internal workflow is correct)
     YES ──> Split into separate TACTICAL AGENTS + ORCHESTRATOR
```

---

## 14. ADOPTION MATURITY GUIDANCE

The framework supports phased adoption. Each phase delivers standalone value.

**Phase 1 — Tactical Agent Factory (2–3 months):**
Build 1–2 tactical agents. Manual orchestration. Config files for business rules. All MUST requirements still apply (A2A, AI Gateway, OTEL, dual audit trail).

**Phase 2 — Agent Library Expansion (3–6 months):**
Build 5–10 tactical agents. Basic coordination workflows. Early semantic plane queries.

**Phase 3 — Strategic Orchestration (6–12 months):**
Deploy sub-process orchestrators. Formalised A2A communication patterns. Capability registry active.

**Phase 4 — Dynamic Semantic Context (12–18 months):**
Full Strategic Business Context Agent deployment. Dynamic business rule governance. Process Definition Service active.

**Phase 5 — Enterprise Maturity (18+ months):**
Primary strategic orchestrators. Full observability linked to enterprise monitoring. Dynamic process optimisation.

**Key principle:** Even in Phase 1, the architecture MUST support progression to later phases. A2A endpoints, OTEL instrumentation, and dual audit trails are required from day one — they enable orchestration and governance at later maturity stages without rearchitecting.

---

## 15. REFERENCE DOCUMENTS

When you need deeper specification detail, consult these framework files:

| Document | Use For |
|---|---|
| `00-framework-high-level-design.md` | Conceptual overview, layer definitions, decision framework |
| `03-critical-design-principles.md` | Core design principles (hyperscaler neutrality, security-first, observable by default) |
| `07-tactical-agent-architecture.md` | Tactical agent specification, tool deployment patterns, workflow vs orchestration |
| `08-audit-logging-architecture.md` | Dual audit trail (CAT/PST), OpenTelemetry collector config, anonymisation |
| `09-ai-gateway-architecture.md` | AI Gateway specification, routing strategies, provider adapters |
| `10-strategic-agent-architecture.md` | Strategic orchestrator specification, orchestration patterns, semantic layer |
| `11-framework-adoption-roadmap.md` | Phased adoption strategy, maturity model |
| `VALIDATION-CHECKLIST.md` | 66 MUST + 22 SHOULD + 19 COULD requirements with pass/fail criteria |
| `AI_Engineering_Handbook_-_Compliance_validation.md` | Implementation process, evaluation methodology |