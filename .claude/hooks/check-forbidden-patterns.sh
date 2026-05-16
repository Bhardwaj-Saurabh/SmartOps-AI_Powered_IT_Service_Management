#!/usr/bin/env bash
# DI AI Framework compliance hook — blocks anti-patterns from §11 of docs/DI_AI_FRAMEWORK.md
# Fires on PostToolUse for Write|Edit. Reads tool input JSON from stdin.
# Exit 2 + stderr message blocks the action with feedback shown to Claude.

set -uo pipefail

# Read the hook event JSON
input="$(cat)"

# Extract file path written/edited. Falls back gracefully if jq is missing.
if command -v jq >/dev/null 2>&1; then
  file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
else
  file_path="$(printf '%s' "$input" | sed -nE 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1)"
fi

[ -z "${file_path}" ] && exit 0
[ ! -f "${file_path}" ] && exit 0

violations=()

# ─── LiteLLM image pin enforcement (YAML / Compose / Dockerfile) ────────────
# CVE-2026-42208 (pre-auth SQLi, CISA KEV) and the v1.83.x advisory chain require
# pinning to v1.83.7+ stable. Block forbidden tags anywhere in the repo.
case "${file_path}" in
  *.yaml|*.yml|*Dockerfile*|*.env|*.env.*)
    if grep -nE 'ghcr\.io/berriai/litellm:(latest|main|v1\.(7[0-9]|80|81|82|83\.[0-6])([^0-9]|$))' "${file_path}" >/dev/null \
       || grep -nE 'berriai/litellm:(latest|main)([^0-9]|$)' "${file_path}" >/dev/null; then
      matches=$(grep -nE 'berriai/litellm:[^[:space:]"'"'"']+' "${file_path}")
      violations+=("Forbidden LiteLLM image tag. Pin to ghcr.io/berriai/litellm:v1.83.10-stable (or later patched stable) — see CLAUDE.md 'LiteLLM Hardening'. CVE-2026-42208 is on the CISA KEV list:"$'\n'"${matches}")
    fi
    # Don't publish LiteLLM port to host
    if grep -nE '^\s*-\s*["'"'"']?4000:4000["'"'"']?\s*$' "${file_path}" >/dev/null; then
      matches=$(grep -nE '^\s*-\s*["'"'"']?4000:4000' "${file_path}")
      violations+=("LiteLLM port 4000 must NOT be published to the host. Use internal Compose network only:"$'\n'"${matches}")
    fi
  ;;
esac

# Only the Python-import checks below apply to Python files
case "${file_path}" in
  *.py) ;;
  *)
    if [ ${#violations[@]} -gt 0 ]; then
      {
        echo "❌ DI AI Framework compliance violation in ${file_path}"
        echo
        for v in "${violations[@]}"; do echo "• ${v}"; echo; done
        echo "See docs/DI_AI_FRAMEWORK.md §11 and CLAUDE.md for the compliant pattern."
      } >&2
      exit 2
    fi
    exit 0
  ;;
esac

case "${file_path}" in
  */libs/gateway_client/*) exit 0 ;;
  */libs/a2a_server/*|*/libs/a2a_client/*) ;;  # checked, but allowed to import httpx etc.
  */services/*|*/libs/*) ;;
  *) exit 0 ;;
esac

# 1. Direct LLM SDK imports — must route via libs/gateway_client
if grep -nE '^[[:space:]]*(from|import)[[:space:]]+(openai|anthropic|azure\.ai\.inference|azure_ai_inference|azure\.ai\.openai|azure_ai_openai|azure\.openai|openai_python)([[:space:]]|$|\.)' "${file_path}" >/dev/null; then
  matches=$(grep -nE '^[[:space:]]*(from|import)[[:space:]]+(openai|anthropic|azure\.ai\.inference|azure_ai_inference|azure\.ai\.openai|azure_ai_openai|azure\.openai)' "${file_path}")
  violations+=("Direct LLM SDK import detected. All LLM calls MUST route through libs/gateway_client (LiteLLM):"$'\n'"${matches}")
fi

# 2. Subsumed frameworks — Microsoft Agent Framework replaces these
if grep -nE '^[[:space:]]*(from|import)[[:space:]]+(semantic_kernel|autogen|pyautogen)([[:space:]]|$|\.)' "${file_path}" >/dev/null; then
  matches=$(grep -nE '^[[:space:]]*(from|import)[[:space:]]+(semantic_kernel|autogen|pyautogen)' "${file_path}")
  violations+=("Subsumed framework import. Use \`agent_framework\` (Microsoft Agent Framework) — do not also install semantic-kernel or autogen:"$'\n'"${matches}")
fi

# 3. Third-party A2A wrappers — implement against Google spec via libs/a2a_*
if grep -nE '^[[:space:]]*(from|import)[[:space:]]+(a2a_sdk|google_a2a|a2a_python|fastA2A|fasta2a)([[:space:]]|$|\.)' "${file_path}" >/dev/null; then
  matches=$(grep -nE '^[[:space:]]*(from|import)[[:space:]]+(a2a_sdk|google_a2a|a2a_python|fastA2A|fasta2a)' "${file_path}")
  violations+=("Third-party A2A wrapper detected. Use libs/a2a_server / libs/a2a_client (spec-native Google A2A):"$'\n'"${matches}")
fi

# 4. Hardcoded business thresholds — likely belongs in configs/semantic-plane/*.yaml
#    Heuristic: numeric comparisons against named business concepts in services/ (not libs/)
case "${file_path}" in
  */services/*)
    if grep -nE '(priority|sla|threshold|severity|escalat|approval|vip|freeze|confidence)[[:alnum:]_]*[[:space:]]*(==|>=|<=|>|<)[[:space:]]*[0-9]' "${file_path}" -i >/dev/null; then
      matches=$(grep -nE '(priority|sla|threshold|severity|escalat|approval|vip|freeze|confidence)[[:alnum:]_]*[[:space:]]*(==|>=|<=|>|<)[[:space:]]*[0-9]' "${file_path}" -i | head -n5)
      violations+=("Possible hardcoded business rule. Query libs/semantic_client → SBCA → configs/semantic-plane/*.yaml instead:"$'\n'"${matches}")
    fi
  ;;
esac

# 5. Tool library embedding — tools are sidecar containers on localhost:9xxx
if grep -nE 'from[[:space:]]+(email_parser|slack_connector|taxonomy_lookup|embedding_search|knowledge_base_connector|script_executor|log_aggregator|metrics_query|topology_walker|health_check_runner|synthetic_monitor|sla_rules_engine|clock_timer|document_formatter|incident_history|clustering_tool|impact_analyser|service_dependency_mapper|team_directory|skill_matrix|configuration_manager|rollback_handler|comparison_tool|email_sender|slack_poster|sms_gateway|knowledge_base_writer|historical_pattern_matcher|form_normaliser)_lib' "${file_path}" >/dev/null; then
  matches=$(grep -nE 'from[[:space:]]+\w+_lib' "${file_path}")
  violations+=("Tool library import detected. Tools MUST run as sidecar containers — call via HTTP on localhost:9xxx:"$'\n'"${matches}")
fi

# 6. Hardcoded provider secrets
if grep -nE '(sk-[a-zA-Z0-9]{20,}|AKIA[A-Z0-9]{16}|AZURE_OPENAI_KEY[[:space:]]*=[[:space:]]*["'"'"'][^"'"'"']+)' "${file_path}" >/dev/null; then
  violations+=("Hardcoded secret detected. Secrets go to gitignored .env.local mounted into LiteLLM / Keycloak only.")
fi

if [ ${#violations[@]} -gt 0 ]; then
  {
    echo "❌ DI AI Framework compliance violation in ${file_path}"
    echo
    for v in "${violations[@]}"; do
      echo "• ${v}"
      echo
    done
    echo "See docs/DI_AI_FRAMEWORK.md §11 and CLAUDE.md 'Hard Rules' for the compliant pattern."
  } >&2
  exit 2
fi

exit 0
