"""Client for the Strategic Business Context Agent (SBCA).

Two capabilities exposed by the SBCA (collocated with the Capability Registry
in Phase 1):

* ``semantic.query_rule`` — fetch a business rule. Hard-fail on error per §5;
  the agent's task transitions to FAILED and no fallback is applied.
* ``capability_registry.register`` / ``…/deregister`` — agent self-registers
  on startup, deregisters on shutdown.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from a2a_client import A2AClient, A2AClientError
from di_framework_core import SemanticPlaneError


class CapabilityAdvertisement(BaseModel):
    name: str
    url: str
    version: str = "0.1.0"
    skills: list[str] = Field(default_factory=list)


class SemanticClient:
    def __init__(self, base_url: str, *, bearer_provider=None, timeout: float = 10.0) -> None:
        self._base_url = base_url
        self._bearer_provider = bearer_provider
        self._timeout = timeout

    async def _client(self) -> A2AClient:
        token = None
        if self._bearer_provider is not None:
            token = await self._bearer_provider()
        return A2AClient(self._base_url, bearer=token, timeout=self._timeout)

    async def query_rule(
        self,
        *,
        domain: str,
        context: dict[str, Any] | None = None,
        process: str | None = None,
        step: str | None = None,
    ) -> Any:
        """Return the rule value, or raise SemanticPlaneError. Never falls back."""
        client = await self._client()
        try:
            task = await client.message_send(
                capability="semantic.query_rule",
                parts=[{"kind": "data", "data": {"domain": domain, "context": context or {}}}],
                process=process,
                step=step,
            )
        except A2AClientError as exc:
            raise SemanticPlaneError(f"SBCA unreachable for rule '{domain}': {exc}") from exc

        if task.status.state.value != "completed":
            reason = task.metadata.di.reason or task.status.state
            raise SemanticPlaneError(f"SBCA returned non-completed state '{reason}' for rule '{domain}'")

        if not task.artifacts:
            raise SemanticPlaneError(f"SBCA returned no artifact for rule '{domain}'")
        artifact = task.artifacts[0]
        for part in artifact.parts:
            if part.kind == "data":
                value = part.data.get("value")  # type: ignore[attr-defined]
                if value is not None:
                    return value
        raise SemanticPlaneError(f"SBCA artifact missing 'value' field for rule '{domain}'")

    async def register(self, ad: CapabilityAdvertisement) -> None:
        client = await self._client()
        try:
            await client.message_send(
                capability="capability_registry.register",
                parts=[{"kind": "data", "data": ad.model_dump()}],
            )
        except A2AClientError as exc:
            raise SemanticPlaneError(f"Capability registration failed: {exc}") from exc

    async def deregister(self, name: str) -> None:
        client = await self._client()
        try:
            await client.message_send(
                capability="capability_registry.deregister",
                parts=[{"kind": "data", "data": {"name": name}}],
            )
        except A2AClientError as exc:
            # Deregister failures should not crash shutdown — log and continue.
            raise SemanticPlaneError(f"Capability deregistration failed: {exc}") from exc
