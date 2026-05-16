"""MS Agent Framework wrapper for the LLM ranking step."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from routing.config import AgentConfig

_RANK_SCHEMA: dict[str, Any] = {
    "name": "RankedTeams",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranked"],
        "properties": {
            "ranked": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["team_id", "score", "reasoning"],
                    "properties": {
                        "team_id":   {"type": "string"},
                        "score":     {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "reasoning": {"type": "string"},
                    },
                },
            },
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
        chat_client=chat_client,
        instructions=system_prompt,
        name="routing-ranker",
        description="LLM step of the Routing Agent's parallel workflow",
    )


async def rank_with_gateway(
    *,
    gateway: GatewayClient,
    cfg: AgentConfig,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    resp = await gateway.chat_completion(
        model=cfg.model.alias,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=cfg.model.temperature,
        max_tokens=cfg.model.max_tokens,
        response_format={"type": "json_schema", "json_schema": _RANK_SCHEMA},
    )
    return json.loads(resp.text or "{}")
