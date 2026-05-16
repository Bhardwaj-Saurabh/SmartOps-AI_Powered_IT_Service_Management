# SmartOps — DI AI Framework Reference Implementation

End-to-end reference implementation of the **DI AI Framework** built around an IT Service Management domain. Driven by two binding documents:

- [docs/DI_AI_FRAMEWORK.md](docs/DI_AI_FRAMEWORK.md) — framework spec (MUST/SHOULD/COULD)
- [docs/PRD.md](docs/PRD.md) — the SmartOps application (12 tactical agents + 3 sub-process orchestrators + 1 primary orchestrator over Incident-to-Resolution)
- [docs/architecture.md](docs/architecture.md) — Incident Intake Agent (build #1) architecture + framework gap-closure

## What's built so far

**Stage 1 — shared libraries** (`libs/`):
`di_framework_core`, `config_loader`, `observability`, `a2a_server` (spec-native Google A2A — no wrappers), `a2a_client`, `gateway_client` (LiteLLM / OpenAI-shape), `semantic_client` (A2A wrapper to SBCA).

**Stage 2 — first runnable stack** (Incident Intake Agent #1):
- `services/incident-intake-agent/` — tactical agent #1, Anthropic prompt-chaining pattern, 12-step workflow
- `services/strategic-business-context-agent/` — SBCA stub (rule queries + capability registry)
- `tools/{email-parser,slack-connector,form-normaliser}/` — three sidecar tools
- `infra/` — Docker Compose, LiteLLM v1.83.10-stable (hardened per CVE-2026-42208), Keycloak with `smartops` realm, OTEL Collector with CAT/PST split, Qdrant, Redis
- `configs/semantic-plane/intake-rules.yaml` — versioned business rules
- `scripts/` — token fetch, Qdrant seed, A2A demo

**Stage 3a — Classification Agent + first service-layer agent**:
- `libs/oidc_client/` — shared OIDC client-credentials provider (reused by every agent)
- `libs/a2a_client/` — adds `A2AClient.from_capability(...)` for registry-based discovery
- `services/classification-agent/` — tactical agent #2, **Anthropic parallelization** (LLM + history matcher run concurrently)
- `services/triage-workflow-orchestrator/` — **first service-layer agent**; composes tactical agents purely through the Capability Registry, no hardcoded peer URLs
- `tools/taxonomy-lookup/` + `tools/historical-pattern-matcher/` — 2 new sidecars
- `configs/semantic-plane/classification-rules.yaml` — overrides, confidence weights, taxonomy version pin

**Stage 3b — full Triage flow (4 tactical agents chained)**:
- `services/priority-scorer/` — tactical agent #3, **Anthropic prompt-chaining** (10-step deterministic chain with one LLM impact/urgency call)
- `services/routing-agent/` — tactical agent #4, **Anthropic parallelization** (team-directory + skill-matrix queried concurrently)
- `tools/impact-analyser/` + `tools/service-dependency-mapper/` (synthetic CMDB topology) — Priority Scorer's sidecars
- `tools/team-directory-connector/` + `tools/skill-matrix-lookup/` (synthetic teams + competencies) — Routing Agent's sidecars
- `configs/semantic-plane/priority-rules.yaml` — Impact × Urgency matrix, VIP overrides, blast-radius thresholds, change-freeze
- `configs/semantic-plane/routing-rules.yaml` — per-area resolver-team rules, priority overrides, load-balancing cap, LLM weight
- Triage Orchestrator extended with `compose_inputs` so Priority + Routing get multi-source composite payloads. **Adding the next tactical agent (Diagnostic in Stage 4) is one more chain entry — no agent edits.**

**Stage 4a — Resolution begins: Diagnostic + Knowledge Search + second service-layer orchestrator**:
- `services/diagnostic-agent/` — tactical agent #5; **Anthropic evaluator-optimizer** (project's first true Anthropic-strict "agent" — LLM-driven iteration count: generator + evaluator loop terminates on SBCA confidence threshold or `max_iterations`)
- `services/knowledge-search-agent/` — tactical agent #6; Anthropic parallelization (vector + keyword in parallel, weighted merge, LLM re-rank with diagnosed root cause)
- `services/resolution-workflow-orchestrator/` — **second service-layer agent**; introduces `input.<key>` references in `compose_inputs` and a `saga:` config stub populated in Stage 4b
- `tools/log-aggregator-connector/` + `tools/metrics-query-tool/` + `tools/topology-walker/` — Diagnostic's sidecars (synthetic logs/metrics/topology)
- `tools/knowledge-base-connector/` + `tools/embedding-search-tool/` — Knowledge Search's sidecars
- `configs/semantic-plane/diagnostic-rules.yaml` — confidence thresholds + `known_issues` short-circuit table
- `configs/semantic-plane/knowledge-rules.yaml` — freshness max-days + vector/keyword weights
- `scripts/seed_qdrant.py` now seeds `knowledge_articles` too; `scripts/demo_resolve.sh` exercises the chain end-to-end

**Stage 4b — full Resolution flow with Saga rollback. First HIGH-RISK EU AI Act agent.**:
- `services/automated-fix-agent/` — tactical agent #7. **HIGH-RISK under Annex III.** Chain pattern with three SBCA-controlled fail-closed gates (`automated_fix_approval`, `automated_fix_scope`, `change_freeze`), unconditional snapshot before mutation, automatic rollback on first step failure. Exposes TWO A2A skills: `apply_automated_fix` (forward path) and `rollback` (called by Saga). Full EU AI Act artifact set in [services/automated-fix-agent/docs/eu-ai-act-risk-assessment.md](services/automated-fix-agent/docs/eu-ai-act-risk-assessment.md) including FRIA.
- `services/verification-agent/` — tactical agent #8. Anthropic parallelization (health-check + synthetic + comparison concurrent). Deterministic floor overrides LLM optimism: if no measurable improvement on any symptom metric, `fix_verified=false` regardless of what the LLM says.
- `tools/script-executor/` + `tools/configuration-manager/` + `tools/rollback-handler/` — Automated Fix sidecars. Synthetic runbook catalogue (`okta-ca-resync`, `vpn-mtu-fix`, `salesforce-sso-uri-update`, `printer-spooler-restart`, `wifi-firmware-downgrade`). The executor honours `SIMULATE_RUNBOOK_FAILURE_AT_STEP` for testing the rollback path.
- `tools/health-check-runner/` + `tools/synthetic-monitor/` + `tools/comparison-tool/` — Verification sidecars. Deterministic post-fix scenarios; one (`demo-unfixed`) is wired to always fail so you can exercise the Saga rollback path with real network traffic.
- `configs/semantic-plane/automated-fix-rules.yaml` — approval matrix, scope cap, change-freeze, rollback-required.
- `configs/semantic-plane/verification-rules.yaml` — improvement-required thresholds, min-to-emit-verified confidence, per-priority soak periods.
- Resolution Orchestrator extended:
  - `chain:` grows from 2 → 4 entries (Diagnostic → Knowledge Search → Automated Fix → Verify)
  - `saga.enabled: true`, two configured compensations (artifact-predicate trigger on `fix_verified=false` AND state trigger on Verification failure) that call back into Automated Fix's `rollback` skill
  - `_run_saga` now actually executes compensations (Stage 4a left it as a plan stub)
  - EU AI Act doc **reclassified high-risk** — the orchestrator inherits Automated Fix's regulatory weight because it's the authorising boundary
- `scripts/demo_full_i2r.sh` — chains Triage → Resolution end-to-end through both orchestrators.

**Stage 5a — Closure begins: Communication + SLA Monitor + third service-layer orchestrator**:
- `services/communication-agent/` — tactical agent #9. Anthropic prompt chaining; one LLM call per (audience, channel) cell. MCP enabled per PRD (`draft_communication`, `send_update`).
- `services/sla-monitor-agent/` — tactical agent #10. Deterministic math + one optional LLM narrative call. SBCA-driven targets (priority × tier), business-hours-aware elapsed via `clock-timer-service`, pause-state deduction via `sla-rules-engine`.
- `services/closure-workflow-orchestrator/` — **third service-layer agent**. Chains Communication + SLA. Adds richer reference syntax: `input.priority.service_tier` dot-walks into nested input fields, `<idx>.<artifact>.<field>` dot-walks into prior artifacts.
- `tools/email-sender/` + `tools/slack-poster/` + `tools/sms-gateway/` — Communication sidecars (synthetic dispatchers with `/sent` + `/posted` inspection endpoints).
- `tools/clock-timer-service/` + `tools/sla-rules-engine/` — SLA sidecars (business-hours-aware time math + pause-state computation).
- `configs/semantic-plane/communication-rules.yaml` — audience/channel/tone/length matrix by priority; escalation_audiences.
- `configs/semantic-plane/sla-rules.yaml` — per-(priority, tier) response/resolve targets; per-region business hours; pause conditions; breach-warning percentages.
- `scripts/demo_closure.sh` — submits a resolved-incident payload; outputs the dispatch + SLA snapshot artifacts.

## Quickstart

```bash
# 1. Copy and edit env. You need an Azure AI Foundry chat + embedding deployment.
cp infra/.env.local.example infra/.env.local
$EDITOR infra/.env.local

# 2. Boot the stack
docker compose --env-file infra/.env.local -f infra/docker-compose.yaml up -d

# 3. Wait for everything to be healthy (Keycloak takes ~30s for the first realm import)
docker compose -f infra/docker-compose.yaml ps

# 4. Seed Qdrant with synthetic historical incidents (so duplicate detection has something to match)
export GATEWAY_TOKEN="$(scripts/get_token.sh agent-incident-intake)"
python scripts/seed_qdrant.py

# 5. Submit a synthetic incident — choose either flow:
scripts/demo_submit_incident.sh   # straight to Incident Intake (agent #1 only)
scripts/demo_triage.sh            # via the Triage Orchestrator → chains all 4 tactical agents
scripts/demo_resolve.sh           # Stage 4a — Resolution Orchestrator (Diagnostic + Knowledge)
scripts/demo_full_i2r.sh          # Stage 4b — full I2R: Triage → Resolution (8 tactical agents)
scripts/demo_closure.sh           # Stage 5a — Closure Orchestrator (Communication + SLA Monitor)

# 6. Verify the whole stack with a single PASS/FAIL run:
scripts/smoketest.sh              # compose health + Keycloak + end-to-end triage
```

The triage demo is the proof of the composition story: the orchestrator never sees a peer URL — it asks the Capability Registry for whichever agent is registered under each capability name. Adding more agents to the chain in Stage 3b will be a one-line `chain:` edit in `services/triage-workflow-orchestrator/configs/agent.yaml`.

## Inspecting the dual audit trail

```bash
# Confidential (full content, encrypted-at-rest in real deployments):
docker compose -f infra/docker-compose.yaml exec otel-collector tail -f /var/log/cat/traces.jsonl

# Platform (anonymised, ops-focused):
docker compose -f infra/docker-compose.yaml exec otel-collector tail -f /var/log/pst/traces.jsonl
```

Every span is tagged `audit.type ∈ {confidential, platform}`. The OTEL Collector's routing processor sends them down separate pipelines.

## Running tests

```bash
uv sync
uv run pytest services/incident-intake-agent/tests
```

Tests run offline — they stub LiteLLM/SBCA/Qdrant via mocks. Covers:
- Happy path: full 12-step chain emits `state=new`
- Duplicate short-circuit: vector similarity ≥ SBCA threshold → `state=duplicate`
- Missing required fields → `state=needs_clarification` mapped to A2A `input-required`
- SBCA failure → `SemanticPlaneError` (no hardcoded fallback)
- DI envelope round-trips through `Message.metadata.di.*`
- Agent Card skills match config

## Compliance checks

Inside Claude Code, run `/compliance-check` to apply the framework §12.1 verdict formula (`FAIL_COUNT ≥ 1 OR WARN_COUNT ≥ 3 → FAIL`) against the working tree. The PostToolUse hook in `.claude/hooks/` already blocks forbidden imports and bad LiteLLM tags at edit time.

## What's next

- Stage 3: Triage Workflow Orchestrator + Classification Agent + Priority Scorer + Routing Agent
- Stage 4: Resolution Workflow Orchestrator + Diagnostic / Knowledge Search / Automated Fix / Verification agents
- Stage 5: Closure Workflow + Communication / SLA Monitor / Resolution Documenter / Problem Linker
- Stage 6: I2R Primary Orchestrator
- Stage 7: Full Strategic Business Context Agent (replaces the stub)

Each stage delivers standalone value per the framework's phased-adoption roadmap (§14).
