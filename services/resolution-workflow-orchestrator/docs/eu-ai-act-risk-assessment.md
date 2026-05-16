# EU AI Act Risk Assessment — Resolution Workflow Orchestrator

## Classification (Stage 4b)

**HIGH-RISK under Annex III** as of Stage 4b. The Stage 4a not-high-risk classification is superseded.

## Why the reclassification

The orchestrator's `chain:` now includes the Automated Fix Agent — itself classified high-risk under Annex III §2 (critical-infrastructure operation for an organisation's internal IT). Because this orchestrator is the authorising boundary that decides when Automated Fix runs — and particularly because its Saga compensation can autonomously invoke `automated_fix.rollback` — the orchestrator inherits the regulatory weight.

This is the framework's intended behaviour: high-risk propagates upward to the strategic layer that drives the actuator.

## Composite controls

### Inherited from the chained agents

See `services/automated-fix-agent/docs/eu-ai-act-risk-assessment.md` for the full Annex III artifact set on the actuator. Highlights:

- SBCA-gated approval matrix + scope cap + change-freeze (Art. 14 human oversight)
- Unconditional snapshot before mutation + automatic rollback on failure (Art. 15 robustness)
- Full CAT audit of every step (Art. 12)

### Orchestrator-specific

| Control | Where |
|---|---|
| **Saga compensation on verification failure** | `configs/agent.yaml` `saga.compensations`. When Verification reports `fix_verified=false` OR Verification itself fails, the orchestrator AUTOMATICALLY calls `automated_fix.rollback` with the snapshot token. |
| **Two-trigger model** | Saga fires on both `on_step_failure` (state-based) AND `on_artifact_predicate` (content-based). Verification returns COMPLETED with a `fix_verified=false` artifact field; the content trigger catches it. Adding another trigger family is a config-only change. |
| **Audited compensation execution** | Every Saga firing is captured in CAT (`saga_planned`, `saga_executed`, `saga_failed`) and PST counters. The `saga_compensations` field in the orchestrator's response artifact preserves the same information for downstream auditors. |
| **No silent autonomous chaining** | The chain definition lives in `configs/agent.yaml` — a human review MUST approve any change adding another autonomous actuator. A "rollback fixed it but Verification still fails" loop is NOT implemented; the agent emits FAILED. |

## Fundamental Rights Impact Assessment (FRIA)

The FRIA documented for the Automated Fix Agent covers the actuation surface. The orchestrator does **not** add new identified risks; it adds **detection and remediation** (the Saga rollback path) to the existing list:

- The Automated Fix FRIA's "Service downtime caused by a bad runbook" risk is **further mitigated** by Saga — Verification's `fix_verified=false` triggers automatic restore without operator intervention.
- The Automated Fix FRIA's "Lack of redress when the fix is wrong" risk is **directly addressed** by the same path — the system self-redresses within the same `resolve_incident` call.

A separate FRIA consultation is not required in addition to Automated Fix's: same affected persons, same risk surface, additional mitigations only.

## Triggers for reassessment

- Any change to `saga.compensations` that REMOVES the Verification → rollback path.
- Any change to `chain:` that introduces a SECOND actuating agent (today only Automated Fix actuates).
- Any change that makes the orchestrator autonomously schedule the soak-period re-check — that crosses into time-shifted decision-making and the assessment must re-examine the human-oversight surface.
- Any change to the Automated Fix Agent's own risk classification.

## Review cadence

Quarterly; whenever `chain:` or `saga:` change materially; whenever the Automated Fix Agent's assessment is revised.

## Cross-references

- DI AI Framework spec §9 — EU AI Act compliance
- `services/automated-fix-agent/docs/eu-ai-act-risk-assessment.md` — full Annex III artifact for the chained actuator
- `services/verification-agent/docs/eu-ai-act-risk-assessment.md` — not-high-risk but safety-critical; its deterministic floor is part of this orchestrator's safety story
