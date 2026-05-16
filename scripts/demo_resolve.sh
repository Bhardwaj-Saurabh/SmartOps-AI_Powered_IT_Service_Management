#!/usr/bin/env bash
# Submit a triaged incident to the Resolution Workflow Orchestrator.
# Chains Diagnostic + Knowledge Search via the Capability Registry.
# Expected input shape mirrors the Triage Orchestrator's output:
#   { incident, classification, priority }
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORCH_URL="${ORCH_URL:-http://localhost:8464}"
TOKEN="${GATEWAY_TOKEN:-$("${ROOT}/scripts/get_token.sh" agent-resolution-orchestrator)}"

read -r -d '' BODY <<'JSON' || true
{
  "jsonrpc": "2.0",
  "id": "resolve-demo-1",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "data",
          "data": {
            "incident": {
              "incident_id": "INC-RESDEMO1",
              "affected_service": "okta-sso",
              "symptoms_summary": "Multiple users report AADSTS50105 when signing into Salesforce via SSO",
              "symptoms_verbatim": "AADSTS50105: signed in user not assigned to a role"
            },
            "classification": {
              "service_area": "application",
              "category": "okta-sso"
            },
            "priority": {
              "priority": "P2",
              "blast_radius": 3
            }
          }
        }
      ],
      "metadata": {
        "di": {
          "capability": "resolve_incident",
          "process": "i2r",
          "step": "resolution.root"
        }
      }
    }
  }
}
JSON

curl -sf -X POST "${ORCH_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary "${BODY}" \
  | python -m json.tool
