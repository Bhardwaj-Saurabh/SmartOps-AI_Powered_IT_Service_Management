# Resolution Documenter Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow)
**EU AI Act:** Not high-risk. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).
**Standalone MCP:** disabled.

## Purpose (single sentence)

Generate structured resolution notes and update the knowledge base.

## Workflow

1. Receive composite `{incident, classification, diagnosis (opt), fix_result, verification (opt)}`
2. SBCA `documentation_template_by_category` → template_id to use
3. SBCA `kb_update_policy` → update-vs-create thresholds
4. SBCA `documentation_publishing` → publish vs draft toggle
5. LLM composes structured note JSON
6. **Decide:** find a close existing article via `knowledge-base-connector` keyword search
7. If close match AND effectiveness above threshold → call `knowledge-base-writer.update`
8. Else if no close match → call `knowledge-base-writer.create` (draft mode per policy)
9. `document-formatter.render` produces the final markdown
10. Emit `documentation` artifact with decision + article_id + markdown
