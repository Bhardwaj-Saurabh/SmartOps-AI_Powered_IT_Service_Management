# Knowledge Search Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** parallelization (vector + keyword run concurrently, then LLM re-rank)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP exposed:** yes (port 8443 — `search_knowledge`, `find_similar_incidents`)

## Purpose (single sentence)

Find knowledge-base articles relevant to an incident.

## Workflow

1. Receive composite input (`incident` + `classification` + optional `diagnosis`)
2. SBCA: `knowledge_relevance` (min_score + vector/keyword weights) and `knowledge_freshness`
3. **Parallel** — embed the symptom summary via the AI Gateway, then concurrently:
   - vector search via `embedding-search-tool` → top-N similar articles
   - keyword search via `knowledge-base-connector` → top-N matches
4. Merge: weighted combination of vector + keyword scores using SBCA weights; dedupe by `article_id`
5. Filter by SBCA `min_score`
6. Flag articles older than `knowledge_freshness.max_days_for_recommendation` (returned but marked)
7. LLM re-rank — given hypothesised root cause, refine ordering
8. Emit `articles` artifact + applicability summary
