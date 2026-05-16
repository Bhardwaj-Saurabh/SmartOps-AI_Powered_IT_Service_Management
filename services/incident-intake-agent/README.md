# Incident Intake Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow, not autonomous agent)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose (single sentence)

Extract structured incident data from multi-channel inputs.

## 12-step workflow chain

Numbering matches [docs/PRD.md](../../docs/PRD.md) for this agent. Each step is a function in `src/incident_intake/workflow.py`.

1. Receive raw input
2. Detect channel + format
3. LLM extract entities (single LLM call via LiteLLM)
4. Normalise to canonical schema
5. Duplicate check (Qdrant vector similarity vs threshold from SBCA)
6. Short-circuit: link to duplicate, return early
7. Enrich reporter context (VIP, department)
8. Required-fields completeness check (rule from SBCA)
9. Short-circuit: emit `input-required` task with clarification questions
10. Assign incident_id
11. Emit structured record as A2A Task artifact
12. Dual audit trail (CAT + PST) — emitted from every step, not just here

Steps 5 and 8 are the two **decision gates** that read business rules from the Strategic Business Context Agent. Steps 6 and 9 are the two short-circuit exits.

## KPIs

Both business and technical KPIs are emitted as OTEL Metrics. Business KPIs are CAT-tagged, technical are PST-tagged. See `configs/agent.yaml` for the canonical list.

## Resilience

| Failure | Behaviour |
|---|---|
| Tool sidecar (steps 1–2, 7) | 3× exponential-backoff retry → `failed` with `di.failed_step` |
| LLM (step 3) | Same; LiteLLM circuit-breaks at provider level too |
| SBCA (steps 5, 8) | **Hard fail** — no fallback per §5 |
| Required fields missing (step 8) | `input-required` task, not a failure |
