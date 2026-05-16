# EU AI Act Risk Assessment — Resolution Documenter Agent

## Classification

**Not classified as a High-Risk AI System under Annex III of the EU AI Act.**

## Reasoning

The agent generates structured resolution notes from incident metadata + diagnosis + fix + verification context and writes them to the knowledge base. Phase 1 only writes **drafts** (`documentation_publishing.publish_automatically = false`); auto-publishing is gated by a separate human reviewer agent in Phase 2+.

Outputs are operational knowledge documentation — not regulated speech, not advice, not contractually binding. The agent does not actuate infrastructure, decide service access, or affect employment / education / justice / biometrics / critical-infrastructure operation.

## Notable controls

| Control | Where |
|---|---|
| Drafts by default | SBCA `documentation_publishing.publish_automatically=false` — no auto-publish in Phase 1 |
| Create-vs-update is policy | SBCA `kb_update_policy` thresholds — auditable + tunable |
| LLM forbidden from inventing facts | System prompt: "Never invent ETAs, vendor names, or fixes not in the input" |
| Full audit | Markdown + decision (create/update/draft) recorded in CAT |

## Triggers for reassessment

- If `documentation_publishing.publish_automatically=true` is ever enabled, reassess: auto-published content is a content-moderation surface and Article 50 transparency obligations become load-bearing.
- If the KB ever becomes customer-facing (today it's internal-only).

## Review cadence

Every framework-version bump; whenever `kb_update_policy` or `documentation_publishing` change.
