# EU AI Act Risk Assessment — Priority Scorer

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Priority Scorer assigns a P-level (P1–P4) to an incident based on three inputs:

1. An LLM call that estimates `impact` and `urgency` categories from the incident narrative.
2. Synthetic CMDB lookups (downstream blast radius, service tier) via the service-dependency-mapper sidecar.
3. A deterministic Impact × Urgency matrix served by the SBCA.

The agent does not take any autonomous action; its output influences resolver-team routing and SLA bookkeeping. P-level decisions affect **internal SLA tracking**, not access to essential public/private services for the reporter — they are operational prioritisation within an IT service-management workflow. They therefore fall outside Annex III §5 (access to services).

A VIP-department override (P1 forced for executive reporters) is a *policy* served from the semantic plane, not a learned model behaviour, and is auditable by a non-engineer.

## Obligations satisfied

| Obligation | How |
|---|---|
| Transparency (Art. 50) | Agent Card identifies this as an AI service; `di.confidence` on the response |
| Logging (Art. 12 / 19) | Full LLM prompt + response + matrix lookup + override decision logged to CAT |
| Explainability         | `decision_chain` artifact records every step: impact estimate, blast radius, urgency floor, matrix cell consulted, override applied (if any) |
| Human override         | Resolver teams (downstream) can always re-prioritise; this agent never holds the final say |

## Triggers for reassessment

- If a future change uses Priority Scorer output as the sole input to denying SLA coverage, **reassess** — that could move it into Annex III §5 territory.
- If the impact-narrative LLM step is replaced with a learned model trained on historical staff time-to-resolve, the agent gains bias risk and must be reassessed for Annex III §4 (employment/work-related).

## Review cadence

At every framework-version bump and whenever the `priority_matrix` or `vip_priority_overrides` rules in the semantic plane change.
