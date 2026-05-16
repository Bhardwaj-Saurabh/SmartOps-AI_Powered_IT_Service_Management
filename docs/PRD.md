# DI AI Framework Reference Implementation: SmartOps — AI-Powered IT Service Management

## Purpose

This document defines a complete, self-contained application for building, testing, and implementing the DI AI Framework from the ground up. The domain is **IT Service Management (ITSM)** — managing the lifecycle of IT incidents, service requests, changes, and problems across an organisation. This domain was chosen because it naturally decomposes into 10+ distinct single-purpose functions, follows a clear end-to-end business process, requires dynamic business rules, and has no dependency on any Delaware-specific use case.

The application exercises every mandatory framework component: 12 tactical agents, 3 sub-process orchestrators, 1 primary orchestrator, MCP servers, tool containers, AI Gateway, semantic plane, dual audit trails (CAT/PST), and full OpenTelemetry observability.

---

## Business Process: Incident-to-Resolution (I2R)

The end-to-end business process is **Incident-to-Resolution**: from the moment an IT issue is reported, through classification, investigation, resolution, and post-incident review.

```
User reports incident
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  PRIMARY ORCHESTRATOR: I2R Process Orchestrator         │
│                                                         │
│  1. Receive incident (UI, email, Slack, monitoring)     │
│  2. Query semantic plane: "Priority rules?"             │
│  3. DECIDE: Auto-resolve OR Investigate                 │
│  4. Coordinate sub-process: Triage & Classification     │
│  5. DECIDE: Route to correct resolution team            │
│  6. Coordinate sub-process: Investigation & Resolution  │
│  7. DECIDE: Escalate? Change needed? Problem created?   │
│  8. Coordinate sub-process: Closure & Review            │
│  9. Track I2R cycle time, SLA compliance, MTTR          │
└─────────────────────────────────────────────────────────┘
```

---

## Architecture Overview

### Strategic Layer

| Component | Type | Purpose |
|---|---|---|
| **I2R Process Orchestrator** | Primary Orchestrator | End-to-end incident lifecycle from report to post-incident review |
| **Triage Workflow Orchestrator** | Sub-Process Orchestrator | Coordinates intake → classification → priority assignment → routing |
| **Resolution Workflow Orchestrator** | Sub-Process Orchestrator | Coordinates diagnosis → knowledge search → fix application → verification |
| **Closure Workflow Orchestrator** | Sub-Process Orchestrator | Coordinates resolution documentation → user confirmation → SLA reporting → problem linkage |

### Tactical Layer (12 Agents)

| # | Agent | Single Purpose (one sentence) | Domain | Tools (sidecars) |
|---|---|---|---|---|
| 1 | **Incident Intake Agent** | Extract structured incident data from multi-channel inputs | Natural language processing | Email Parser, Slack Connector, Form Normaliser |
| 2 | **Classification Agent** | Classify incidents by category, subcategory, and service area | Taxonomy & categorisation | Taxonomy Lookup, Historical Pattern Matcher |
| 3 | **Priority Scorer** | Calculate incident priority from impact and urgency assessment | Risk scoring | Impact Analyser, Service Dependency Mapper |
| 4 | **Routing Agent** | Determine the correct resolver group for a classified incident | Organisational routing | Team Directory Connector, Skill Matrix Lookup |
| 5 | **Diagnostic Agent** | Perform automated root cause analysis using log and metric correlation | Infrastructure diagnostics | Log Aggregator Connector, Metrics Query Tool, Topology Walker |
| 6 | **Knowledge Search Agent** | Find relevant knowledge articles and past resolution patterns | Information retrieval | Knowledge Base Connector, Embedding Search Tool |
| 7 | **Automated Fix Agent** | Execute pre-approved automated remediation runbooks | Runbook automation | Script Executor, Configuration Manager, Rollback Handler |
| 8 | **Verification Agent** | Verify that an applied fix resolved the reported symptoms | Validation & testing | Health Check Runner, Synthetic Monitor, Comparison Tool |
| 9 | **Communication Agent** | Generate and send status updates to affected stakeholders | Stakeholder communication | Email Sender, Slack Poster, SMS Gateway |
| 10 | **SLA Monitor Agent** | Calculate SLA compliance metrics and detect breaches in real time | SLA analytics | Clock/Timer Service, SLA Rules Engine |
| 11 | **Resolution Documenter** | Generate structured resolution notes and update the knowledge base | Technical writing | Document Formatter, Knowledge Base Writer |
| 12 | **Problem Linker Agent** | Identify recurring incident patterns and link them to problem records | Pattern detection | Incident History Connector, Clustering Tool |

### Semantic Layer Services

| Service | Purpose |
|---|---|
| **Strategic Business Context Agent** | Holds all dynamic business rules: priority matrices, SLA definitions, escalation policies, routing rules, auto-fix approval criteria, change-freeze calendars |
| **Capability Registry** | Tactical agents register on startup; orchestrators discover agents by capability name, not hardcoded service addresses |
| **Process Definition Service** | BPMN workflow definitions for the I2R process and sub-processes; supports versioned process evolution |

### Infrastructure Services

| Service | Purpose |
|---|---|
| **AI Gateway** | All LLM calls from all 12 tactical agents route through it. OpenAI API-compatible interface. Handles model selection, rate limiting, token metering, circuit breaking |
| **OpenTelemetry Collector** | Receives traces/metrics/logs from all agents. Classification processor splits into CAT and PST pipelines. Exports to separate storage |

---

## Detailed Agent Specifications

### Agent 1: Incident Intake Agent

**Purpose:** Extract structured incident data from multi-channel inputs.

**Domain expertise:** Natural language processing, entity extraction, multi-format parsing.

**Internal workflow (12+ steps):**
1. Receive raw input (email body, Slack message, web form, monitoring alert)
2. Detect input channel and format
3. Call AI Gateway → LLM extracts: reporter, affected service, symptoms, timestamp
4. Normalise extracted fields to canonical schema
5. Detect duplicate against open incidents (embedding similarity via tool)
6. If duplicate: flag and link, return early
7. Enrich with reporter context (department, VIP status) via tool lookup
8. Validate completeness (all required fields present)
9. If incomplete: generate clarification questions
10. Assign initial incident ID
11. Emit structured incident record
12. Log to CAT (full content) and PST (anonymised metadata)

**Tool containers (sidecars):**
- `email-parser` (port 9001): Parses MIME emails, extracts body/attachments
- `slack-connector` (port 9002): Slack API integration, message retrieval
- `form-normaliser` (port 9003): Maps various web form schemas to canonical format

**Semantic plane queries:**
- "What fields are required for incident intake in the [service area] domain?"
- "What is the duplicate detection threshold?"

**A2A interface (port 8444):** Accepts `incident_intake` capability requests from Triage Workflow Orchestrator.

**MCP interface (port 8443):** YES — this agent also operates standalone. Users can submit incidents directly via Claude Desktop or a chat UI without going through the orchestrator. Exposes `submit_incident` and `check_duplicate` tools.

---

### Agent 2: Classification Agent

**Purpose:** Classify incidents by category, subcategory, and service area.

**Domain expertise:** Taxonomy management, multi-label classification.

**Internal workflow (8+ steps):**
1. Receive structured incident from intake
2. Call AI Gateway → LLM classifies against taxonomy (category, subcategory, service)
3. Tool: Historical Pattern Matcher → find similar past incidents and their classifications
4. If LLM classification and historical match diverge: use weighted confidence scoring
5. Tool: Taxonomy Lookup → validate classification exists in current taxonomy version
6. Query semantic plane: "What classification overrides apply for [service area]?"
7. Apply overrides if applicable
8. Return classification with confidence score

**Tool containers:**
- `taxonomy-lookup` (port 9001): Validates against versioned ITSM taxonomy
- `historical-pattern-matcher` (port 9002): Embedding-based similarity search against closed incidents

**Semantic plane queries:**
- "Current taxonomy version and valid categories for [domain]?"
- "Are there classification override rules for [service area]?"

**A2A interface:** Accepts `incident_classification` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 3: Priority Scorer

**Purpose:** Calculate incident priority from impact and urgency assessment.

**Domain expertise:** Risk scoring, impact analysis.

**Internal workflow (10+ steps):**
1. Receive classified incident
2. Call AI Gateway → LLM assesses impact narrative (how many users, business criticality)
3. Tool: Service Dependency Mapper → identify upstream/downstream service dependencies
4. Calculate blast radius (number of affected services)
5. Tool: Impact Analyser → quantify business impact (revenue risk, user count)
6. Query semantic plane: "What is the priority matrix for [service tier]?"
7. Apply priority matrix (Impact × Urgency → Priority 1-4)
8. Query semantic plane: "Are there VIP escalation rules for [reporter/department]?"
9. Apply VIP adjustments if applicable
10. Return priority score with full reasoning chain (for explainability)

**Tool containers:**
- `impact-analyser` (port 9001): Calculates business impact metrics
- `service-dependency-mapper` (port 9002): CMDB integration, returns service topology

**Semantic plane queries:**
- "Priority matrix for [service tier]?"
- "VIP escalation rules for [department]?"
- "Current change-freeze calendar?"

**A2A interface:** Accepts `priority_scoring` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 4: Routing Agent

**Purpose:** Determine the correct resolver group for a classified incident.

**Domain expertise:** Organisational routing, skill-based assignment.

**Internal workflow (8+ steps):**
1. Receive classified and prioritised incident
2. Query semantic plane: "Routing rules for [category/subcategory] at [priority]?"
3. Tool: Team Directory Connector → get available resolver groups
4. Tool: Skill Matrix Lookup → match required skills to team capabilities
5. Call AI Gateway → LLM ranks candidate teams by fit
6. Check team capacity (queue depth from tool)
7. Apply load-balancing logic
8. Return resolver group assignment with routing rationale

**Tool containers:**
- `team-directory-connector` (port 9001): Queries HR/directory system for team structures
- `skill-matrix-lookup` (port 9002): Maps required skills to team competencies

**Semantic plane queries:**
- "Routing rules for [category] at [priority level]?"
- "Load-balancing policy?"

**A2A interface:** Accepts `incident_routing` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 5: Diagnostic Agent

**Purpose:** Perform automated root cause analysis using log and metric correlation.

**Domain expertise:** Infrastructure diagnostics, observability data analysis.

**Internal workflow (15+ steps):**
1. Receive incident with classification and affected services
2. Tool: Metrics Query Tool → pull relevant metrics (CPU, memory, latency, error rates) for time window
3. Tool: Log Aggregator Connector → search for error patterns in logs
4. Tool: Topology Walker → trace request path through service mesh
5. Call AI Gateway → LLM correlates metrics anomalies with log patterns
6. Identify candidate root causes (ranked by confidence)
7. For each candidate: Tool: Metrics Query → validate hypothesis against additional data
8. Call AI Gateway → LLM refines diagnosis based on validation results
9. Iterative refinement loop (max 3 iterations or until confidence > threshold from semantic plane)
10. Generate diagnostic report with evidence chain
11. Classify root cause type (infrastructure, application, configuration, external)
12. Return diagnosis with confidence and supporting evidence

**Tool containers:**
- `log-aggregator-connector` (port 9001): Queries Elasticsearch/Loki for log patterns
- `metrics-query-tool` (port 9002): Queries Prometheus/Grafana for time-series data
- `topology-walker` (port 9003): Traverses service mesh topology from CMDB

**Semantic plane queries:**
- "What is the minimum confidence threshold for automated diagnosis?"
- "Known issues and workarounds for [service/component]?"

**A2A interface:** Accepts `root_cause_analysis` capability.

**MCP interface:** YES — diagnostics can also be triggered standalone by on-call engineers via Claude Desktop. Exposes `diagnose_service` and `correlate_logs` tools.

---

### Agent 6: Knowledge Search Agent

**Purpose:** Find relevant knowledge articles and past resolution patterns.

**Domain expertise:** Information retrieval, semantic search.

**Internal workflow (10+ steps):**
1. Receive diagnosis/incident context
2. Build search query from incident classification + diagnosis keywords
3. Tool: Embedding Search Tool → vector similarity search against knowledge base
4. Tool: Knowledge Base Connector → structured keyword search (complementary)
5. Call AI Gateway → LLM evaluates relevance of top-N results
6. Filter by recency and applicability (version compatibility)
7. Rank by resolution success rate (historical effectiveness)
8. Query semantic plane: "Knowledge article freshness policy?"
9. Flag stale articles
10. Return ranked list of relevant articles with relevance scores and excerpts

**Tool containers:**
- `knowledge-base-connector` (port 9001): REST API to Confluence/ServiceNow knowledge base
- `embedding-search-tool` (port 9002): Vector database (Qdrant/Weaviate) similarity search

**Semantic plane queries:**
- "Knowledge article freshness policy (max age)?"
- "Minimum relevance score for article recommendation?"

**A2A interface:** Accepts `knowledge_search` capability.

**MCP interface:** YES — knowledge search is independently useful for engineers browsing solutions. Exposes `search_knowledge` and `find_similar_incidents` tools.

---

### Agent 7: Automated Fix Agent

**Purpose:** Execute pre-approved automated remediation runbooks.

**Domain expertise:** Runbook automation, infrastructure remediation.

**Internal workflow (12+ steps):**
1. Receive diagnosis and recommended fix
2. Query semantic plane: "Is automated remediation approved for [fix type] on [service tier]?"
3. If not approved: return `requires_human` status
4. Tool: Script Executor → retrieve runbook from runbook library
5. Validate runbook parameters against incident context
6. Tool: Configuration Manager → snapshot current state (for rollback)
7. Execute runbook step-by-step (sequential)
8. After each step: check for errors
9. If error: Tool: Rollback Handler → restore from snapshot
10. If success: record execution log
11. Call AI Gateway → LLM summarise what was changed
12. Return execution result with change summary

**Tool containers:**
- `script-executor` (port 9001): Sandboxed script runner (SSH/API-based)
- `configuration-manager` (port 9002): Takes/restores configuration snapshots
- `rollback-handler` (port 9003): Reverts changes on failure

**Semantic plane queries:**
- "Is auto-fix approved for [fix type] on [service tier]?"
- "Maximum auto-fix scope (how many systems at once)?"
- "Change-freeze calendar — is auto-fix allowed right now?"

**A2A interface:** Accepts `apply_automated_fix` capability.

**MCP interface:** No — auto-fix always requires orchestrator context for safety governance.

---

### Agent 8: Verification Agent

**Purpose:** Verify that an applied fix resolved the reported symptoms.

**Domain expertise:** Validation, synthetic testing.

**Internal workflow (10+ steps):**
1. Receive incident context and applied fix description
2. Tool: Health Check Runner → execute health checks on affected service
3. Tool: Synthetic Monitor → replay the failing scenario
4. Compare pre-fix and post-fix metrics
5. Tool: Comparison Tool → statistical comparison (before/after)
6. Call AI Gateway → LLM evaluate whether symptoms are resolved
7. If symptoms persist: return `fix_failed` with evidence
8. If symptoms resolved: calculate time-to-recovery
9. Query semantic plane: "What is the verification soak period for [priority]?"
10. If soak period required: schedule delayed re-check

**Tool containers:**
- `health-check-runner` (port 9001): Runs HTTP/TCP/custom health checks
- `synthetic-monitor` (port 9002): Replays recorded user scenarios
- `comparison-tool` (port 9003): Statistical comparison engine

**A2A interface:** Accepts `verify_resolution` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 9: Communication Agent

**Purpose:** Generate and send status updates to affected stakeholders.

**Domain expertise:** Stakeholder communication, multi-channel messaging.

**Internal workflow (8+ steps):**
1. Receive incident status update and audience
2. Query semantic plane: "Communication templates and frequency rules for [priority]?"
3. Call AI Gateway → LLM generate audience-appropriate update (technical vs executive vs end-user)
4. Apply branding and formatting from template
5. Tool: resolve recipient list from affected users/stakeholders
6. Tool: Email Sender / Slack Poster / SMS Gateway → send to appropriate channels
7. Log all communications to CAT
8. Return delivery confirmation

**Tool containers:**
- `email-sender` (port 9001): SMTP/SendGrid integration
- `slack-poster` (port 9002): Slack Webhook/API integration
- `sms-gateway` (port 9003): SMS/Twilio integration

**Semantic plane queries:**
- "Communication templates for [priority] incidents?"
- "Notification frequency policy?"
- "Escalation communication rules (who gets notified at what threshold)?"

**A2A interface:** Accepts `send_status_update` capability.

**MCP interface:** YES — engineers can use standalone to craft and send incident comms via Claude Desktop. Exposes `draft_communication` and `send_update` tools.

---

### Agent 10: SLA Monitor Agent

**Purpose:** Calculate SLA compliance metrics and detect breaches in real time.

**Domain expertise:** SLA analytics, time-based calculations.

**Internal workflow (8+ steps):**
1. Receive incident with priority and timestamps
2. Query semantic plane: "SLA targets for [priority] [category] [customer tier]?"
3. Tool: Clock/Timer Service → calculate elapsed time against SLA targets
4. Tool: SLA Rules Engine → apply business hours, pause rules, exclusions
5. Calculate: time to respond, time to resolve, percentage consumed
6. If approaching breach threshold: emit warning
7. If breached: emit breach event
8. Return SLA status snapshot

**Tool containers:**
- `clock-timer-service` (port 9001): Business-hours-aware time calculator
- `sla-rules-engine` (port 9002): Applies SLA pausing, exclusion, and escalation rules

**Semantic plane queries:**
- "SLA targets for [priority] [category] [customer tier]?"
- "Business hours definition for [region]?"
- "SLA pause conditions?"

**A2A interface:** Accepts `calculate_sla_status` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 11: Resolution Documenter

**Purpose:** Generate structured resolution notes and update the knowledge base.

**Domain expertise:** Technical writing, knowledge management.

**Internal workflow (10+ steps):**
1. Receive full incident context: diagnosis, fix applied, verification results
2. Call AI Gateway → LLM generate structured resolution notes (root cause, fix, prevention)
3. Classify resolution type (known-error fix, new solution, workaround, manual)
4. Tool: Document Formatter → apply documentation template
5. Query semantic plane: "Knowledge base update criteria?"
6. If new solution: Tool: Knowledge Base Writer → create new article
7. If existing article: Tool: Knowledge Base Writer → update article with additional context
8. Generate lessons-learned summary
9. Tag resolution for future pattern matching
10. Return documentation record

**Tool containers:**
- `document-formatter` (port 9001): Applies markdown/HTML templates
- `knowledge-base-writer` (port 9002): Creates/updates articles in Confluence/ServiceNow

**Semantic plane queries:**
- "Knowledge base update criteria (when to create vs update)?"
- "Resolution documentation template for [category]?"

**A2A interface:** Accepts `document_resolution` capability.

**MCP interface:** No — always orchestrated.

---

### Agent 12: Problem Linker Agent

**Purpose:** Identify recurring incident patterns and link them to problem records.

**Domain expertise:** Pattern detection, incident correlation.

**Internal workflow (10+ steps):**
1. Receive resolved incident with classification and root cause
2. Tool: Incident History Connector → retrieve recent incidents with same classification
3. Tool: Clustering Tool → cluster by root cause similarity
4. Call AI Gateway → LLM assess whether pattern indicates a systemic problem
5. If cluster size exceeds threshold from semantic plane: flag as recurring
6. Query semantic plane: "Problem creation criteria?"
7. If criteria met and no existing problem record: recommend new problem creation
8. If existing problem record: link incident to problem
9. Update pattern statistics (frequency, trend direction)
10. Return linkage result and pattern analysis

**Tool containers:**
- `incident-history-connector` (port 9001): Queries incident database
- `clustering-tool` (port 9002): Embedding-based clustering engine

**Semantic plane queries:**
- "Problem creation threshold (minimum recurring incidents)?"
- "Problem creation criteria?"

**A2A interface:** Accepts `link_to_problem` capability.

**MCP interface:** No — always orchestrated.

---

## Orchestrator Specifications

### Primary Orchestrator: I2R Process Orchestrator

**Type:** Primary Strategic Orchestrator

**Business process:** Incident-to-Resolution (end-to-end)

**Business decisions (queried from semantic plane, never hardcoded):**
- Should this incident be auto-resolved (known-error match)?
- Which resolution path? (auto-fix vs manual investigation)
- Should this escalate? (SLA breach, VIP, high blast radius)
- Is a change record needed? (if fix involves infrastructure changes)
- Should a problem record be created? (recurring pattern detected)

**Coordinates (via A2A):**
- Triage Workflow Orchestrator
- Resolution Workflow Orchestrator
- Closure Workflow Orchestrator
- Communication Agent (directly, for escalation notifications)
- SLA Monitor Agent (directly, for continuous SLA tracking)

**State:** Externalised to Redis. Incident state machine: New → Triaged → Investigating → Resolving → Verifying → Resolved → Closed.

**Business KPIs tracked:**
- Mean Time to Resolution (MTTR)
- SLA compliance rate
- First-contact resolution rate
- Auto-resolution rate
- Escalation rate

**Orchestration pattern:** Event-driven with Saga compensation. If resolution fails, compensates by reverting auto-fix and escalating to manual.

---

### Sub-Process Orchestrator: Triage Workflow

**Type:** Sub-Process Strategic Orchestrator

**Coordinates (via A2A):**
1. Incident Intake Agent → structured incident
2. Classification Agent → categorised incident
3. Priority Scorer → prioritised incident
4. Routing Agent → assigned resolver group

**Reused by:** I2R Process Orchestrator, and potentially future Change Management and Service Request processes.

**Error handling:** If classification fails, retry once with enriched context. If routing fails (no matching team), escalate to primary orchestrator.

---

### Sub-Process Orchestrator: Resolution Workflow

**Type:** Sub-Process Strategic Orchestrator

**Coordinates (via A2A):**
1. Diagnostic Agent → root cause analysis
2. Knowledge Search Agent → relevant solutions
3. Automated Fix Agent → remediation (if approved)
4. Verification Agent → confirm fix worked

**Reused by:** I2R Process Orchestrator, and potentially future Problem Management resolution process.

**Error handling:** Saga pattern — if Verification Agent reports `fix_failed`, orchestrator calls Automated Fix Agent rollback, then escalates to manual resolution.

---

### Sub-Process Orchestrator: Closure Workflow

**Type:** Sub-Process Strategic Orchestrator

**Coordinates (via A2A):**
1. Resolution Documenter → generate and store resolution notes
2. Communication Agent → send resolution confirmation to user
3. SLA Monitor Agent → final SLA calculation
4. Problem Linker Agent → check for recurring patterns

**Reused by:** I2R Process Orchestrator, and potentially future Problem Management closure.

---

## MCP Server Summary

Four agents expose MCP servers for standalone use:

| Agent | MCP Port | Exposed Tools | Standalone Use Case |
|---|---|---|---|
| Incident Intake Agent | 8443 | `submit_incident`, `check_duplicate` | Users submit incidents via Claude Desktop/chat UI without going through full orchestration |
| Diagnostic Agent | 8443 | `diagnose_service`, `correlate_logs` | On-call engineers run diagnostics independently of any incident record |
| Knowledge Search Agent | 8443 | `search_knowledge`, `find_similar_incidents` | Engineers browse knowledge base for solutions outside incident context |
| Communication Agent | 8443 | `draft_communication`, `send_update` | Managers craft and send ad-hoc incident communications |

All four agents also implement A2A (port 8444) for orchestrated use. Dual interface.

---

## Tool Container Inventory

28 tool containers across 12 agents. All accessed via HTTP over localhost (sidecar pattern in production, shared services in dev).

| Tool Container | Used By | Port | Protocol |
|---|---|---|---|
| email-parser | Incident Intake | 9001 | HTTP/REST |
| slack-connector | Incident Intake | 9002 | HTTP/REST |
| form-normaliser | Incident Intake | 9003 | HTTP/REST |
| taxonomy-lookup | Classification | 9001 | HTTP/REST |
| historical-pattern-matcher | Classification | 9002 | HTTP/REST |
| impact-analyser | Priority Scorer | 9001 | HTTP/REST |
| service-dependency-mapper | Priority Scorer | 9002 | HTTP/REST |
| team-directory-connector | Routing | 9001 | HTTP/REST |
| skill-matrix-lookup | Routing | 9002 | HTTP/REST |
| log-aggregator-connector | Diagnostic | 9001 | HTTP/REST |
| metrics-query-tool | Diagnostic | 9002 | HTTP/REST |
| topology-walker | Diagnostic | 9003 | HTTP/REST |
| knowledge-base-connector | Knowledge Search | 9001 | HTTP/REST |
| embedding-search-tool | Knowledge Search | 9002 | HTTP/REST |
| script-executor | Automated Fix | 9001 | HTTP/REST |
| configuration-manager | Automated Fix | 9002 | HTTP/REST |
| rollback-handler | Automated Fix | 9003 | HTTP/REST |
| health-check-runner | Verification | 9001 | HTTP/REST |
| synthetic-monitor | Verification | 9002 | HTTP/REST |
| comparison-tool | Verification | 9003 | HTTP/REST |
| email-sender | Communication | 9001 | HTTP/REST |
| slack-poster | Communication | 9002 | HTTP/REST |
| sms-gateway | Communication | 9003 | HTTP/REST |
| clock-timer-service | SLA Monitor | 9001 | HTTP/REST |
| sla-rules-engine | SLA Monitor | 9002 | HTTP/REST |
| document-formatter | Resolution Documenter | 9001 | HTTP/REST |
| knowledge-base-writer | Resolution Documenter | 9002 | HTTP/REST |
| incident-history-connector | Problem Linker | 9001 | HTTP/REST |
| clustering-tool | Problem Linker | 9002 | HTTP/REST |

---

## Semantic Plane Business Rules (Dynamic — Never Hardcoded)

These rules live in the Strategic Business Context Agent and are queried at runtime:

| Rule Category | Example Rules | Queried By |
|---|---|---|
| **Priority Matrix** | Impact × Urgency → Priority (P1–P4) | Priority Scorer |
| **SLA Targets** | P1: 1hr response / 4hr resolve; P2: 4hr/24hr; P3: 8hr/72hr | SLA Monitor, I2R Orchestrator |
| **VIP Escalation** | Board members → auto-P1; C-suite → auto-P2 | Priority Scorer |
| **Auto-Fix Approval** | Service tier Gold + known-error → auto-fix allowed | Automated Fix Agent |
| **Routing Rules** | Network issues → Network Ops; Application → DevOps; Security → SecOps | Routing Agent |
| **Change Freeze** | No auto-fixes during quarterly release freeze | Automated Fix Agent |
| **Problem Threshold** | 3+ incidents with same root cause in 30 days → create problem | Problem Linker |
| **Communication Frequency** | P1: update every 30min; P2: every 2hr; P3: on resolution | Communication Agent |
| **Duplicate Threshold** | Embedding similarity > 0.92 within 24hrs → flag duplicate | Incident Intake |
| **Knowledge Freshness** | Articles > 180 days old flagged as potentially stale | Knowledge Search |
| **Diagnosis Confidence** | Minimum 0.75 confidence for automated diagnosis acceptance | Diagnostic Agent |
| **Classification Overrides** | Security-related keywords → force "Security Incident" classification | Classification Agent |
| **Business Hours** | UK: Mon–Fri 08:00–18:00 GMT; US: Mon–Fri 08:00–18:00 ET | SLA Monitor |

---

## Why This Application Is a Good Framework Validation Vehicle

**Exercises every mandatory requirement:**
- 12 tactical agents with clear single-purpose boundaries (sizing tests all pass)
- 3 sub-process orchestrators coordinating 2–4 tactical agents each (reusable across future processes)
- 1 primary orchestrator managing end-to-end business process with business decisions
- A2A protocol on all 16 components
- MCP protocol on 4 agents that have legitimate standalone use cases
- 28 tool containers as sidecars (no library embedding)
- AI Gateway for all LLM calls across all agents
- Semantic plane with 12+ dynamic business rule categories
- Dual audit trail (CAT for incident data containing user/system info; PST for performance metrics)
- OpenTelemetry on everything, reconfigurable exporters
- OAuth2/JWT at every boundary
- EU AI Act risk classification required (Automated Fix Agent is potentially high-risk due to autonomous infrastructure changes)

**Tests framework boundaries correctly:**
- Internal workflow complexity (Diagnostic Agent has 15+ steps) does NOT make it a strategic orchestrator — it's single-purpose, single-domain
- The Automated Fix Agent is the most interesting compliance test: it executes changes, so it needs human oversight governance from the semantic plane (auto-fix approval rules, change-freeze checks). This exercises the "business rules MUST be queried" requirement
- Communication Agent with MCP tests dual-interface pattern
- Resolution Workflow uses Saga pattern (rollback on verification failure)
- Problem Linker exercises pattern detection without becoming a "God agent"

**Completely independent of Delaware use cases.** No invoices, no P2P, no ERP posting. Any organisation with an IT environment can relate to this domain.

---

## Implementation Order (Aligned with Framework Adoption Roadmap)

**Phase 1 — Tactical Agent Factory (Weeks 1–8):**
Build Incident Intake Agent and Classification Agent. Prove the framework scaffolding works: A2A endpoints, AI Gateway routing, OTEL instrumentation, dual audit trail, tool sidecar containers. Manual orchestration (scripts).

**Phase 2 — Agent Library Expansion (Weeks 9–20):**
Build remaining 10 tactical agents. Deploy Triage Workflow Orchestrator. Validate sub-process orchestration pattern via A2A. Enable MCP on the 4 standalone-eligible agents.

**Phase 3 — Strategic Orchestration (Weeks 21–30):**
Deploy Resolution Workflow and Closure Workflow orchestrators. Deploy I2R Process Orchestrator. Full end-to-end Incident-to-Resolution flow working.

**Phase 4 — Dynamic Semantic Context (Weeks 31–38):**
Deploy Strategic Business Context Agent with all 12+ rule categories. Migrate hardcoded configs to semantic plane queries. Process Definition Service active.

**Phase 5 — Enterprise Maturity (Weeks 39+):**
Process mining on I2R metrics. A/B testing of routing strategies. Advanced Saga patterns. Multi-region deployment.