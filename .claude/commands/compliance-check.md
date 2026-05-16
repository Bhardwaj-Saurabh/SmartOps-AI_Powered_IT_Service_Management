---
description: Run the DI AI Framework compliance checklist against staged or recent changes. Delegates to the framework-compliance-reviewer and a2a-spec-validator subagents in parallel.
argument-hint: [--staged | --branch | --files <glob>]
allowed-tools: Bash, Read, Agent
---

The user wants a compliance verdict on `$ARGUMENTS` (default: working-tree changes).

## Procedure

1. **Determine the review scope.**
   - `--staged` → `git diff --cached --name-only`
   - `--branch` → `git diff --name-only $(git merge-base HEAD origin/main)..HEAD`
   - `--files <glob>` → expand the glob
   - no args → `git status --short` + `git diff --name-only`
   If the repo has no git changes and no args, ask the user which files / directories to review. Don't run a whole-repo scan blindly.

2. **Launch both subagents in parallel** (single message, two Agent tool uses):
   - `framework-compliance-reviewer` — pass the file list and tell it to enforce [docs/DI_AI_FRAMEWORK.md](docs/DI_AI_FRAMEWORK.md) §11/§12 and [CLAUDE.md](CLAUDE.md) "Hard Rules".
   - `a2a-spec-validator` — only if any reviewed file lives under `libs/a2a_*` or under `services/<agent>/src/main.py` (Agent Card surface). Otherwise skip this agent and note it as N/A.

3. **Merge the two reports** and apply the §12.1 verdict formula **verbatim**:

   ```
   FAIL_COUNT = number of MUST requirements failed across both subagents
   WARN_COUNT = number of SHOULD requirements failed
   VERDICT    = FAIL  if (FAIL_COUNT >= 1 OR WARN_COUNT >= 3)
                PASS  otherwise
   ```

   - Report `FAIL_COUNT`, `WARN_COUNT`, and the verdict exactly as above. Do not soften or rewrite the formula.
   - List every MUST violation with `file:line` and the spec §reference (e.g. `§3.1 A2A`, `§6.3 dual audit trail`).
   - Group SHOULD violations under a separate header.
   - End with a "Next steps" block: bullet list of the smallest set of edits that would flip the verdict to PASS, each citing the exact file:line.
   - State clearly that under §12.1 a FAIL verdict **blocks deployment**.

4. **Do not auto-fix.** Compliance is review-only. Offer to open a follow-up turn to fix the violations, but do not edit unless the user says yes.

## Output skeleton

```
COMPLIANCE CHECK — <scope>

FAIL_COUNT (MUST failures): N
WARN_COUNT (SHOULD failures): M
VERDICT: PASS | FAIL    [under §12.1, FAIL blocks deployment]

Per-agent §12.2 checklist (one row per agent reviewed):
  <agent-name>:
    Architecture:   [ ] single function/container  [ ] single stack  [ ] resource limits  [ ] stateless  [ ] layer classified
    Protocols:      [ ] A2A:8444  [ ] MCP:8443 (if applicable)  [ ] REST/gRPC
    AI Gateway:     [ ] all LLM via gateway  [ ] no provider keys  [ ] token usage tracked
    Observability:  [ ] OTEL active  [ ] correlation IDs  [ ] /health + /ready  [ ] business+technical KPIs  [ ] reconfigurable
    Audit:          [ ] CAT encrypted  [ ] PST anonymised  [ ] every span classified
    Security:       [ ] OAuth2/JWT  [ ] RBAC  [ ] no hardcoded secrets  [ ] TLS 1.3 + AES-256-GCM
    Semantic plane: [ ] rules via SBCA  [ ] no hardcoded logic  [ ] capability advertised
    Tools:          [ ] separate containers  [ ] network access  [ ] independently versioned
    EU AI Act:      [ ] risk classified  [ ] high-risk extras if applicable

MUST violations:
- [§<ref>] <agent>/<file:line> — <one line>
  Fix: <one line>

SHOULD violations:
- ...

Next steps (smallest path to PASS):
1. ...
2. ...
```
