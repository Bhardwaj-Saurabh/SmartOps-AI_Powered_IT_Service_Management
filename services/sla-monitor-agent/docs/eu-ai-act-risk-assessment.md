# EU AI Act Risk Assessment — SLA Monitor Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The SLA Monitor Agent computes a per-incident SLA snapshot: response/resolve time consumed against targets, paused-time deductions, and a flagged breach/warning state. The numbers are produced **deterministically** by tool sidecars + SBCA rules. The single optional LLM call writes a one-line narrative summarising the snapshot — purely descriptive, not decision-making.

SLA snapshots influence operational prioritisation only. They do not gate access to services, employment, education, justice, biometrics, or critical-infrastructure operation.

## Notable controls

| Control | Where |
|---|---|
| Targets in policy, not code | SBCA `sla_targets` keyed by (priority, customer_tier) |
| Business-hours handling | Per-region rules in SBCA `business_hours`; agent applies via `clock-timer-service` |
| Pause conditions auditable | SBCA `sla_pause_conditions` enumerates states that pause the clock |
| Narrative optional | `narrative.enabled = false` in `agent.yaml` skips the LLM call entirely for high-frequency polling |
| Deterministic floor | The pass/fail boolean fields (`response_breached`, `resolve_breached`, `*_warning`) are computed without the LLM and are the authoritative answer |

## Triggers for reassessment

- If the SLA Monitor ever drives auto-rejection of incidents past breach (it doesn't today).
- If targets ever become legally-binding customer commitments.

## Review cadence

Every framework-version bump; whenever `sla_targets` change.
