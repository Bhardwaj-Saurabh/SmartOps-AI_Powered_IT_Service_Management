#!/usr/bin/env bash
# Submit a synthetic incident to the Incident Intake Agent via A2A.
# Pretty-prints the resulting Task.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_URL="${AGENT_URL:-http://localhost:8444}"
TOKEN="${GATEWAY_TOKEN:-$("${ROOT}/scripts/get_token.sh" agent-incident-intake)}"

PAYLOAD="${ROOT}/services/incident-intake-agent/tests/data/sample_a2a_payload.json"

curl -sf -X POST "${AGENT_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary "@${PAYLOAD}" \
  | python -m json.tool
