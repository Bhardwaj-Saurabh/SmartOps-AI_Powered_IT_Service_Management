# Verification Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** parallelization (health-check + synthetic + metric comparison run concurrently)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP:** disabled.

## Purpose (single sentence)

Verify that an applied fix resolved the reported symptoms.

## Workflow

1. Receive composite input `{incident, classification, fix_result}`
2. SBCA `verification_thresholds` + `verification_confidence` + `verification_soak_period`
3. **Parallel** — three concurrent evidence collectors:
   - health-check-runner (post-fix probes)
   - synthetic-monitor (replay the failing scenarios with `after_fix=true`)
   - metrics-query-tool (post-fix snapshot) + comparison-tool (pre/post deltas)
4. Determine the **deterministic floor**: if comparison-tool says no metric improved, `fix_verified=false` regardless of LLM
5. LLM evaluator scores the evidence pack
6. If LLM `confidence < min_to_emit_verified` and deterministic floor is also weak → `fix_verified=false`
7. Annotate `soak_period_minutes` for downstream (Phase 1 doesn't schedule the re-check)
8. Emit `verification` artifact with `fix_verified`, `confidence`, `residual_concerns`, full evidence pack

## How rollback gets triggered

This agent **does not** trigger rollback directly. It returns `fix_verified=false` and the Resolution Workflow Orchestrator's saga compensation calls Automated Fix's `rollback` skill.
