#!/usr/bin/env bash
# Fetch an OIDC client-credentials token from Keycloak for a given agent.
# Usage:  scripts/get_token.sh agent-incident-intake
#         export GATEWAY_TOKEN="$(scripts/get_token.sh agent-incident-intake)"
#
# Requires Keycloak admin port published on localhost:8081 (default in compose).

set -euo pipefail

CLIENT_ID="${1:-agent-incident-intake}"
REALM_URL="${KEYCLOAK_REALM_URL:-http://localhost:8081/realms/smartops}"

# Map known dev secrets — these are the *committed* defaults in realm-export.json.
# Override with env var for any non-default secret rotation.
case "${CLIENT_ID}" in
  agent-incident-intake)         DEFAULT_SECRET="dev-incident-intake-secret-rotate-me" ;;
  agent-sbca)                    DEFAULT_SECRET="dev-sbca-secret-rotate-me" ;;
  agent-classification)          DEFAULT_SECRET="dev-classification-secret-rotate-me" ;;
  agent-priority-scorer)         DEFAULT_SECRET="dev-priority-scorer-secret-rotate-me" ;;
  agent-routing)                 DEFAULT_SECRET="dev-routing-secret-rotate-me" ;;
  agent-diagnostic)              DEFAULT_SECRET="dev-diagnostic-secret-rotate-me" ;;
  agent-knowledge-search)        DEFAULT_SECRET="dev-knowledge-search-secret-rotate-me" ;;
  agent-triage-orchestrator)     DEFAULT_SECRET="dev-triage-orchestrator-secret-rotate-me" ;;
  agent-resolution-orchestrator) DEFAULT_SECRET="dev-resolution-orchestrator-secret-rotate-me" ;;
  *)                             DEFAULT_SECRET="" ;;
esac

CLIENT_SECRET="${CLIENT_SECRET:-${DEFAULT_SECRET}}"
if [ -z "${CLIENT_SECRET}" ]; then
  echo "Set CLIENT_SECRET env var for client ${CLIENT_ID}" >&2
  exit 1
fi

curl -sf -X POST "${REALM_URL}/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])"
