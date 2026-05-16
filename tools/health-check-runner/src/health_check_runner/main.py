"""Health check runner sidecar (synthetic).

Returns pass/fail per probe. The `healthy_after_fix` flag in checks.yaml
controls outcomes when ``after_fix=true`` so tests can deterministically
exercise both passing and failing verification paths.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="health-check-runner", version="0.1.0")

_CHECKS_PATH = os.environ.get("CHECKS_PATH", "/app/data/checks.yaml")
_corpus: dict[str, Any] = yaml.safe_load(Path(_CHECKS_PATH).read_text()) or {}


class RunRequest(BaseModel):
    service: str
    after_fix: bool = False


class ProbeResult(BaseModel):
    name: str
    kind: str
    target: str | None = None
    passed: bool
    latency_ms: float
    detail: str | None = None


class RunResponse(BaseModel):
    service: str
    matched: bool
    overall_passed: bool
    probes: list[ProbeResult]


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    entry = (_corpus.get("services") or {}).get(req.service.lower())
    if entry is None:
        return RunResponse(service=req.service, matched=False, overall_passed=False, probes=[])
    healthy_after_fix = bool(entry.get("healthy_after_fix", True))
    probes_out: list[ProbeResult] = []
    for probe in entry.get("probes") or []:
        # Pre-fix probes are deterministically failing (we know symptoms exist).
        # Post-fix probes pass when healthy_after_fix is true.
        passed = req.after_fix and healthy_after_fix
        probes_out.append(ProbeResult(
            name=str(probe.get("name", "?")),
            kind=str(probe.get("kind", "?")),
            target=probe.get("target"),
            passed=passed,
            latency_ms=15.0,
            detail=None if passed else ("symptom still present" if not req.after_fix or not healthy_after_fix else None),
        ))
    return RunResponse(
        service=req.service, matched=True,
        overall_passed=all(p.passed for p in probes_out) and bool(probes_out),
        probes=probes_out,
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "tool": "health-check-runner", "version": "0.1.0",
            "services_configured": len(_corpus.get("services") or {})}
