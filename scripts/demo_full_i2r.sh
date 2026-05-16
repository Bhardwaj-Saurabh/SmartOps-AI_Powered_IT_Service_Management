#!/usr/bin/env bash
# Full Incident-to-Resolution demo: Triage → Resolution end-to-end.
#
# 1. Triage Orchestrator receives a raw email, runs Intake → Classify →
#    Priority → Routing, emits the triaged-incident shape.
# 2. We extract that composite + pass it to the Resolution Orchestrator,
#    which runs Diagnostic → Knowledge Search → Automated Fix → Verify
#    (with Saga rollback if Verification fails).
#
# Test the rollback path:
#   SIMULATE_RUNBOOK_FAILURE_AT_STEP=1 docker compose up -d --force-recreate script-executor
#   scripts/demo_full_i2r.sh         # script-executor fails → Automated Fix
#                                    # rolls back; saga is exercised via the
#                                    # rolled_back state too.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIAGE_URL="${TRIAGE_URL:-http://localhost:8454}"
RESOLUTION_URL="${RESOLUTION_URL:-http://localhost:8464}"

TRIAGE_TOKEN="$("${ROOT}/scripts/get_token.sh" agent-triage-orchestrator)"
RESOLUTION_TOKEN="$("${ROOT}/scripts/get_token.sh" agent-resolution-orchestrator)"

echo "── Phase 1: Triage Orchestrator ──"
TRIAGE_PAYLOAD="${ROOT}/services/triage-workflow-orchestrator/tests/data/sample_triage_payload.json"
TRIAGE_RESPONSE=$(curl -sf --max-time 90 -X POST "${TRIAGE_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TRIAGE_TOKEN}" \
  --data-binary "@${TRIAGE_PAYLOAD}")

if [ -z "${TRIAGE_RESPONSE}" ]; then
  echo "Triage call returned no body" >&2
  exit 1
fi

echo "${TRIAGE_RESPONSE}" | python -m json.tool

# Extract the four step artifacts and assemble the resolution input.
RESOLUTION_INPUT=$(printf '%s' "${TRIAGE_RESPONSE}" | python3 <<'PY'
import json, sys
body = json.load(sys.stdin)
result = body.get("result") or {}
triage_art = next((a for a in (result.get("artifacts") or []) if a.get("name") == "triage_result"), None)
triage = (next((p for p in (triage_art or {}).get("parts") or [] if p.get("kind") == "data"), {}) or {}).get("data") or {}
by_step = {s["step"]: s for s in triage.get("steps") or []}
def artifact(step_label, artifact_name):
    s = by_step.get(step_label) or {}
    for a in s.get("artifacts") or []:
        if a.get("name") == artifact_name:
            return a.get("data") or {}
    return {}

incident       = artifact("triage.intake",     "incident")
classification = artifact("triage.classify",   "classification")
priority       = artifact("triage.prioritise", "priority")
routing        = artifact("triage.route",      "routing")

# Resolution Orchestrator expects {incident, classification, priority}.
payload = {
    "jsonrpc": "2.0",
    "id": "resolve-demo-1",
    "method": "message/send",
    "params": {
        "message": {
            "role": "user",
            "parts": [{"kind": "data", "data": {
                "incident": incident,
                "classification": classification,
                "priority": priority,
                "routing": routing,    # passed through for downstream context
            }}],
            "metadata": {"di": {
                "capability": "resolve_incident",
                "process": "i2r",
                "step": "resolution.root",
            }},
        }
    },
}
print(json.dumps(payload))
PY
)

echo
echo "── Phase 2: Resolution Orchestrator ──"
curl -sf --max-time 120 -X POST "${RESOLUTION_URL}/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${RESOLUTION_TOKEN}" \
  --data-binary "${RESOLUTION_INPUT}" \
  | python -m json.tool
