---
description: Scaffold a new tactical agent under services/<name>/ following the DI AI Framework canonical layout, wired for Microsoft Agent Framework + spec-native A2A + LiteLLM gateway.
argument-hint: <agent-name> [--pattern chain|route|parallel|orchestrator-workers|evaluator-optimizer]
allowed-tools: Read, Write, Edit, Bash, Glob
---

The user wants to scaffold the tactical agent **$1**. Optional pattern hint: **$2 $3** (one of Anthropic "Building Effective Agents" patterns).

## Procedure

1. **Resolve the agent's PRD section.** Open [docs/PRD.md](docs/PRD.md), find the section for `$1` (match by kebab-case → "Title Case Agent"). Extract: purpose (one sentence), numbered internal workflow, tool sidecars + ports, semantic-plane queries, A2A capability name, whether MCP is exposed.

   If `$1` does not match any agent in the PRD, **stop** and tell the user — do not invent an agent.

2. **Confirm the Anthropic pattern.** If the user didn't pass `--pattern`, infer the most likely one from the PRD workflow:
   - linear N-step workflow → `chain`
   - parallel tool calls before correlation (e.g. Diagnostic) → `parallel`
   - branching decisions (e.g. orchestrators) → `route` or `orchestrator-workers`
   - iterative refinement with a confidence/quality signal → `evaluator-optimizer`
   - none of the above → `augmented-llm` (default)

   State the pattern choice in one sentence and proceed.

3. **Create the directory structure** at `services/$1/`:

   ```
   services/$1/
   ├── README.md                # Layer = tactical. EU AI Act risk level. One-sentence purpose. Pattern. Business + technical KPIs list.
   ├── pyproject.toml           # Depends on libs/* via uv workspace. NEVER include openai/anthropic/azure-ai-* / semantic-kernel / autogen / third-party a2a libs.
   ├── Dockerfile               # python:3.12-slim, non-root, HEALTHCHECK
   ├── configs/
   │   └── agent.yaml           # model alias (LiteLLM), prompts ref, tool endpoints, A2A skills, KPI list, audit field classification
   ├── docs/
   │   └── eu-ai-act-risk-assessment.md  # NOT a placeholder. First-draft Annex III classification with reasoning. High-risk extras only if Annex III matches.
   ├── src/<snake_case_name>/
   │   ├── __init__.py
   │   ├── main.py              # FastAPI: mounts libs.a2a_server.AgentApp, OTEL setup, /health /ready, capability_registry/register on startup + …/deregister on shutdown
   │   ├── agent.py             # agent_framework definition. Module docstring states "Anthropic pattern: <name> (workflow|agent)" per architecture.md.
   │   ├── workflow.py          # One function per numbered PRD step. Each function returns its result + emits OTEL span with audit.type + di.correlation_id.
   │   ├── tools.py             # HTTP clients for sidecar tools (httpx). MUST propagate X-Correlation-Id and traceparent on every call. NEVER library embedding.
   │   └── config.py            # pydantic schema for agent.yaml, loaded via libs.config_loader
   └── tests/
       ├── test_agent_card.py   # Asserts /.well-known/agent-card.json validates against schema; skills[] ids match capability advertisements
       ├── test_envelope.py     # Asserts DI envelope (di.capability, di.correlation_id, di.process, di.step) round-trips; requires_human maps to input-required
       ├── test_workflow.py     # Per-step unit tests with mocked gateway/semantic/tool clients. Includes SBCA-failure-no-fallback test.
       └── test_a2a.py          # JSON-RPC message/send round-trip
   ```

4. **Use the libs/ packages — never duplicate their logic.** The scaffolded code MUST import:
   - `libs.a2a_server` for the A2A surface (Agent Card builder + JSON-RPC + SSE + JWT middleware)
   - `libs.gateway_client` for any LLM call (it points at LiteLLM)
   - `libs.semantic_client` for any business rule lookup
   - `libs.observability` for OTEL + CAT/PST classification + `/health` `/ready`
   - `libs.config_loader` for YAML loading + hot reload
   - `agent_framework` (Microsoft) for the agent loop / tool calling

   If any of these `libs/` packages don't exist yet, **list them as TODO scaffolds** and ask the user whether to also scaffold the missing lib(s) in this same task.

5. **Author `agent.yaml`** with these keys (values from the PRD section):
   ```yaml
   name: $1
   version: 0.1.0
   pattern: <chain|route|parallel|orchestrator-workers|evaluator-optimizer|augmented-llm>
   pattern_kind: workflow | agent     # Anthropic terminology — chain/route/parallel/orchestrator-workers = workflow; evaluator-optimizer with model-chosen iterations = agent
   model:
     alias: <litellm-alias-from-infra/litellm/config.yaml>
     temperature: 0.2
     max_tokens: 2048
   oidc:
     client_id: agent-$1
     audience: agent-$1            # gateway and peers verify aud == client_id
   a2a:
     port: 8444
     skills:
       - id: <capability-id-from-PRD>
         name: <human name>
         description: <from PRD>
   capability_registry:
     register_on_startup: true     # calls SBCA-stub capability_registry/register
     deregister_on_shutdown: true
   tools:
     # one entry per sidecar with hostname:port from PRD. Client MUST propagate X-Correlation-Id + traceparent.
   semantic_queries:
     # exact query keys this agent will ask SBCA. NO hardcoded fallbacks if SBCA fails — task fails.
   audit:
     cat_fields: [reporter, full_content, decision_chain]
     pst_fields: [duration_ms, token_count, error_class]
   kpis:
     business: [<list specific to this agent's domain>]
     technical: [a2a_latency_ms, tokens_used, sidecar_latency_ms, error_rate, step_failure_count]
   resilience:
     tool_retry: {attempts: 3, backoff: exponential}
     llm_retry: {attempts: 3, backoff: exponential}
     sbca_failure: hard_fail        # NEVER fallback to hardcoded thresholds — §5 violation
   ```

6. **Write `agent.py`** as a thin wrapper around `agent_framework` using the chosen pattern. Module docstring first line MUST state the pattern verbatim — the compliance reviewer checks for this.

7. **Write `workflow.py`** with one stub function per numbered PRD step. Each function has a docstring quoting the PRD step text. **Do not implement business logic yet** — leave `raise NotImplementedError("PRD step N")` so the user can fill in. This is intentional: implementation belongs in a follow-up turn.

8. **Append a Compose service entry** to `infra/docker-compose.yaml` (create if missing) with: build context, env_file referencing `.env.local`, ports `8444:8444` mapped on a unique host port, mem/cpu limits, healthcheck on `/health`, depends_on `litellm`, `keycloak`, `otel-collector`, `redis`. If the YAML doesn't exist yet, only write the service block and tell the user.

9. **Update `configs/capabilities.yaml`** (or create it) with the new agent's capability advertisement.

10. **Do NOT install dependencies, do NOT run the agent.** Stop after writing files. Print a summary:
    - Path created
    - Pattern chosen + why
    - PRD steps stubbed (count)
    - Any libs/ packages still missing
    - Exact follow-up commands the user can run (`uv sync`, `docker compose build $1`)

## Hard constraints (the hook will block these — don't even try)

- No `import openai`, `import anthropic`, `import azure.ai.*` in any generated file.
- No `import semantic_kernel`, `import autogen`.
- No `import a2a_sdk`, `import fasta2a`, etc.
- No hardcoded numeric business thresholds. If a step needs one, generate a `await semantic.query_rule(...)` call with a TODO comment naming the YAML key under `configs/semantic-plane/`.
- No `try/except` around `semantic_client.query_rule(...)` that returns a hardcoded fallback. SBCA failure = task `failed`. Re-raise.
- No tool library embedding — generate `httpx.AsyncClient` calls to `http://<tool>:<port>/...` that explicitly forward `X-Correlation-Id` and `traceparent` headers.
- No DI fields outside `Message.metadata.di.*` / `Task.metadata.di.*`. Don't invent new A2A task states — `requires_human` is `input-required` + `di.requires_human: true`.

## Refuse

If the agent name is not in [docs/PRD.md](docs/PRD.md), refuse and tell the user which agents are valid. Do not invent new tactical agents — those are framework-level decisions.
