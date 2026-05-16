#!/usr/bin/env bash
# Submit a synthetic incident to the Triage Workflow Orchestrator,
# which chains Incident Intake → Classification via the Capability Registry.
# Pretty-prints the resulting Task.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8454}"
TOKEN="${GATEWAY_TOKEN:-$("${ROOT}/scripts/get_token.sh" agent-triage-orchestrator)}"

PAYLOAD="${ROOT}/services/triage-workflow-orchestrator/tests/data/sample_triage_payload.json"

curl -sf -X POST "${ORCHESTRATOR_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary "@${PAYLOAD}" \
  | python -m json.tool
