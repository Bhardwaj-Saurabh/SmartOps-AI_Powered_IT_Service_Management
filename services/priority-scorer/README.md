# Priority Scorer

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow, not autonomous agent)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose (single sentence)

Compute incident priority (P1–P4) from impact and urgency.

## 10-step workflow chain

1. Receive composite input (`incident` + `classification`)
2. Tool: service-dependency-mapper → blast radius + service tier
3. LLM: impact + urgency narrative analysis
4. SBCA: `blast_radius_thresholds` — apply urgency floor from blast radius
5. Tool: impact-analyser → numeric impact score + bucket (uses blast radius + VIP)
6. Take max of LLM impact bucket and analyser impact bucket
7. SBCA: `priority_matrix` → impact × urgency cell → P-level
8. SBCA: `vip_priority_overrides` → enforce VIP minimum P-level
9. SBCA: `change_freeze` → annotate (does not block; Automated Fix uses this)
10. Emit `priority` artifact with full `decision_chain`

## A2A capability

`priority_scoring` — input is `{incident: {...}, classification: {...}}`; output is `{incident_id, priority, impact, urgency, decision_chain, ...}`.
