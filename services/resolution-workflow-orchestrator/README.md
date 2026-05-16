# Resolution Workflow Orchestrator

**Layer:** Strategic Sub-Process Orchestrator (DI AI Framework §2.1)
**Anthropic pattern:** prompt chaining (deterministic sequence at this level)
**EU AI Act:** Not high-risk in Stage 4a (read-only). **Reassessment required at Stage 4b** when Automated Fix joins the chain. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose

Chain tactical agents that resolve an already-triaged incident.

- Stage 4a: Diagnostic → Knowledge Search (read-only)
- Stage 4b: + Automated Fix → Verification, with Saga compensation on failure

## How it works

Caller submits a triaged incident payload (output of the Triage Orchestrator: `{incident, classification, priority}`). Each chain step is resolved by name via `A2AClient.from_capability()` — no hardcoded peer URLs.

For multi-source steps, `compose_inputs` supports two reference kinds:

- `input.<key>` — pull from the caller's original payload (e.g. `input.incident`)
- `<step_idx>.<artifact_name>` — pull from a prior step's artifact (e.g. `0.diagnosis`)

## Composability promise (still holds)

The same Diagnostic + Knowledge Search agents can be reused by a sibling
orchestrator for a different process (e.g. proactive maintenance) by writing
a new `chain:` block with a different `process` value in the A2A envelope.
No agent code changes.
