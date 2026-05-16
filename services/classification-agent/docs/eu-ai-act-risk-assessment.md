# EU AI Act Risk Assessment — Classification Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Classification Agent assigns a `service_area` and `category` label to an already-structured incident produced by the Incident Intake Agent. It is upstream of resolver-group routing and resolution, but is itself read-only: it does not actuate changes, does not affect access to essential services, and does not make employment, education, justice, or biometric decisions.

The agent uses two parallel inputs to form its decision:

1. An LLM call (via the AI Gateway) that picks the best label given the incident summary and the runtime taxonomy.
2. A nearest-neighbour query against historical incidents (via Qdrant) that returns past classifications for similar incidents.

The two are combined using SBCA-supplied weights (`classification_confidence.llm_weight`, `history_weight`). A keyword-driven **override list** (e.g. security keywords) lives in the semantic plane and forces specific labels regardless of LLM confidence — this is a *compliance ratchet*, not an automated harm vector.

## Obligations satisfied

| Obligation | How |
|---|---|
| Transparency (Art. 50)              | Agent Card identifies this as an AI system; `di.confidence` is part of every response |
| Logging (Art. 12 / 19)               | Full prompt + response + history matches + weighted decision chain logged to CAT |
| Explainability                       | `weighted_decision_chain` artifact returns LLM label, history label, weights applied, and the final pick |
| Data governance                      | PII redacted from PST events; only the symptoms-summary and labels flow to PST |
| Human override path                  | A reclassify endpoint will be added in Stage 4 when triaged incidents reach human resolvers |

## Triggers for reassessment

- If a future change uses classification output as a **sole** input to a high-impact decision (e.g. auto-rejecting incidents from specific groups, denying SLA coverage), this MUST be re-evaluated.
- If the label set ever expands to cover topics in EU AI Act Annex III (employment, education, etc.), this MUST be re-evaluated.

## Cross-references

- DI AI Framework §9 — EU AI Act compliance requirements.
- Sibling: Automated Fix Agent remains the project's likely high-risk candidate; Classification feeds it indirectly but does not actuate any infrastructure change.

## Review cadence

At every framework-version bump, and immediately on any change that adds a label category outside the existing five service areas.
