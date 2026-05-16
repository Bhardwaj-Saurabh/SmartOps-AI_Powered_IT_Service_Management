# Communication Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow)
**EU AI Act:** Not high-risk. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP exposed:** yes (`draft_communication`, `send_update` per PRD)

## Purpose (single sentence)

Generate and send audience-tailored incident-status updates.

## Workflow

1. Receive composite input `{incident, classification, priority, diagnosis (opt), fix_result (opt), verification (opt)}` + a `trigger` value (e.g. `state_change` / `escalation` / `resolution`)
2. SBCA `communication_templates[priority]` → audiences + channels + tone + length to use
3. SBCA `escalation_audiences[priority]` → extra audiences if the trigger is `escalation`
4. Resolve recipient list per audience (synthetic Phase 1: reporter email, well-known team aliases)
5. **For each (audience, channel) cell** — LLM composes content (one call per cell)
6. Per cell, dispatch to the appropriate sidecar (email-sender / slack-poster / sms-gateway)
7. Aggregate dispatch results into a `communications_sent` artifact
