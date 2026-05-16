# Closure Workflow Orchestrator

**Layer:** Strategic Sub-Process Orchestrator (DI AI Framework §2.1)
**Anthropic pattern:** prompt chaining (deterministic sequence)
**EU AI Act:** Not high-risk. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose

Post-resolution housekeeping:
- Stage 5a: notify stakeholders (Communication) + record SLA snapshot (SLA Monitor)
- Stage 5b: + write resolution notes / update KB (Resolution Documenter) + link to problem catalogue (Problem Linker)

## Composability promise (still holds)

The chain is config-only. Reordering, adding, or removing entries is a YAML edit. A different orchestrator for a different process (e.g. "post-change-review") could reuse the same agents with a different `process` value in the A2A envelope.

## Reference syntax for compose_inputs

In addition to the Resolution Orchestrator's two reference kinds, this orchestrator's runner supports **dot-walks into nested input fields**:

- `input.priority.service_tier` — pulls `initial_payload["priority"]["service_tier"]`
- `1.sla_status.targets.resolve` — pulls a nested field from a prior step's artifact

This is essential for closure, where the caller passes a deeply structured triaged+resolved incident and downstream agents need specific sub-fields (priority string vs the whole priority object, etc.).
