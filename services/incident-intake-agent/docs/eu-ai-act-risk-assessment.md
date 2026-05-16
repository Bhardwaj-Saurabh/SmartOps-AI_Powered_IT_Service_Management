# EU AI Act Risk Assessment — Incident Intake Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Incident Intake Agent processes inbound IT incident reports (emails, Slack messages, web forms) and produces a structured incident record. It performs:

- Entity extraction (reporter, affected service, symptoms, timestamp)
- Channel detection and parsing
- Duplicate detection against historical incidents (vector similarity)
- Field completeness validation against business rules

The agent's outputs feed *downstream* triage and routing agents. The intake step itself does not make:

- Employment, recruitment, or worker-evaluation decisions (Annex III §4)
- Education or vocational training decisions (Annex III §3)
- Decisions affecting access to essential private/public services (Annex III §5)
- Biometric identification or categorisation (Annex III §1)
- Decisions in critical infrastructure operation (Annex III §2) — note: it *receives* IT operations reports but does not actuate changes
- Justice, migration, asylum, or border-control decisions (Annex III §§ 6–8)

Therefore the agent falls under the EU AI Act's general-purpose AI system transparency and logging obligations (Articles 50, 52) rather than the high-risk regime of Chapter III.

## Obligations the agent already satisfies

| Obligation | How |
|---|---|
| Transparency that an AI system is in use (Art. 50) | A2A Agent Card explicitly identifies this as an AI service; outputs include `di.confidence` so callers know LLM-derived fields aren't deterministic |
| Logging (Art. 12 / 19) | Every request, prompt, and decision logged to Confidential Audit Trail (CAT) per DI AI Framework §6.3 |
| Human oversight at downstream gates (Art. 14, applied voluntarily) | Step 9 produces `INPUT_REQUIRED` clarifications when fields are missing — human reporter is asked, never overridden |
| Data governance (Art. 10, applied voluntarily) | No PII flows to PST (90-day store); reporter identity only in CAT (7-year, encrypted, RBAC + MFA) |

## Triggers that would change this classification

This assessment must be re-evaluated and may move to **high-risk** if any of the following happen:

- The agent begins to make resource-allocation decisions that materially affect access to services (e.g. auto-rejecting incidents from specific user groups).
- The agent is deployed in a critical-infrastructure context (Annex III §2) where misclassification has safety impact.
- The agent's outputs are used as the sole decision input by other systems without human review of the high-impact branches.

## Cross-references

- DI AI Framework spec §9 — EU AI Act compliance requirements
- Sibling agent of higher concern: **Automated Fix Agent** is the project's likely Annex III high-risk candidate (it executes infrastructure changes autonomously) and ships with the full high-risk artefact set: human-oversight intervention points, bias/accuracy metrics, Fundamental Rights Impact Assessment.

## Review cadence

Reviewed at every framework-version bump or at the addition of any decision branch that materially affects a downstream Annex III determination. Next scheduled review: at the start of the Resolution Workflow Orchestrator build (which will introduce automated routing decisions).
