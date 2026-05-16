#!/usr/bin/env bash
# scripts/smoketest.sh
#
# Boot check + end-to-end triage round-trip against a running Compose stack.
# Run AFTER `docker compose -f infra/docker-compose.yaml up -d` has settled.
#
# Exits 0 on full PASS; non-zero with a per-check FAIL summary otherwise.
#
# Usage:
#   scripts/smoketest.sh                 # full suite
#   SKIP_E2E=1 scripts/smoketest.sh      # health checks only (skip Keycloak + triage call)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${ROOT}/infra/docker-compose.yaml"
COMPOSE="docker compose -f ${COMPOSE_FILE}"

ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8454}"
INTAKE_URL="${INTAKE_URL:-http://localhost:8444}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
KEYCLOAK_REALM_URL="${KEYCLOAK_REALM_URL:-http://localhost:8081/realms/smartops}"

# ─── helpers ───────────────────────────────────────────────────────────
pass_count=0
fail_count=0
fail_lines=()

pass() { echo "  ✓ $1"; pass_count=$((pass_count + 1)); }
fail() { echo "  ✗ $1" >&2; fail_count=$((fail_count + 1)); fail_lines+=("$1"); }

section() { echo; echo "── $1 ──"; }

require() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 2; }; }
require docker
require curl
require python3

# ─── 1. Compose stack health ──────────────────────────────────────────
section "Compose stack"

if ! $COMPOSE ps >/dev/null 2>&1; then
  fail "compose project not running — start with: docker compose -f infra/docker-compose.yaml up -d"
else
  unhealthy=$(
    $COMPOSE ps --format json 2>/dev/null \
      | python3 -c '
import json, sys
bad = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        # docker compose v2 may emit one JSON object per line or a single array
        if line.startswith("["):
            rows = json.loads(line)
        else:
            rows = [json.loads(line)]
    except json.JSONDecodeError:
        continue
    for r in rows:
        name = r.get("Service") or r.get("Name") or "?"
        state = r.get("State", "")
        health = r.get("Health", "")
        if state != "running":
            bad.append(f"{name}:state={state}")
        elif health and health != "healthy":
            bad.append(f"{name}:health={health}")
print("\n".join(bad))
'
  )
  if [ -z "${unhealthy}" ]; then
    pass "all compose services running + healthy"
  else
    while IFS= read -r line; do
      fail "service ${line}"
    done <<< "${unhealthy}"
  fi
fi

# ─── 2. Externally-exposed surface ─────────────────────────────────────
section "Exposed health endpoints"

check_http() {
  local label="$1" url="$2" expect="${3:-200}"
  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 4 "${url}" || echo "000")
  if [ "${code}" = "${expect}" ]; then
    pass "${label} → ${code}"
  else
    fail "${label} → ${code} (expected ${expect}, url=${url})"
  fi
}

check_http "incident-intake /health"          "${INTAKE_URL}/health"
check_http "triage-orchestrator /health"      "${ORCHESTRATOR_URL}/health"
check_http "triage-orchestrator Agent Card"   "${ORCHESTRATOR_URL}/.well-known/agent-card.json"
check_http "i2r-primary-orchestrator /health" "${I2R_URL:-http://localhost:8484}/health"
check_http "i2r-primary-orchestrator Card"    "${I2R_URL:-http://localhost:8484}/.well-known/agent-card.json"
check_http "qdrant readyz"                    "${QDRANT_URL}/readyz"
check_http "keycloak realm"                   "${KEYCLOAK_REALM_URL}/.well-known/openid-configuration"

# ─── 3. End-to-end triage round-trip ───────────────────────────────────
if [ "${SKIP_E2E:-0}" = "1" ]; then
  section "End-to-end (skipped via SKIP_E2E=1)"
else
  section "End-to-end triage round-trip"

  TOKEN_OUTPUT="$("${ROOT}/scripts/get_token.sh" agent-triage-orchestrator 2>&1 || echo "")"
  if [ -z "${TOKEN_OUTPUT}" ] || [[ "${TOKEN_OUTPUT}" == *"error"* ]]; then
    fail "could not obtain JWT for agent-triage-orchestrator: ${TOKEN_OUTPUT}"
  else
    pass "obtained JWT for orchestrator client"

    PAYLOAD="${ROOT}/services/triage-workflow-orchestrator/tests/data/sample_triage_payload.json"
    response=$(curl -sS --max-time 60 -X POST "${ORCHESTRATOR_URL}/" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN_OUTPUT}" \
      --data-binary "@${PAYLOAD}" || echo "")

    if [ -z "${response}" ]; then
      fail "triage POST returned no body"
    else
      # Parse the response in Python for robustness.
      summary=$(printf '%s' "${response}" | python3 -c '
import json, sys
body = json.load(sys.stdin)
if "error" in body and body["error"]:
    print("ERROR:" + json.dumps(body["error"]))
    sys.exit(0)
result = body.get("result") or {}
state = (result.get("status") or {}).get("state")
artifacts = result.get("artifacts") or []
triage_artifact = next((a for a in artifacts if a.get("name") == "triage_result"), None)
if not triage_artifact:
    print("NO_TRIAGE_ARTIFACT")
    sys.exit(0)
triage = next((p.get("data") for p in (triage_artifact.get("parts") or []) if p.get("kind") == "data"), {}) or {}
print(f"STATE:{state}|CHAIN:{triage.get(\"chain_state\")}|STEPS:{len(triage.get(\"steps\") or [])}")
' || echo "PARSE_ERROR")

      case "${summary}" in
        STATE:completed*CHAIN:completed*STEPS:4)
          pass "triage_incident: state=completed, 4 chain steps completed"
          ;;
        STATE:completed*CHAIN:completed*STEPS:*)
          steps=$(echo "${summary}" | sed -E 's/.*STEPS://')
          fail "triage_incident completed but only ${steps}/4 steps ran"
          ;;
        ERROR:*)
          fail "JSON-RPC error: ${summary#ERROR:}"
          ;;
        NO_TRIAGE_ARTIFACT)
          fail "triage response had no triage_result artifact (response: $(printf '%s' "${response}" | head -c 400))"
          ;;
        PARSE_ERROR)
          fail "couldn't parse triage response (first 400 chars: $(printf '%s' "${response}" | head -c 400))"
          ;;
        *)
          fail "triage_incident: ${summary}"
          ;;
      esac
    fi
  fi
fi

# ─── summary ───────────────────────────────────────────────────────────
echo
echo "──────────────────────────"
echo "  PASS: ${pass_count}    FAIL: ${fail_count}"
echo "──────────────────────────"

if [ "${fail_count}" -gt 0 ]; then
  echo
  echo "Failures:"
  for line in "${fail_lines[@]}"; do
    echo "  - ${line}"
  done
  echo
  echo "Inspect with: docker compose -f infra/docker-compose.yaml logs -f"
  exit 1
fi
