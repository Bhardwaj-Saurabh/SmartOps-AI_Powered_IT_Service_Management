# Diagnostic Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** evaluator-optimizer (**this is the project's first true "agent" by Anthropic's stricter definition** — LLM-driven control flow with iteration count chosen at run time, not predetermined)
**EU AI Act:** Not high-risk under Annex III. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP exposed:** yes (port 8443 — `diagnose_service`, `correlate_logs`)

## Purpose (single sentence)

Correlate logs, metrics, and topology into a ranked root-cause hypothesis.

## Workflow — evaluator-optimizer loop

1. Receive incident + classification + priority (composite input from orchestrator)
2. SBCA: `known_issues` — if any matches, short-circuit to high-confidence diagnosis
3. SBCA: `diagnostic_confidence` (`min_to_emit`, `min_to_accept`, `max_iterations`) + `diagnostic_window`
4. Topology walk (request path)
5. **Loop** (up to `max_iterations`):
   a. Collect evidence: logs + metrics for the affected service + each upstream layer
   b. **Generator LLM**: produce candidate causes JSON with evidence citations + `best_index`
   c. Run validation tool calls scoped to the best candidate (extra metric/log queries)
   d. **Evaluator LLM**: score confidence given the validation evidence
   e. If `confidence >= min_to_emit` → break
6. Emit final diagnosis:
   * confidence ≥ `min_to_emit` → state `completed`
   * `min_to_accept ≤ confidence < min_to_emit` → state `completed`, flagged `low_confidence`
   * confidence `< min_to_accept` → state `failed` (don't synthesise certainty)

## A2A capability

`root_cause_analysis` — input `{incident, classification, priority}`; output `{incident_id, root_cause, confidence, iterations, decision_chain, supporting_evidence, ...}`.
