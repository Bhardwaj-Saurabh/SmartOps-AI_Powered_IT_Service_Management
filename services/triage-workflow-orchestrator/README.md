# Triage Workflow Orchestrator

**Layer:** Strategic Sub-Process Orchestrator (DI AI Framework §2.1)
**Anthropic pattern:** prompt chaining (deterministic sequence; no LLM-driven control flow at this level)
**EU AI Act:** Not high-risk — see [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## The point of this service

This is the **first service-layer agent**. It demonstrates the compounding-value claim: building a new workflow over the 12 tactical agents requires **only** wiring up capability names — no hardcoded URLs, no shared transport code, no agent edits.

In Stage 3a it chains two agents (Intake → Classification). Stage 3b adds Priority Scorer → Routing. Stage 4+ may add more triage steps. **None of the called tactical agents need to change.**

## How it works

1. Caller sends `message/send` with capability `triage_incident` to this orchestrator
2. The orchestrator reads its `chain:` list from `configs/agent.yaml`
3. For each step in the chain:
   - Resolve the capability via `A2AClient.from_capability(name, registry_url=SBCA_URL)`
   - Send the previous step's relevant artifact as input (`forward_field`)
   - Honour short-circuits: if a step returns `INPUT_REQUIRED` (e.g. Incident Intake needs clarification), the orchestrator stops and forwards that state up
4. Returns a `triaged_incident` artifact containing every step's output + the decision chain

## Composability promise

A new orchestrator (e.g. "service request fulfillment") can reuse the same Incident Intake + Classification agents by:

1. Copying `configs/agent.yaml`
2. Changing `name`, `oidc.client_id`, and the `chain:` order
3. Setting a different `process` value in the A2A envelope (e.g. `process: "service_request"`)

No agent changes. No new infrastructure. The tactical agents emit the same KPIs but tagged by the new `process` so the dashboards split automatically.
