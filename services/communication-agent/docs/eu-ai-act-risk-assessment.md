# EU AI Act Risk Assessment — Communication Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The Communication Agent generates audience-tailored incident-status updates and dispatches them through email / Slack / SMS sidecars. It does not make decisions affecting access to services, employment, education, justice, biometrics, or critical-infrastructure operation. Generated content is short-lived operational status (not regulated speech, not advice, not contract-like).

The agent is **safety-relevant** because misleading or inaccurate updates can erode trust during an incident — addressed via the controls below.

## Notable controls

| Control | Where |
|---|---|
| Audience + channel matrix is policy, not code | SBCA `communication_templates` is auditable + tunable by non-engineers |
| Tone/length is prescribed per audience | System prompt + SBCA `tone`/`length` fields keep executive updates terse and end-user updates empathetic; not free-form |
| No fabricated certainty | System prompt explicitly forbids invented uncertainty (e.g. inventing an ETA the agent doesn't have) |
| Reporter PII excluded from resolver-team messages | Composer prompt: "Never include reporter PII beyond the department" |
| Full content + recipient list logged to CAT | Every send is auditable retrospectively |
| Delivery failures surfaced | `deliveries_failed` KPI on the PST stream |

## Triggers for reassessment

- If the agent ever sends external communications outside the operating organisation (e.g. customer SMS for a SaaS provider) — Art. 50 transparency obligations strengthen substantially.
- If outputs become contractual (e.g. RPO/RTO commitments by SMS) — that crosses out of operational status into committed terms.

## Review cadence

Every framework-version bump; whenever the templates rule changes materially; whenever a new channel is added (channel added = new content surface, new risk profile).
