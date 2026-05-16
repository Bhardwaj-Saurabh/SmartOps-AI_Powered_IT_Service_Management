"""LLM evaluator wrapper for the Verification Agent."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from verification.config import AgentConfig


_VERIFY_SCHEMA: dict[str, Any] = {
    "name": "VerificationVerdict",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["fix_verified", "confidence", "reasoning", "residual_concerns"],
        "properties": {
            "fix_verified":      {"type": "boolean"},
            "confidence":        {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning":         {"type": "string"},
            "residual_concerns": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def build_evaluator_agent(cfg: AgentConfig, gateway: GatewayClient, system_prompt: str) -> ChatAgent:
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    return ChatAgent(
        chat_client=chat_client, instructions=system_prompt,
        name="verification-evaluator",
        description="Judges whether a fix resolved the symptoms",
    )


async def evaluate_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _VERIFY_SCHEMA},
    )
    return json.loads(resp.text or "{}")
