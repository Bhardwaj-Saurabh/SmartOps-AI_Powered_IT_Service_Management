# EU AI Act Risk Assessment — Routing Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Routing Agent picks a resolver team for an internal IT incident. The choice affects which internal staff group handles the ticket — it does **not** affect:
- Access to public services for the reporter (Annex III §5)
- Employment, recruitment, or worker evaluation (§4)
- Education, biometrics, justice, migration, or critical-infrastructure operation

It is operational dispatch within an IT service-management workflow. The agent combines three inputs:

1. SBCA `routing_rules` — deterministic service-area → preferred-team mapping
2. Team directory + skill matrix lookups via sidecars (parallel)
3. An LLM ranking of the candidate teams that survives steps 1–2

The LLM has a low weight (`routing_llm_weight: 0.4`) — final scoring is dominated by skill-match + queue capacity.

## Concerns to monitor

- **Worker-impact bias**: if the routing distribution skews unfairly toward certain teams over time, that's an internal fairness concern (workload) but not an Annex III §4 employment decision. Surface via PST KPIs (`routed_incident_count` by team).
- **Capacity overload**: `load_balancing.max_queue_depth` lives in SBCA so it can be tuned without code redeploy.

## Triggers for reassessment

- If a future change uses routing output to deny SLA coverage, reassess.
- If teams' headcount or queue is sourced from a learned model (rather than the synthetic directory), reassess for §4 implications.

## Review cadence

At every framework-version bump and whenever `routing_rules` or `load_balancing` rules change.
