"""LLM composer wrapper for the Communication Agent."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from communication.config import AgentConfig


_COMPOSE_SCHEMA: dict[str, Any] = {
    "name": "CommunicationMessage",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["subject", "body", "cta"],
        "properties": {
            "subject": {"type": "string"},
            "body":    {"type": "string"},
            "cta":     {"type": "string"},
        },
    },
}


def build_chat_agent(cfg: AgentConfig, gateway: GatewayClient, system_prompt: str) -> ChatAgent:
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    return ChatAgent(
        chat_client=chat_client, instructions=system_prompt,
        name="communication-composer",
        description="Composes audience-tailored incident updates",
    )


async def compose_with_gateway(
    *, gateway: GatewayClient, cfg: AgentConfig,
    system_prompt: str, user_prompt: str,
) -> dict[str, Any]:
    resp = await gateway.chat_completion(
        model=cfg.model.alias,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=cfg.model.temperature,
        max_tokens=cfg.model.max_tokens,
        response_format={"type": "json_schema", "json_schema": _COMPOSE_SCHEMA},
    )
    return json.loads(resp.text or "{}")
