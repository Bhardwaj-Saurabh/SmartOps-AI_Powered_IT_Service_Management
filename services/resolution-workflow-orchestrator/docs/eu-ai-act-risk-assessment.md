# EU AI Act Risk Assessment — Resolution Workflow Orchestrator

## Classification (Stage 4a)

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

This orchestrator runs no AI model itself. It chains tactical agents over the A2A protocol via the Capability Registry. The risk profile inherits from the tactical agents it calls.

For **Stage 4a** the chain is read-only:

| Component called | Risk profile |
|---|---|
| Diagnostic Agent           | Not high-risk (Annex III) |
| Knowledge Search Agent     | Not high-risk (Annex III) |

Neither agent actuates infrastructure.

## Reassessment required at Stage 4b

When Stage 4b adds the **Automated Fix Agent** to the chain, this orchestrator pulls a high-risk tactical agent into its sequence. At that point:

- The orchestrator's `saga:` configuration becomes load-bearing: a verification failure MUST trigger a rollback compensation, audited in CAT.
- The orchestrator's Annex III status itself moves to high-risk because it can be the proximate cause of an autonomous infrastructure change (even though the actuation itself happens in Automated Fix). The Fundamental Rights Impact Assessment for Automated Fix will reference this orchestrator's audit trail as the authorising boundary.
- This document MUST be updated before merging Stage 4b.

## Triggers for reassessment

- Adding any high-risk tactical agent to `chain:` (immediate reassessment).
- Adding LLM-driven orchestration logic (e.g. an LLM picking which agents to call) — that would make this orchestrator itself an "agent" by Anthropic's strict definition and may pull it into Annex III §1.

## Review cadence

At every framework-version bump, on every `chain:` edit, and unconditionally before Stage 4b merges.
