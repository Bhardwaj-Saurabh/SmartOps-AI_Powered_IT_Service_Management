# Classification Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** parallelization (LLM + history matcher run concurrently; results combined)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose (single sentence)

Classify incidents by service area and category.

## 8-step workflow (parallelization pattern)

Steps 2 and 3 run **in parallel** — this is the defining trait of the Anthropic parallelization pattern. Steps 1, 4–8 are sequential.

1. Receive structured incident (from Triage Orchestrator)
2. **Parallel** — LLM classifier (taxonomy-aware prompt; json_schema response)
3. **Parallel** — Embed summary → Qdrant nearest historical incidents → majority label
4. Weighted-confidence merge (weights from SBCA)
5. Taxonomy validate via `taxonomy-lookup` sidecar; reject if label is not in current taxonomy version
6. Apply SBCA `classification_overrides` (security-keyword override etc.)
7. Hard-fail if taxonomy version drift detected against `classification_taxonomy_version`
8. Return `service_area`, `category`, `confidence`, `decision_chain` artifact

## A2A capability

`incident_classification` — input is a previously-extracted incident (matching the Incident Intake Agent's output schema); output is a label record.

## Wiring it up

- **Tools (sidecars):** `taxonomy-lookup:9001`, `historical-pattern-matcher:9002`
- **Semantic plane queries:** `classification_overrides`, `classification_confidence`, `classification_taxonomy_version`
- **AI Gateway:** chat (label decision) + embeddings (history match)
