# EU AI Act Risk Assessment — Verification Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Verification Agent runs health checks + synthetic-monitor replays + a pre/post metric comparison and combines the evidence with a single LLM evaluator call to produce `fix_verified: bool`. It does not actuate anything; the Resolution Orchestrator decides whether to trigger Saga rollback based on this agent's verdict.

The agent is **safety-critical** even though it's not high-risk: a false-positive `fix_verified=true` would mean a broken fix is left in place. The mitigation is in the agent's own design — the LLM evaluator is instructed to lean toward `fix_verified=false` on any ambiguity, and the comparison-tool's deterministic per-metric thresholds (from SBCA `verification_thresholds`) are an objective check that doesn't depend on the LLM at all.

## Notable controls

| Control | Where |
|---|---|
| Deterministic floor for "improvement" | SBCA `verification_thresholds` — pre/post comparison requires actual measurable improvement on the symptom metric |
| LLM evaluator bias | System prompt explicitly tells the model to default to `fix_verified=false` on ambiguity |
| Minimum confidence to assert success | SBCA `verification_confidence.min_to_emit_verified` (0.7) |
| Full evidence pack in CAT | health-check results + synthetic-monitor results + comparison output + LLM prompt/response |

## Triggers for reassessment

- If the agent ever becomes the sole authority for a rollback decision **without orchestrator review** (currently the orchestrator owns the saga compensation call).
- If new health-check sources include personal data beyond what's already in incident records.

## Review cadence

Every framework-version bump; whenever the verification rules in the semantic plane change materially.
