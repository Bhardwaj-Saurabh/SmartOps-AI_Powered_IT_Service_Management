"""Script executor sidecar (synthetic).

Phase 1 simulates remediation steps. A real production sidecar would shell
out to SSH or invoke vendor APIs — the executor's interface contract is the
same, so swapping the backend is a one-file change.

For test/demo purposes the executor can be told to fail at step N by setting
``SIMULATE_RUNBOOK_FAILURE_AT_STEP`` on the container.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="script-executor", version="0.1.0")

_RB_PATH = os.environ.get("RUNBOOKS_PATH", "/app/data/runbooks.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_RB_PATH).read_text()) or {}
_SIM_FAIL_STEP = int(os.environ.get("SIMULATE_RUNBOOK_FAILURE_AT_STEP", "-1"))


def _by_id(rid: str) -> dict[str, Any] | None:
    for rb in _corpus.get("runbooks") or []:
        if rb.get("id") == rid:
            return rb
    return None


class CatalogueResponse(BaseModel):
    runbooks: list[dict[str, Any]]


class ExecuteRequest(BaseModel):
    runbook_id: str
    parameters: dict[str, Any]
    snapshot_id: str | None = None      # passed by Automated Fix Agent for audit


class StepResult(BaseModel):
    step_index: int
    action: str
    outcome: str
    duration_ms: float
    detail: str | None = None


class ExecuteResponse(BaseModel):
    runbook_id: str
    overall_outcome: str        # succeeded | failed | partial
    steps: list[StepResult]
    parameters_used: dict[str, Any]


@app.get("/catalogue", response_model=CatalogueResponse)
async def catalogue() -> CatalogueResponse:
    # Drop internal-only fields from the catalogue surface so we never
    # leak operator-private metadata to the calling agent.
    public: list[dict[str, Any]] = []
    for rb in _corpus.get("runbooks") or []:
        public.append({
            "id": rb.get("id"),
            "title": rb.get("title"),
            "fix_type": rb.get("fix_type"),
            "applicable_to": rb.get("applicable_to", []),
            "param_schema": rb.get("param_schema", {}),
            "estimated_duration_seconds": rb.get("estimated_duration_seconds", 0),
            "step_count": len(rb.get("steps", []) or []),
        })
    return CatalogueResponse(runbooks=public)


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    rb = _by_id(req.runbook_id)
    if rb is None:
        raise HTTPException(status_code=404, detail=f"Unknown runbook: {req.runbook_id}")

    # Parameter validation: required keys present.
    schema = rb.get("param_schema") or {}
    missing = [k for k, spec in schema.items() if spec.get("required") and k not in req.parameters]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required parameters: {missing}")

    results: list[StepResult] = []
    overall = "succeeded"
    for idx, step in enumerate(rb.get("steps") or []):
        action = str(step.get("action", "?"))
        # Simulated work — small randomised delay so traces look realistic.
        sleep_ms = random.uniform(40, 200)
        time.sleep(sleep_ms / 1000.0)
        if _SIM_FAIL_STEP == idx:
            results.append(StepResult(step_index=idx, action=action, outcome="failed",
                                      duration_ms=sleep_ms, detail="SIMULATE_RUNBOOK_FAILURE_AT_STEP triggered"))
            overall = "failed"
            break
        results.append(StepResult(step_index=idx, action=action, outcome=str(step.get("expected_outcome", "success")),
                                  duration_ms=sleep_ms))
    return ExecuteResponse(
        runbook_id=req.runbook_id,
        overall_outcome=overall,
        steps=results,
        parameters_used=req.parameters,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "healthy", "tool": "script-executor", "version": "0.1.0",
        "runbook_count": len(_corpus.get("runbooks") or []),
        "simulated_failure_step": _SIM_FAIL_STEP,
    }
