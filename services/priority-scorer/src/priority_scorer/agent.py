"""MS Agent Framework wrapper for the impact-narrative LLM step.

The Priority Scorer overall is a deterministic chain, but step 3 (impact +
urgency estimation from free text) is the one LLM call. We model it with
the same ChatAgent shape as elsewhere so a future evaluator-optimizer
upgrade (re-asking the model when blast-radius and narrative disagree)
slots in without rewiring transport.
"""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from priority_scorer.config import AgentConfig

_IMPACT_SCHEMA: dict[str, Any] = {
    "name": "ImpactEstimate",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["impact", "urgency", "reasoning"],
        "properties": {
            "impact":  {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "urgency": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
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
        name="priority-impact-estimator",
        description="LLM step of the Priority Scorer chain",
    )


async def estimate_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _IMPACT_SCHEMA},
    )
    return json.loads(resp.text or "{}")
