# Automated Fix Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow) with explicit SBCA-controlled approval gates
**EU AI Act:** **HIGH-RISK under Annex III.** Full assessment + FRIA + controls in [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP:** disabled (auto-fix only runs inside an orchestrated safety context — this is itself an Annex III control).

## Purpose (single sentence)

Execute pre-approved automated remediation runbooks.

## Two A2A capabilities

| Capability | Purpose |
|---|---|
| `apply_automated_fix` | Full forward path — runbook select → snapshot → execute → rollback-on-error → summarise |
| `rollback`            | Restore a prior snapshot. Called by the Resolution Orchestrator's saga when Verification reports `fix_verified=false` |

## Workflow (12 steps, all on the apply_automated_fix path)

1. Receive composite input `{incident, classification, priority, diagnosis, knowledge_articles}`
2. SBCA `automated_fix_approval[fix_type][service_tier]` — if false → emit `INPUT_REQUIRED`
3. SBCA `automated_fix_scope` — blast radius + affected users above cap → `INPUT_REQUIRED`
4. SBCA `change_freeze.active` — if true → `INPUT_REQUIRED`
5. Fetch runbook catalogue from script-executor
6. LLM picks runbook + parameters (must be from catalogue; null is allowed → escalate)
7. Validate parameters against runbook schema
8. configuration-manager snapshot **before** any mutation (snapshot_id is the rollback handle)
9. script-executor `/execute` runs the runbook
10. On step failure → automatic rollback via rollback-handler; emit `state=failed` with `failed_step`
11. LLM summary of what changed
12. Emit `fix_result` artifact with `rollback_token` (= snapshot_id) so the orchestrator can call back on verification failure

## Safety story (summary; full in EU AI Act doc)

| Control | Where |
|---|---|
| Fail-closed approval gate | step 2 — default false in SBCA |
| Scope cap | step 3 — `max_blast_radius`, `max_affected_users` |
| Change-freeze override | step 4 — shared rule with Priority Scorer |
| Mandatory snapshot | step 8 — unconditional, before any mutation |
| Mid-execution rollback | step 10 — automatic on first step failure |
| Orchestrator-initiated rollback | `rollback` skill — called by Saga when Verification fails |
| Full CAT audit | every step — full prompts, responses, parameters, step log, rollback invocations |
