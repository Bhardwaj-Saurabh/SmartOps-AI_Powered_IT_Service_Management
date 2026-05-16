"""LLM note-composer wrapper for the Resolution Documenter."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from resolution_documenter.config import AgentConfig


_NOTE_SCHEMA: dict[str, Any] = {
    "name": "ResolutionNote",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "root_cause", "fix_summary", "prevention", "validation",
                     "symptoms_seen_by_user", "applicable_services", "applicable_keywords"],
        "properties": {
            "title":                {"type": "string"},
            "root_cause":           {"type": "string"},
            "fix_summary":          {"type": "string"},
            "prevention":           {"type": "string"},
            "validation":           {"type": "string"},
            "symptoms_seen_by_user": {"type": "string"},
            "applicable_services":  {"type": "array", "items": {"type": "string"}},
            "applicable_keywords":  {"type": "array", "items": {"type": "string"}},
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
        name="resolution-documenter-composer",
        description="Composes structured resolution-note JSON",
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
        response_format={"type": "json_schema", "json_schema": _NOTE_SCHEMA},
    )
    return json.loads(resp.text or "{}")
