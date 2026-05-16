"""LLM systemic-pattern assessment wrapper for the Problem Linker."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from problem_linker.config import AgentConfig


_ASSESS_SCHEMA: dict[str, Any] = {
    "name": "SystemicAssessment",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["is_systemic", "confidence", "reasoning",
                     "recommended_problem_title", "recurrence_pattern", "scope_note"],
        "properties": {
            "is_systemic":               {"type": "boolean"},
            "confidence":                {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning":                 {"type": "string"},
            "recommended_problem_title": {"type": "string"},
            "recurrence_pattern":        {"type": "string"},
            "scope_note":                {"type": "string"},
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
        name="problem-linker-assessor",
        description="Assesses whether an incident cluster indicates a systemic problem",
    )


async def assess_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _ASSESS_SCHEMA},
    )
    return json.loads(resp.text or "{}")
