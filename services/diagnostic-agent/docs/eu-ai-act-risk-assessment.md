# EU AI Act Risk Assessment — Diagnostic Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Diagnostic Agent performs automated root-cause analysis: it correlates logs, metrics, and service topology to produce a ranked hypothesis about why an incident is occurring. It does **not** make any change to infrastructure — that responsibility belongs to the Automated Fix Agent (Stage 4b), which carries its own (high-risk) assessment.

A diagnosis is an analytic output, not a decision affecting access to services, employment, education, justice, biometrics, or critical-infrastructure operation. Downstream consumers (Knowledge Search, Automated Fix, or a human resolver) act on the diagnosis under their own risk profiles.

## Notable model behaviour

The agent runs an **evaluator-optimizer** loop (Anthropic pattern): it generates a candidate hypothesis, runs validation tool calls, and re-scores the hypothesis. Iteration count is capped at `diagnostic_confidence.max_iterations` (3 in the seed rules) and terminates early at `min_to_emit`. Below `min_to_accept` the agent emits `failed` with the strongest hypothesis it reached — it does not synthesise certainty it doesn't have.

This iterative model behaviour is the reason `pattern_kind` is set to `agent` (LLM-driven control flow) rather than `workflow`. The framework's logging requirements still apply uniformly; every iteration is captured in the CAT audit trail (`hypothesis_history`, `evaluator_scores`).

## Obligations satisfied

| Obligation | How |
|---|---|
| Transparency (Art. 50)              | Agent Card marks this as an AI agent; `di.confidence` returned |
| Logging (Art. 12 / 19)              | Every iteration's prompt + response + evidence chain captured in CAT |
| Explainability                      | The `decision_chain` artifact records each iteration's hypothesis, validation steps, and evaluator score |
| Robustness                          | Iteration cap + minimum-confidence emit floor prevent the agent fabricating certainty |
| Human oversight                     | An on-call engineer can call this agent via MCP for second opinions; the diagnostic output is never the sole input to any actuator without explicit downstream review |

## Triggers for reassessment

- If the diagnosis output is wired to drive Automated Fix selection *without* the orchestrator's explicit approval-rule check, reassess — the responsibility chain breaks.
- If the agent's evidence-collection scope expands to PII-bearing audit sources (e.g. user activity logs), reassess for data-protection implications.

## Review cadence

Every framework-version bump; whenever `diagnostic_confidence` thresholds change; before any future change that connects this agent's output directly to an actuator.
