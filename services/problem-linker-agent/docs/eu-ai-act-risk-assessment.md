# EU AI Act Risk Assessment — Problem Linker Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The agent detects recurring incident patterns + recommends linking incidents to existing problem records (or recommends new ones). It does not actuate anything — its outputs are advisory recommendations for problem-management workflow.

The agent's LLM step is instructed to default to `is_systemic=false` on ambiguity; SBCA-supplied thresholds gate when a new-problem recommendation is even considered. Linking to an *existing* open problem (the most common action) is purely metadata association.

## Notable controls

| Control | Where |
|---|---|
| Recurrence threshold is policy | SBCA `problem_creation_threshold` keyed by service_area; security category is more aggressive |
| Cluster cohesion required | SBCA `cluster_min_similarity` filters thin clusters |
| Eligibility allow-list | SBCA `problem_link_categories.eligible` — categories not on the list never get auto-recommendation |
| LLM bias to "not systemic" | System prompt mandates the safe default; single-user clusters explicitly downweighted |
| Audit | LLM prompt + response + cluster contents + decision all in CAT |

## Triggers for reassessment

- If problem records ever auto-create change requests touching production (today: recommend-only).
- If the agent's output is wired to drive *any* automated action (e.g. opening a change ticket without human review).

## Review cadence

Every framework-version bump; on any `problem_creation_threshold` or `problem_link_categories` change.
