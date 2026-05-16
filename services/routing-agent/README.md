# Routing Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** parallelization (team directory + skill matrix queried concurrently)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose (single sentence)

Pick the resolver team for an incident.

## 8-step workflow

1. Receive composite input (`incident` + `classification` + `priority`)
2. SBCA: `routing_rules` → candidate teams for the service area
3. SBCA: `routing_priority_overrides` → extra candidates for P1/P2
4. **Parallel** — `team-directory-connector` (availability + queue depth) + `skill-matrix-lookup` (match score per team)
5. SBCA: `load_balancing` → filter teams above max queue depth
6. LLM ranks remaining candidates given priority + symptoms
7. Weighted score = LLM × `routing_llm_weight` + skill_match × (1 − `routing_llm_weight`); pick max
8. Emit `routing` artifact with chosen team + full ranking

## A2A capability

`incident_routing` — input is `{incident, classification, priority}`; output is `{incident_id, assigned_team, candidate_ranking, decision_chain}`.
