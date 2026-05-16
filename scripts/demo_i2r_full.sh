#!/usr/bin/env bash
# Full Incident-to-Resolution demo against the *primary* I2R orchestrator.
#
# Unlike scripts/demo_full_i2r.sh (which manually chains Triage → Resolution),
# this script makes a SINGLE A2A call into the primary orchestrator's
# handle_incident skill. The primary orchestrator then drives:
#   Triage → (optional escalation) → Resolution → Closure
# entirely via Capability Registry lookups.
#
# Exercise the rollback path:
#   SIMULATE_RUNBOOK_FAILURE_AT_STEP=0 docker compose up -d --force-recreate script-executor
#   scripts/demo_i2r_full.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
I2R_URL="${I2R_URL:-http://localhost:8484}"

I2R_TOKEN="$("${ROOT}/scripts/get_token.sh" agent-i2r-orchestrator)"

REQ=$(python3 <<'PY'
import json
payload = {
    "jsonrpc": "2.0",
    "id": "i2r-demo-1",
    "method": "message/send",
    "params": {
        "message": {
            "role": "user",
            "parts": [{"kind": "data", "data": {
                "channel": "email",
                "email_raw": (
                    "From: priya.rao@example.sales\n"
                    "To: helpdesk@example.com\n"
                    "Subject: URGENT: VPN keeps disconnecting every 2 minutes\n"
                    "Date: Tue, 12 May 2026 09:14:32 +0000\n\n"
                    "Hi team,\nI've been completely unable to work this morning. "
                    "The corporate VPN drops every couple of minutes and I have to "
                    "reconnect. This started around 8:50 AM. I'm on Windows 11, "
                    "using the Cisco AnyConnect client v4.10.\n"
                ),
                "trigger": "resolution",
                "current_state": "new",
                "region": "UK",
                "started_at_epoch": 1747040072,
                "state_transitions": [{"state": "new", "at_epoch": 1747040072}],
            }}],
            "metadata": {"di": {
                "capability": "handle_incident",
                "process": "i2r",
                "step": "i2r.root",
            }},
        }
    },
}
print(json.dumps(payload))
PY
)

echo "── Primary I2R Orchestrator: handle_incident ──"
curl -sf --max-time 240 -X POST "${I2R_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${I2R_TOKEN}" \
  --data-binary "${REQ}" \
  | python -m json.tool
