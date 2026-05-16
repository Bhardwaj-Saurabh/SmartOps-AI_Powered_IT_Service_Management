# EU AI Act Risk Assessment — Knowledge Search Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Knowledge Search Agent returns ranked knowledge-base articles for an incident. It is **purely an information-retrieval tool**: it does not actuate anything, doesn't make decisions about people, doesn't gate access to services, and doesn't generate content beyond a relevance re-ranking explanation. It is the digital equivalent of "we found these three articles that may help; here's why".

Output is consumed by Automated Fix (which has its own high-risk assessment in Stage 4b) and by human resolvers. Neither consumer treats this agent's ranking as binding.

## Obligations satisfied

| Obligation | How |
|---|---|
| Transparency (Art. 50)              | Agent Card marks this as an AI service |
| Logging (Art. 12 / 19)              | Full search query + candidate pool + LLM re-rank prompts/responses in CAT |
| Explainability                      | Each returned article carries a per-article `reasoning` string from the re-ranker |
| Freshness governance                | `knowledge_freshness.max_days_for_recommendation` flags stale articles in the response so downstream consumers can de-prioritise them |
| Data governance                     | Operates over an internal KB only; no personal data flows through search queries unless the incident summary contains it (in which case PST excludes it via the collector redaction rules) |

## Triggers for reassessment

- If the agent is ever wired to generate *new content* (article authoring) — current scope is retrieval + ranking only.
- If the KB ever stores personal data beyond what's already in incident records — reassess data-protection flow.

## Review cadence

Every framework-version bump; whenever `knowledge_freshness` or `knowledge_relevance` rules change.
