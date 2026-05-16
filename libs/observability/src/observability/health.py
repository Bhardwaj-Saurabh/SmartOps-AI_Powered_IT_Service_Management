"""Health (`/health`) and readiness (`/ready`) endpoints per DI AI Framework §6.2.

``/ready`` MUST probe the AI Gateway and the semantic plane. Pass async
probes (returning bool) at mount time. A failing probe → HTTP 503.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Response, status

Probe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class HealthCheck:
    service: str
    version: str
    probes: dict[str, Probe]


def mount_health(app: FastAPI, hc: HealthCheck) -> None:
    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "healthy", "service": hc.service, "version": hc.version}

    @app.get("/ready")
    async def _ready(response: Response) -> dict[str, object]:
        results: dict[str, bool] = {}
        for name, probe in hc.probes.items():
            try:
                results[name] = await probe()
            except Exception:
                results[name] = False
        ready = all(results.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready else "not_ready", "checks": results}
