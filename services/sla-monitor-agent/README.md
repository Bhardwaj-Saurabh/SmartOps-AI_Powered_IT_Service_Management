# SLA Monitor Agent

**Layer:** Tactical (DI AI Framework §2.2)
**Anthropic pattern:** prompt chaining (workflow); LLM is optional + descriptive only
**EU AI Act:** Not high-risk. See [docs/eu-ai-act-risk-assessment.md](docs/eu-ai-act-risk-assessment.md).

## Purpose (single sentence)

Calculate SLA compliance metrics and detect breaches in real time.

## Workflow

1. Receive `{incident, priority, customer_tier, started_at_epoch, state_transitions, region}`
2. SBCA `sla_targets[priority][customer_tier]` → response + resolve targets in minutes
3. SBCA `sla_business_hours_only[priority]` → choose 24/7 vs business-hours math
4. SBCA `business_hours[region]` → timezone, weekdays, start/end (if business-hours-only)
5. SBCA `sla_pause_conditions` → list of states that pause the clock
6. SBCA `sla_breach_warning_pct[priority]` → warning threshold
7. `clock-timer-service` computes raw elapsed (24/7 OR business-hours)
8. `sla-rules-engine` computes paused-minutes from state transitions
9. Subtract paused from raw → adjusted elapsed; compute consumed %
10. If `narrative.enabled`, LLM produces a one-line summary
11. Emit `sla_status` artifact with: targets, consumed %, breached/warning flags, narrative, recommended_action
