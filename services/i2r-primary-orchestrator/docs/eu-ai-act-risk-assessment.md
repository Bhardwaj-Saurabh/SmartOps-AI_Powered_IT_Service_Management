# EU AI Act Risk Assessment — I2R Primary Orchestrator

## Classification

**HIGH-RISK under Annex III** — inherits the high-risk classification of the chained Resolution Workflow Orchestrator (which itself inherits from the Automated Fix Agent). This orchestrator is the **outermost authorising boundary** of the autonomous actuation path.

## Why

The orchestrator's `chain:` includes `resolve_incident`, which is high-risk because it includes Automated Fix. Per the framework's propagation rule (documented in `services/resolution-workflow-orchestrator/docs/eu-ai-act-risk-assessment.md`), high-risk classification propagates upward to any strategic orchestrator that drives the actuator.

This is the canonical surface a regulator would point at when asking "who authorises the autonomous IT change?". The answer is: this orchestrator, gated by SBCA's `i2r_escalation_criteria`, `automated_fix_approval`, `change_freeze`, and the Resolution Orchestrator's Saga.

## Composite controls (inherited + this-level)

### Inherited

- All controls from the Automated Fix Agent's assessment (approval matrix, scope cap, change-freeze, unconditional snapshot, automatic rollback).
- All controls from the Resolution Orchestrator's assessment (Saga compensation on verification failure, two-trigger Saga model, audited compensation execution).

### Orchestrator-specific (new)

| Control | Where |
|---|---|
| **Early escalation surface** | `i2r_escalation_criteria` in SBCA. After Triage, the orchestrator inspects `priority`, `blast_radius`, and `reporter_department` to decide if a P1-style escalation notification should fire BEFORE Resolution begins. Auditable + tunable; humans can be warned ahead of an autonomous change attempt. |
| **Closure-on-failed-resolution policy** | `i2r_run_closure_on_failed_resolution` in SBCA. When Resolution ends in `failed` (e.g. Saga rollback fired), should Closure still run to notify the reporter + record SLA + draft an incident note? Default true. |
| **Single end-to-end correlation id** | The orchestrator's request-scoped `di.correlation_id` threads through every downstream A2A call, so the CAT audit trail can reconstruct the full incident lifecycle as one transaction. |
| **End-to-end KPI envelope** | MTTR, STP rate, escalation count emitted as OTEL metrics with `di.process = "i2r"` so a future Phase-2 process-mining surface gets clean inputs. |

## What a regulator would inspect

- This document.
- `configs/semantic-plane/i2r-rules.yaml` — every business-process-level decision the orchestrator makes, in plain YAML.
- CAT trace stream filtered by `di.process = "i2r"` — full end-to-end audit of each incident, including which gates fired and which were bypassed.
- The Automated Fix Agent's `docs/eu-ai-act-risk-assessment.md` for the FRIA.

## Triggers for reassessment

- Any new actuator added to any chained sub-process orchestrator's chain. Today only Automated Fix actuates.
- Any change to `i2r_run_closure_on_failed_resolution` that suppresses the failure-notification path.
- Any change that makes this orchestrator make autonomous policy decisions beyond the SBCA-supplied rules (e.g. an LLM-driven router on top of `chain:`).

## Review cadence

Quarterly; on every change to `i2r-rules.yaml` or the orchestrator's `chain:`; on every reassessment of the Resolution or Automated Fix risk classifications.
