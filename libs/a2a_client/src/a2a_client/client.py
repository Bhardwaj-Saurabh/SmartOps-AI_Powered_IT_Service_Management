"""Spec-native Google A2A client.

Discovers the remote Agent Card, sends JSON-RPC ``message/send`` / ``tasks/get``
calls, threads the DI envelope (capability, correlation_id, process, step)
through ``Message.metadata.di``, and propagates JWT + correlation headers.

No SSE streaming yet — added when the first orchestrator needs it.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from di_framework_core import (
    CORRELATION_ID_HEADER,
    DIEnvelope,
    current_correlation_id,
    ensure_correlation_id,
)

from a2a_server.models import AgentCard, Message, Task


class A2AClientError(RuntimeError):
    pass


class A2AClient:
    """Async client. One instance per remote agent.

    Args:
      base_url:   Agent's root URL, e.g. ``http://incident-intake:8444``
      bearer:     Caller's OAuth2 token (verified by remote). Required outside dev.
      timeout:    Per-call timeout in seconds.

    Two construction paths:
      * ``A2AClient(url, bearer=...)``                       — direct, URL known up front.
      * ``await A2AClient.from_capability("name", ...)``    — capability-based discovery
        via the Capability Registry. Use this in service-layer orchestrators so they
        don't hardcode tactical-agent URLs.
    """

    def __init__(self, base_url: str, *, bearer: str | None = None, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer = bearer
        self._timeout = timeout
        self._card: AgentCard | None = None

    @classmethod
    async def from_capability(
        cls,
        capability_name: str,
        *,
        registry_url: str,
        bearer: str | None = None,
        timeout: float = 30.0,
    ) -> "A2AClient":
        """Resolve a capability via the Capability Registry, return a client
        bound to the URL it currently advertises.

        Service-layer orchestrators use this to compose workflows over the 12
        tactical agents without hardcoding their URLs — the framework's
        capability-registry indirection (§5.1) made tangible.

        Raises:
          A2AClientError: capability is not registered, has no URL, or the
            registry itself is unreachable.
        """
        registry = cls(registry_url, bearer=bearer, timeout=timeout)
        task = await registry.message_send(
            capability="capability_registry.lookup",
            parts=[{"kind": "data", "data": {"name": capability_name}}],
        )
        if not task.artifacts:
            raise A2AClientError(
                f"Capability registry returned no artifact for '{capability_name}'"
            )
        entry: dict[str, Any] | None = None
        for part in task.artifacts[0].parts:
            if part.kind == "data":
                entry = part.data.get("entry")
                break
        if entry is None:
            raise A2AClientError(f"Capability '{capability_name}' is not registered")
        url = entry.get("url")
        if not url:
            raise A2AClientError(
                f"Capability '{capability_name}' is registered without a URL: {entry}"
            )
        return cls(url, bearer=bearer, timeout=timeout)

    async def agent_card(self) -> AgentCard:
        if self._card is not None:
            return self._card
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
                resp = await client.get(f"{self._base_url}{path}")
                if resp.status_code == 200:
                    self._card = AgentCard.model_validate(resp.json())
                    return self._card
        raise A2AClientError(f"Could not fetch Agent Card from {self._base_url}")

    def _headers(self) -> dict[str, str]:
        cid = current_correlation_id() or ensure_correlation_id()
        h = {"Content-Type": "application/json", CORRELATION_ID_HEADER: cid}
        if self._bearer:
            h["Authorization"] = f"Bearer {self._bearer}"
        return h

    async def message_send(
        self,
        *,
        capability: str,
        parts: list[dict[str, Any]],
        process: str | None = None,
        step: str | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> Task:
        cid = ensure_correlation_id()
        message = Message.model_validate(
            {
                "role": "user",
                "parts": parts,
                "contextId": context_id,
                "taskId": task_id,
                "metadata": {
                    "di": DIEnvelope(
                        capability=capability,
                        correlation_id=cid,
                        process=process,
                        step=step,
                    ).model_dump(exclude_none=True)
                },
            }
        )
        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": str(uuid4()),
            "params": {"message": message.model_dump(mode="json", exclude_none=True)},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._base_url, json=payload, headers=self._headers())
            resp.raise_for_status()
            body = resp.json()
        if body.get("error"):
            raise A2AClientError(f"A2A error: {body['error']}")
        return Task.model_validate(body["result"])

    async def tasks_get(self, task_id: str) -> Task:
        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/get",
            "id": str(uuid4()),
            "params": {"id": task_id},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._base_url, json=payload, headers=self._headers())
            resp.raise_for_status()
            body = resp.json()
        if body.get("error"):
            raise A2AClientError(f"A2A error: {body['error']}")
        return Task.model_validate(body["result"])
