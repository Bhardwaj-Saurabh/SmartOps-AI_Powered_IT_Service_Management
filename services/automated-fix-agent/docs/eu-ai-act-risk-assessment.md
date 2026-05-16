# EU AI Act Risk Assessment — Automated Fix Agent

## Classification

**HIGH-RISK AI System under Annex III of the EU AI Act.**

This is the project's first and only high-risk agent. All sibling agents (Incident Intake, Classification, Priority Scorer, Routing, Diagnostic, Knowledge Search, Verification) are classified as not-high-risk because they are read-only or advisory. The Automated Fix Agent actuates autonomous infrastructure changes, and so falls within Annex III §2 (critical-infrastructure operation) under the reading that internal IT services running production workloads are critical infrastructure for the operating organisation.

The assessment is conservative by design — if the agent's blast radius is small and the operating organisation does not consider its IT a critical service, the regulatory weight is lower. We classify high-risk regardless, because the controls listed below produce a better-engineered system whether the regulator considers it Annex III or not.

## Mandatory controls — implemented in this build

### 1. Human oversight (Art. 14)

Three explicit intervention points are wired through the SBCA semantic plane so they're auditable + tunable without code redeploy:

| Intervention | Where | Behaviour when no human is available |
|---|---|---|
| **Approval gate** | SBCA rule `automated_fix_approval` keyed on `(fix_type, service_tier)` | Default = `false`. Any fix type not in the matrix → emit A2A state `INPUT_REQUIRED` + `di.requires_human=true` + reason. The agent never invents approval for an unrecognised fix type. |
| **Scope cap** | SBCA rule `automated_fix_scope` (`max_blast_radius`, `max_affected_users`) | Above the cap → `INPUT_REQUIRED`. The Priority Scorer's blast radius is the input. |
| **Change-freeze** | SBCA rule `change_freeze.active` (shared with Priority Scorer) | When `true` → `INPUT_REQUIRED` regardless of approval, unless the incident is explicitly flagged emergency. |

A human reviewer can flip any of these rules in `configs/semantic-plane/automated-fix-rules.yaml` and hot-reload SBCA — no agent restart, no deployment. The audit trail records the rule value at decision time, so the human trail is reconstructable retrospectively.

### 2. Risk-management system (Art. 9) + transparency (Art. 13)

Every step of every executed runbook is logged to the Confidential Audit Trail (CAT) with:

- Configuration snapshot ID taken **before** the first mutation
- Runbook ID + parameters + the LLM rationale for picking that runbook
- Per-step outcome with timestamp + duration
- If any step failed: the rollback handler's invocation + result
- The summary LLM call's input/output (so the explanation surface is auditable)

This is the canonical Article 12 logging surface for this agent.

### 3. Robustness + accuracy (Art. 15)

- **Rollback is mandatory.** SBCA rule `rollback_required.default = true`. Snapshot-on-entry is unconditional; first-step failure triggers automatic restore via the rollback handler. There is no "we'll roll back later" path.
- **Saga compensation on verification failure.** The Resolution Workflow Orchestrator's saga config (`saga.compensations`) calls back into Automated Fix's `rollback` skill if the downstream Verification Agent reports `fix_verified=false`. This survives the Verification Agent itself crashing — the orchestrator's failure path catches it.
- **Failure-closed runbook validation.** Required parameters missing → fail closed (script-executor returns 400); no "make up a default value" path.

### 4. Accuracy + bias metrics (Art. 15 cont.)

| KPI | Tracked as | Surface |
|---|---|---|
| Fix success rate by `fix_type` | OTEL metric (PST) | Per-fix-type aggregation; alert if it drops below the rolling baseline |
| Rollback rate | OTEL metric (PST) | High rollback rate signals either runbook-quality regression or a real underlying change in the environment |
| Approval-denied rate | OTEL metric (PST) | Tracks how often SBCA gates the agent; sudden jumps indicate stale `automated_fix_approval` rules |
| Per-service-tier coverage | OTEL metric (PST) | Bias-like signal: are bronze-tier services excluded from automation in a way that disadvantages a particular department? |

Bias surfacing here is about **coverage fairness** (which services benefit from automation) rather than demographic bias, but the principle (transparent measurement of differential treatment) is the same.

### 5. Conformity assessment + post-market monitoring (Arts. 43, 72)

- Every deployment of this agent MUST update [docs/eu-ai-act/](../../docs/eu-ai-act/) (project-level) with the deployment date + the semantic-plane rule snapshot at deploy time.
- The smoketest (`scripts/smoketest.sh`) and the saga rollback integration test exercise the failure-and-rollback path; these are the project's automated post-market conformity checks.
- The Resolution Orchestrator's risk assessment now references this agent and is itself **reclassified high-risk** when this agent is in its chain. See `services/resolution-workflow-orchestrator/docs/eu-ai-act-risk-assessment.md`.

## Fundamental Rights Impact Assessment (FRIA — Art. 27)

### Affected persons

End users whose IT services are altered by an automated fix. The agent does not directly process personal data beyond the incident reporter's identity (already collected by Incident Intake) and the `affected_users` parameter (an email list passed only when applicable to the runbook, e.g. `okta-ca-resync`).

### Identified risks

| Risk | Likelihood (low/med/high) | Severity | Mitigation |
|---|---|---|---|
| **Service downtime caused by a bad runbook** | low | medium | Snapshot + automatic rollback; Verification Agent triggers Saga rollback on failure |
| **Unequal access to fast resolution** based on service tier | medium | low | `automated_fix_approval` matrix is published in the semantic plane; non-engineering stakeholders can review and challenge |
| **Privacy intrusion via runbook parameters** | low | low | Reporter identity and `affected_users` flow to CAT only (encrypted, RBAC+MFA, 7-year retention). PST receives only `fix_type` + step outcomes. |
| **Lack of redress when the fix is wrong** | medium | medium | Every fix is reversible by snapshot; every fix is logged with a reviewable rationale; Verification Agent provides an automatic objective re-check; orchestrator-initiated rollback provides a synchronous safety net |

### Stakeholders consulted

In a production deployment, FRIA consultation MUST include:

- Incident reporters (representative sample)
- Resolver-team leads (whose work the agent partially automates)
- Information security
- Legal / data protection officer

This template documents the structure for that consultation; the project itself is a reference implementation, so the consultation list above is the artefact requirement, not an attestation of consultation having happened.

### Residual risk acceptance

The residual risks listed above are accepted subject to the controls in §1–§5 of this document remaining live. Removing any control (e.g. setting `rollback_required.default = false`) requires re-running the FRIA and updating this document.

## Triggers for reassessment

- Any change to `automated_fix_approval` that broadens the matrix.
- Any change to the runbook catalogue (`tools/script-executor/data/runbooks.yaml`) that introduces a fix type touching authentication, identity, or financial-system components.
- Any change to the orchestrator's saga config that removes the rollback compensation.
- Any change that lets the agent run without prior diagnosis (currently the input shape requires `diagnosis`).

## Review cadence

Quarterly, AND before any release that changes any of the triggers above.

## Cross-references

- DI AI Framework spec §9 — EU AI Act compliance requirements
- `configs/semantic-plane/automated-fix-rules.yaml` — the rules cited above
- `services/resolution-workflow-orchestrator/docs/eu-ai-act-risk-assessment.md` — orchestrator-level high-risk classification
