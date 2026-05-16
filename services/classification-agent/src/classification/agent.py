"""Microsoft Agent Framework ChatAgent wrapper for the LLM classification step.

Anthropic pattern at the workflow level: parallelization. The LLM call here
is one of the two parallel branches (the other is the historical-pattern
matcher). They merge in workflow.py.
"""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from classification.config import AgentConfig


_LABEL_SCHEMA: dict[str, Any] = {
    "name": "IncidentLabel",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["service_area", "category", "confidence", "reasoning"],
        "properties": {
            "service_area": {"type": "string"},
            "category": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reasoning": {"type": "string"},
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
        name="classification-labeller",
        description="LLM step of the Classification Agent's parallel workflow",
    )


async def classify_with_gateway(
    *,
    gateway: GatewayClient,
    cfg: AgentConfig,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """Direct gateway path used by workflow.py's parallel branch."""
    response = await gateway.chat_completion(
        model=cfg.model.alias,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=cfg.model.temperature,
        max_tokens=cfg.model.max_tokens,
        response_format={"type": "json_schema", "json_schema": _LABEL_SCHEMA},
    )
    return json.loads(response.text or "{}")
