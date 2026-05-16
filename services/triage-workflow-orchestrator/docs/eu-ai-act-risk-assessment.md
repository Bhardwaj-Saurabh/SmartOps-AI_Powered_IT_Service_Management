# EU AI Act Risk Assessment — Triage Workflow Orchestrator

## Classification

**Not classified as a High-Risk AI System under Annex III.**

## Reasoning

The Triage Workflow Orchestrator does **not** itself run any AI model. It is a coordination layer that calls already-classified tactical agents in sequence via the A2A protocol. The risk profile inherits from the agents it composes:

| Component called | Risk profile | Notes |
|---|---|---|
| Incident Intake Agent | Not high-risk (Annex III) | See `services/incident-intake-agent/docs/eu-ai-act-risk-assessment.md` |
| Classification Agent | Not high-risk (Annex III) | See `services/classification-agent/docs/eu-ai-act-risk-assessment.md` |

Because all called agents are out-of-scope of Annex III, the orchestrator is also out-of-scope. The orchestrator carries general transparency / logging obligations (Art. 50, 12, 19) which are satisfied by:

- A2A Agent Card identifying this as an automated orchestrator
- Every chained call logged to the Confidential Audit Trail with the per-step decision, latency, and downstream agent identity
- The `decision_chain` artifact returned to the caller is the structured explainability surface

## Triggers for reassessment

- If any **future** agent added to the triage chain is classified as high-risk (e.g. the Automated Fix Agent in Stage 4), the orchestrator's `chain:` config makes it visible at audit time; reassess immediately.
- If the orchestrator gains autonomous routing logic (an LLM picking which agents to call), reassess — that would move it from "workflow" to "agent" in Anthropic terms and may pull it into Annex III §1c (autonomous decision-making).

## Review cadence

At every framework-version bump, and on every config change to `chain:`.
