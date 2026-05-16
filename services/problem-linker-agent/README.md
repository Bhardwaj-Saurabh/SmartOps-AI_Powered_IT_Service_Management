# Problem Linker Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow); one LLM call for systemic-pattern assessment
**EU AI Act:** Not high-risk. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP:** disabled.

## Purpose (single sentence)

Identify recurring incident patterns and link them to problem records.

## Workflow

1. Receive composite `{incident, classification, diagnosis (opt)}`
2. SBCA `problem_creation_threshold[service_area]` + `window_days` → recurrence threshold
3. SBCA `cluster_min_similarity[default]` → cluster cohesion floor
4. SBCA `problem_link_categories.eligible` → is this (area, category) auto-eligible?
5. `incident-history-connector.query` → past incidents matching service_area + category in window + open problem records
6. `clustering-tool.cluster` → group history into clusters by similarity signature
7. Filter clusters by SBCA `cluster_min_similarity` floor
8. **Match against existing open problems** by similarity signature → if hit, link incident to that problem
9. Else if biggest cluster meets the threshold AND category is eligible → LLM assesses whether it's systemic
10. Emit `problem_link` artifact with decision (linked / new_problem_recommended / below_threshold / not_eligible)
