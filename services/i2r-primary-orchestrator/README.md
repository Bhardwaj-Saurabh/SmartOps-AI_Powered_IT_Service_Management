# I2R Primary Orchestrator

**Layer:** Primary Strategic Orchestrator (DI AI Framework §2.1) — the project's only one.
**Anthropic pattern:** prompt chaining (3-stage composition of sub-process orchestrators)
**EU AI Act:** **HIGH-RISK** (inherited via the chained Resolution Orchestrator's Automated Fix). See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose

Drive the end-to-end **Incident-to-Resolution** business process. One A2A call in, a fully triaged + resolved + closed incident out (or a clean partial state if a sub-process short-circuited).

```
caller (UI / API / monitor)
  │  handle_incident
  ▼
i2r-primary-orchestrator
  ├─▶ triage_incident      → triage_summary  { incident, classification, priority, routing }
  ├─▶ (SBCA-gated: maybe escalate via Communication directly)
  ├─▶ resolve_incident     → resolution_summary { diagnosis, knowledge, fix_result, verification }
  └─▶ close_incident       → closure_summary { communications_sent, sla_status, documentation, problem_link }
```

## What's new about this orchestrator

This is the first place that explicitly handles **end-to-end business-process decisions** rather than just gluing tactical agents:

- **Triage short-circuit:** if Triage returns `INPUT_REQUIRED` (e.g. Intake needs clarification from the reporter), the orchestrator stops cleanly with a partial result instead of failing forward.
- **Resolution failure handling:** SBCA rule `i2r_run_closure_on_failed_resolution` decides whether to still run Closure when Resolution ended in `failed` (default: yes — so the reporter is notified the fix attempt didn't work and the SLA snapshot is still recorded).
- **Early escalation:** SBCA rule `i2r_escalation_criteria` lets the orchestrator fire an early escalation notification (via Communication) after Triage but before Resolution, when priority / blast radius / VIP signals are strong.
- **One correlation id** threads through every downstream call so the CAT audit can reconstruct the full incident lifecycle.

## Composability promise

This is the canonical example of the framework's compounding-value claim. A second primary orchestrator for a different business process (e.g. Change Management) reuses **the same 12 tactical agents and the same 3 sub-process orchestrators** by writing its own `chain:` block with a different `process` value in the A2A envelope. No agent changes.

## State machine (Phase 1)

The orchestrator emits one Task with the full chain outcome. Phase 2 will externalise the state machine to Redis so partial states (triaged-but-not-resolved) can be picked up by a different orchestrator instance on retry.

Current states (in the response artifact's `i2r_state` field):
- `submitted`            — request accepted
- `triaged`              — Triage chain completed
- `triage_needs_input`   — Triage short-circuited on INPUT_REQUIRED
- `resolving`            — Resolution chain in flight
- `resolution_failed`    — Resolution ended in failed; Closure may still have run per SBCA
- `resolution_completed` — Resolution finished, Closure pending
- `closed`               — full lifecycle complete
- `failed`               — uncategorised orchestrator failure
