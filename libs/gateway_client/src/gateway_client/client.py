"""LiteLLM AI Gateway client.

Speaks OpenAI's HTTP shape directly (no ``openai`` SDK needed — keeps the
dependency surface tight). Carries:

* ``Authorization: Bearer <agent JWT>``  — LiteLLM is configured to verify the
  Keycloak JWT and meter per-agent on the ``azp`` claim.
* ``X-Correlation-Id``                  — propagated end-to-end.
* ``traceparent``                       — W3C trace context.

Retries 3× with exponential backoff on transient failures (5xx / network).
A 4xx raises immediately with the LiteLLM error body so callers don't waste
retries on bad inputs.
"""
from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from di_framework_core import (
    CORRELATION_ID_HEADER,
    GatewayError,
    current_correlation_id,
)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, int] | None = None

    @property
    def text(self) -> str:
        if not self.choices:
            return ""
        msg = self.choices[0].get("message") or {}
        return msg.get("content") or ""


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    data: list[dict[str, Any]]
    usage: dict[str, int] | None = None

    @property
    def vectors(self) -> list[list[float]]:
        return [item["embedding"] for item in self.data]


@dataclass
class GatewayClient:
    """One instance per agent process. Reuses a single httpx.AsyncClient pool."""

    base_url: str = os.environ.get("AI_GATEWAY_URL", "http://litellm:4000")
    bearer_provider: Any = None  # async callable () -> str returning the agent's JWT
    max_retries: int = 3
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.bearer_provider is not None:
            token = await self.bearer_provider()
            if token:
                h["Authorization"] = f"Bearer {token}"
        cid = current_correlation_id()
        if cid:
            h[CORRELATION_ID_HEADER] = cid
        return h

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        client = await self._client()
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.post(path, json=body, headers=await self._headers())
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_err = exc
                await asyncio.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 500:
                last_err = GatewayError(f"Gateway 5xx ({resp.status_code}): {resp.text[:500]}")
                await asyncio.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise GatewayError(f"Gateway {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise GatewayError(f"Gateway failed after {self.max_retries} attempts: {last_err}")

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[ChatMessage] | list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                m.model_dump(exclude_none=True) if isinstance(m, ChatMessage) else m for m in messages
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if response_format is not None:
            body["response_format"] = response_format
        return ChatResponse.model_validate(await self._post("/v1/chat/completions", body))

    async def embedding(self, *, model: str, input: str | list[str]) -> EmbeddingResponse:
        body: dict[str, Any] = {"model": model, "input": input}
        return EmbeddingResponse.model_validate(await self._post("/v1/embeddings", body))


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter: base 0.5s, cap 8s."""
    cap = min(8.0, 0.5 * (2**attempt))
    return random.uniform(0.0, cap)
