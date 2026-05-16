#!/usr/bin/env bash
# Closure Workflow demo. Submits a resolved-incident payload to the closure
# orchestrator, which fans out to Communication + SLA Monitor.
#
# To see what was "sent":
#   curl -s http://localhost:8474/...    # not exposed; use docker logs
#   docker compose -f infra/docker-compose.yaml exec -T email-sender curl -s http://localhost:9001/sent | python -m json.tool
#   docker compose -f infra/docker-compose.yaml exec -T slack-poster  curl -s http://localhost:9002/posted | python -m json.tool
#   docker compose -f infra/docker-compose.yaml exec -T sms-gateway   curl -s http://localhost:9003/sent | python -m json.tool
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOSURE_URL="${CLOSURE_URL:-http://localhost:8474}"
TOKEN="${GATEWAY_TOKEN:-$("${ROOT}/scripts/get_token.sh" agent-closure-orchestrator)}"

NOW_EPOCH="$(date -u +%s)"
STARTED_EPOCH=$((NOW_EPOCH - 5400))   # 90 minutes ago

read -r -d '' BODY <<JSON || true
{
  "jsonrpc": "2.0",
  "id": "closure-demo-1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "data",
          "data": {
            "incident": {
              "incident_id": "INC-CLDEMO",
              "affected_service": "okta-sso",
              "reporter": "alice@example.sales",
              "reporter_department": "sales",
              "symptoms_summary": "Multiple users reported AADSTS50105; fix applied + verified"
            },
            "classification": {"service_area": "application", "category": "okta-sso"},
            "priority": {"priority": "P2", "service_tier": "silver", "blast_radius": 3, "emergency": false},
            "diagnosis": {"root_cause": "CA group sync lapsed", "cause_type": "configuration", "confidence": 0.92},
            "fix_result": {"state": "completed", "selected_runbook_id": "okta-ca-resync",
                           "rollback_token": "snap-CL1", "what_changed": "Re-synced CA group"},
            "verification": {"fix_verified": true, "confidence": 0.9},
            "trigger": "resolution",
            "current_state": "resolved",
            "region": "UK",
            "started_at_epoch": ${STARTED_EPOCH},
            "state_transitions": [
              {"state": "new",      "at_epoch": ${STARTED_EPOCH}},
              {"state": "working",  "at_epoch": $((STARTED_EPOCH + 600))},
              {"state": "resolved", "at_epoch": ${NOW_EPOCH}}
            ]
          }
        }
      ],
      "metadata": {"di": {"capability": "close_incident", "process": "i2r", "step": "closure.root"}}
    }
  }
}
JSON

curl -sf --max-time 90 -X POST "${CLOSURE_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary "${BODY}" \
  | python -m json.tool
