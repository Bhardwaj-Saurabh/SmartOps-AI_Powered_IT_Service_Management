# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Spec-only.** No source code, tests, or build scripts yet — just two design docs and an empty Python 3.12 / uv project. When asked to implement something, scaffold under the layout in §"Monorepo Layout" below rather than inventing one. Do not invent run/test commands that don't exist yet.

## What This Project Is

A reference implementation of the **DI AI Framework** built around an IT Service Management domain (**SmartOps**), end-to-end business process **Incident-to-Resolution (I2R)**. The system is decomposed into **12 tactical agents + 3 sub-process orchestrators + 1 primary orchestrator** with 28 tool sidecars, exercising every mandatory framework component.

Source-of-truth documents:

- [docs/DI_AI_FRAMEWORK.md](docs/DI_AI_FRAMEWORK.md) — **binding** framework specification (MUST/SHOULD/COULD). Read before designing any component.
- [docs/PRD.md](docs/PRD.md) — per-agent purpose, internal workflow steps, tool sidecars, ports, semantic-plane queries, A2A/MCP exposure, business-rule catalogue.

Framework spec wins on conflict; the PRD is the SmartOps instantiation of it.

## Locked Technology Decisions

| Concern | Choice | Notes |
|---|---|---|
| Agent runtime | **Microsoft Agent Framework** (`agent-framework`, Oct 2025) | Python only. Unifies Semantic Kernel + AutoGen. Use it for agent loops, workflows, tool calling. **Do not** also install `semantic-kernel` or `autogen` packages — they're subsumed. |
| Agent-to-agent protocol | **Google A2A protocol (spec-native)** | Implement against [a2aproject.github.io/A2A](https://a2aproject.github.io/A2A/specification) directly. **Do not** use third-party "A2A" wrappers. JSON-RPC 2.0 over HTTPS, Agent Card at `/.well-known/agent-card.json`, tasks lifecycle, SSE streaming, push-notification webhooks. Port **8444**. |
| Agent design patterns | **Anthropic "Building Effective Agents"** | Use the named patterns: augmented LLM, prompt chaining, routing, parallelization (sectioning/voting), orchestrator-workers, evaluator-optimizer. Pick the simplest pattern that works; don't add evaluator loops without a measured need. Match agents in [docs/PRD.md](docs/PRD.md) to one of these patterns in their docstring. |
| AI Gateway | **LiteLLM self-hosted, pinned to `ghcr.io/berriai/litellm:v1.83.10-stable`** | OpenAI-API-compatible proxy. Holds Azure AI Foundry creds. Agents talk OpenAI SDK shape, never Azure SDK directly. Port **4000**. **Never use `:latest` or any tag earlier than v1.83.7** — see "LiteLLM hardening" below. |
| LLM provider | **Azure AI Foundry** | Keys live in LiteLLM config (mounted from secret store / env). Agents never see Azure keys. Model aliases live in `infra/litellm/config.yaml` and are referenced by name from agent configs. |
| Vector DB | **Qdrant, local Docker** | Port **6333** (REST) / **6334** (gRPC). Used by `embedding-search-tool`, `historical-pattern-matcher`, `clustering-tool`. Collections + schema live in `infra/qdrant/collections.yaml`. |
| Config / semantic plane | **YAML in repo (Git-versioned)** | Strategic Business Context Agent loads `configs/semantic-plane/*.yaml` and serves rules via A2A. Versioned via Git PRs. |
| Identity | **Keycloak in Docker Compose (OIDC)** | Realm + clients + roles defined in `infra/keycloak/realm-export.json`. Agents verify JWTs via JWKS discovery. No shortcuts — dev tokens go through real OIDC. |
| Local runtime | **Docker Compose** | One service per agent, one per tool sidecar, plus Qdrant / LiteLLM / OTEL Collector / Keycloak / Redis. Matches "one agent per container" MUST from day one. |
| State store | **Redis** | Orchestrator workflow state. Port **6379**. |
| Observability | **OpenTelemetry Collector** | OTLP 4317 (gRPC) / 4318 (HTTP). Classification processor splits CAT vs PST pipelines. |
| Python | **3.12**, **uv** for env + lockfile | `pyproject.toml` already pins `>=3.12`. |

## Configuration-Driven Design (Hard Requirement)

**Every behavioural knob is configuration, not code.** Configs are loaded by agents at startup and live-reloadable where possible. Configs are served *through the gateway* (LiteLLM for model routing) or *through the semantic plane* (SBCA for business rules) so that changes don't require redeploying agents.

What goes in YAML, never in Python:

- Model selection per agent / per task (LiteLLM model alias, temperature, max tokens)
- Business rules: thresholds, priority matrix, SLA targets, VIP lists, routing rules, change-freeze calendar, auto-fix approvals, problem-creation thresholds, knowledge freshness, diagnosis confidence, business hours — see [docs/PRD.md](docs/PRD.md) "Semantic Plane Business Rules" table
- Agent capability advertisements (capability name, A2A endpoint, version)
- Tool sidecar endpoints and timeouts
- Prompt templates (versioned in `configs/prompts/<agent>/`)
- OTEL exporter routing (CAT vs PST classification rules)
- Workflow / BPMN sequences for orchestrators

What stays in Python:

- Workflow execution sequence skeletons (Extract → Validate → …)
- Retry / circuit-breaker mechanics
- Data shape transformations
- A2A protocol plumbing

Decision test: *"Would a non-engineer want to change this without a deploy?"* → YAML. Otherwise → code.

## Monorepo Layout (Canonical — Use This)

```
.
├── services/                       # One folder per agent or orchestrator. Each = one container.
│   ├── incident-intake-agent/
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/<package>/
│   │   │   ├── main.py             # FastAPI app, OTEL setup, A2A server mount
│   │   │   ├── agent.py            # Microsoft Agent Framework agent definition
│   │   │   ├── workflow.py         # Internal steps (1–N from PRD)
│   │   │   └── config.py           # Pydantic config schema, loads YAML
│   │   ├── configs/                # Agent-local config (non-business)
│   │   │   └── agent.yaml
│   │   ├── tests/
│   │   └── README.md               # Layer classification + EU AI Act risk level
│   ├── classification-agent/
│   ├── ...                         # 10 more tactical agents
│   ├── triage-workflow-orchestrator/
│   ├── resolution-workflow-orchestrator/
│   ├── closure-workflow-orchestrator/
│   ├── i2r-process-orchestrator/
│   └── strategic-business-context-agent/
├── tools/                          # One folder per tool sidecar (28 total). Each = one container.
│   ├── email-parser/
│   ├── taxonomy-lookup/
│   ├── ...
├── libs/                           # Shared Python packages. Installed via uv workspace.
│   ├── di_framework_core/          # Base types, error model, correlation IDs, JWT verifier
│   ├── a2a_server/                 # Google A2A spec-native server (Agent Card, JSON-RPC, SSE, tasks)
│   ├── a2a_client/                 # Spec-native A2A client (discovers via Agent Card)
│   ├── gateway_client/             # OpenAI-compatible client pointed at LiteLLM. Single import path for all LLM calls.
│   ├── semantic_client/            # A2A client to SBCA. `query_rule(domain, context)`.
│   ├── observability/              # OTEL setup, CAT/PST classification helpers, /health, /ready
│   └── config_loader/              # YAML + env, hot-reload, pydantic validation
├── configs/
│   ├── semantic-plane/             # Business rules YAML — read by SBCA, exposed via A2A
│   │   ├── priority-matrix.yaml
│   │   ├── sla-targets.yaml
│   │   ├── routing-rules.yaml
│   │   └── ...
│   ├── prompts/                    # Versioned prompt templates per agent
│   └── capabilities.yaml           # Capability registry seed
├── infra/
│   ├── docker-compose.yaml         # All services + sidecars + Qdrant + LiteLLM + OTEL + Keycloak + Redis
│   ├── docker-compose.dev.yaml     # Overrides for local dev
│   ├── litellm/config.yaml         # Model routing, Azure AI Foundry deployments
│   ├── qdrant/collections.yaml     # Collection schemas (vector size, distance, payload)
│   ├── otel/collector-config.yaml  # CAT/PST split, exporters
│   └── keycloak/realm-export.json  # Realm, clients, roles, scopes
├── docs/
│   ├── DI_AI_FRAMEWORK.md          # binding spec
│   ├── PRD.md                      # SmartOps application spec
│   └── eu-ai-act/                  # per-agent risk assessments
├── pyproject.toml                  # uv workspace root
└── .python-version
```

## Hard Rules (Refuse if Asked to Violate)

These are MUST-level — see [docs/DI_AI_FRAMEWORK.md](docs/DI_AI_FRAMEWORK.md) §11. One violation = compliance FAIL.

- **No direct LLM SDK imports** in `services/`. Forbidden: `openai`, `anthropic`, `azure-ai-inference`, `azure-openai`, `azure-ai-foundry`. Allowed: only `libs/gateway_client` (which talks to LiteLLM in OpenAI-compatible mode). Agents never see provider keys.
- **No hardcoded business rules.** Thresholds, routing, SLA, approvals, VIP lists, change-freeze, priority matrix → YAML under `configs/semantic-plane/`, queried via `libs/semantic_client`.
- **No third-party "A2A" wrappers.** Use `libs/a2a_server` / `libs/a2a_client` which implement the [Google A2A spec](https://a2aproject.github.io/A2A/specification) directly: Agent Card at `/.well-known/agent-card.json`, JSON-RPC 2.0 methods (`message/send`, `message/stream`, `tasks/get`, `tasks/cancel`), SSE for streaming, task lifecycle (`submitted`/`working`/`input-required`/`completed`/`canceled`/`failed`).
- **No tactical agent calling another tactical agent.** Only orchestrators route between them.
- **No tool library embedding.** Tools are sidecar containers on `localhost:9xxx`. Forbidden in services: `from <tool-name>_lib import ...`.
- **No mixed tech stacks per container.** Python only. One agent → one container → one `pyproject.toml`.
- **No shared in-process state.** Workflow state → Redis. No module-level mutable state.
- **A2A required on every tactical agent**, port 8444, even if it also runs standalone via MCP.
- **`/health` and `/ready` required.** `/ready` MUST probe AI Gateway and SBCA connectivity.
- **Dual audit trail required.** Every span/log classified as `audit.type = "confidential"` (CAT) or `"platform"` (PST). PII never in PST.
- **No provider API keys in code or env-at-rest.** Secrets injected only into LiteLLM and Keycloak containers via Compose `env_file` pointing at gitignored `.env.local`.

## Anti-Pattern Detection (Local Hook)

A pre-commit hook in `.claude/hooks/check-forbidden-patterns.sh` blocks the above forbidden imports and obvious hardcoded thresholds in `services/` Python files. If it fires, **fix the violation** — do not suppress the hook.

## LiteLLM Hardening (Mandatory — Read Before Touching `infra/litellm/`)

LiteLLM had a chain of severe CVEs in 2026, including pre-auth SQL injection (**CVE-2026-42208**, CVSS 9.3, on CISA KEV list — actively exploited within 36 hours of disclosure), MCP stdio command injection (CVE-2026-30623), auth-bypass config RCE (CVE-2026-35029), and sandbox escape in custom-code guardrails (CVE-2026-40217). When configuring LiteLLM in this repo:

- **Image pin is non-negotiable:** `ghcr.io/berriai/litellm:v1.83.10-stable` (or a later stable in the v1.83.7+ patched line, after security review). **Never** `:latest`, `:main`, or any tag from v1.81.16 – v1.83.6. The hook flags this; do not bypass it.
- **No internet exposure.** LiteLLM listens only on the internal Compose network. Do not publish port 4000 to the host in `infra/docker-compose.yaml` (no `4000:4000`). Agents reach it as `http://litellm:4000`.
- **Disable risky endpoints in `infra/litellm/config.yaml`:**
  - `/prompts/test` — SSTI vector (GHSA-xqmj-j6mv-4862)
  - `/guardrails/test_custom_code` — sandbox escape, runs as root in the default image (GHSA-wxxx-gvqv-xp7p)
  - MCP stdio test endpoints — CVE-2026-30623
  - Mutating `/config/*` admin endpoints — privilege escalation (GHSA-jjhc-v7c2-5hh6)
  - Set `general_settings.disable_spend_logs_writes`, `general_settings.allowed_routes: [...]` to an explicit allow-list, and `general_settings.ui_access_mode: "admin_only"`.
- **Override the container to run as non-root.** Default image runs as root, which is what made the guardrail sandbox escape RCE. Add `user: "65532:65532"` and a `read_only: true` filesystem with tmpfs for `/tmp` in Compose.
- **Master key + DB key rotation.** `LITELLM_MASTER_KEY` and `DATABASE_URL` come from gitignored `.env.local` only. Rotate the master key after any container restart in dev. Never commit virtual keys.
- **Don't enable the Postgres-backed virtual-key store unless you need it.** The SQL injection lived on that auth path. If you do enable it, ensure the Postgres user has only the privileges LiteLLM needs (no `SUPERUSER`, no `CREATE` on other schemas).
- **Pin via digest after first pull.** Once you've verified `v1.83.10-stable`, record the `sha256:...` digest in `infra/litellm/config.yaml` (comment) and switch the Compose image reference to the digest form for reproducibility.
- **Re-check on every bump.** Before changing the pin, read the LiteLLM security blog and GitHub Security tab for new advisories. Document the new tag + the read-of advisories in the PR description.

## Agent Implementation Template (Microsoft Agent Framework + A2A)

Each tactical agent in `services/<agent>/` is structured as:

1. **`agent.py`** — defines the agent using `agent_framework` (e.g. `ChatAgent` with tools wired to sidecars via `libs/gateway_client`). Match one Anthropic pattern (chain / route / parallel / orchestrator-workers / evaluator-optimizer) and state which in a one-line module docstring.
2. **`workflow.py`** — the numbered internal steps from the agent's section in [docs/PRD.md](docs/PRD.md). Each step is a function; the agent loop or workflow primitive sequences them.
3. **`main.py`** — FastAPI app that:
   - Mounts `libs/a2a_server` (Agent Card + JSON-RPC handlers + SSE)
   - Sets up OTEL with CAT/PST classification
   - Exposes `/health`, `/ready`
   - Registers capability with SBCA on startup
4. **`config.py`** — pydantic schema for `configs/agent.yaml`; loaded once, hot-reloaded for selected sections (prompts, thresholds).

The agent's **Agent Card** (served at `/.well-known/agent-card.json`) advertises: `name`, `description`, `version`, `url`, `capabilities` (streaming, push notifications), `defaultInputModes`, `defaultOutputModes`, `skills[]` (each matching one PRD capability), `securitySchemes` (OIDC via Keycloak).

## When to Add an Anthropic Pattern

- **Augmented LLM** — default for any agent that calls a tool. Don't over-pattern.
- **Prompt chaining** — Incident Intake (extract → normalise → enrich → validate).
- **Routing** — I2R Primary Orchestrator (auto-resolve vs investigate; escalate vs not).
- **Parallelization** — Diagnostic Agent (parallel log + metric + topology queries before correlation); Knowledge Search (vector + keyword in parallel).
- **Orchestrator-workers** — the three sub-process orchestrators *are* this pattern.
- **Evaluator-optimizer** — Diagnostic Agent's iterative refinement loop (max 3 iterations or confidence ≥ threshold from semantic plane). Don't add elsewhere unless you can name the quality signal being evaluated.

## Common Commands (Will Exist After Scaffold)

Not implemented yet — when implementing, target these exact entry points:

| Task | Command |
|---|---|
| Boot full stack | `docker compose -f infra/docker-compose.yaml up -d` |
| Boot infra only (no agents) | `docker compose -f infra/docker-compose.yaml --profile infra up -d` |
| Tail one agent's logs | `docker compose -f infra/docker-compose.yaml logs -f incident-intake-agent` |
| Run agent tests | `cd services/<agent> && uv run pytest` |
| Run all tests | `uv run pytest` (workspace root) |
| Lint / type-check | `uv run ruff check . && uv run mypy libs services` |
| Reload semantic-plane rules | edit YAML, then `curl -X POST http://sbca:8444/admin/reload` |

If the user asks to run any of these before the corresponding code exists, say so and offer to scaffold it.

## Slash Commands & Subagents

The `.claude/` folder ships these:

- `/new-tactical-agent <agent-name>` — scaffolds a new agent under `services/<name>/` per the template above, with A2A server, gateway client, OTEL, health checks, and an empty workflow keyed off its PRD section.
- `/compliance-check` — runs the framework MUST checklist against staged or recent changes.
- Subagent `framework-compliance-reviewer` — invoked for reviewing diffs against the §11 anti-pattern table.
- Subagent `a2a-spec-validator` — verifies an A2A implementation matches the Google spec (Agent Card schema, JSON-RPC methods, task lifecycle).

## Terminology

| Use | Never use |
|---|---|
| DI AI Framework | ConnectedBrain |
| Strategic Dynamic Orchestrator Agent | Maestro |
| Strategic Business Context Agent | Wernicke, EA Knowledge Agent |
