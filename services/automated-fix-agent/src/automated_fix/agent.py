"""LLM call wrappers for the Automated Fix Agent — runbook selection + post-fix summary."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from automated_fix.config import AgentConfig


_SELECT_SCHEMA: dict[str, Any] = {
    "name": "RunbookSelection",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["selected_runbook_id", "parameters", "rationale"],
        "properties": {
            "selected_runbook_id": {"type": ["string", "null"]},
            "parameters":          {"type": "object"},
            "rationale":           {"type": "string"},
        },
    },
}

_SUMMARY_SCHEMA: dict[str, Any] = {
    "name": "FixSummary",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["what_changed", "changed_resources", "user_visible_impact"],
        "properties": {
            "what_changed":         {"type": "string"},
            "changed_resources":    {"type": "array", "items": {"type": "string"}},
            "user_visible_impact":  {"type": "string"},
        },
    },
}


def build_chat_agents(cfg: AgentConfig, gateway: GatewayClient,
                      select_system: str, summary_system: str) -> tuple[ChatAgent, ChatAgent]:
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    selector = ChatAgent(chat_client=chat_client, instructions=select_system,
                         name="automated-fix-selector",
                         description="Picks a runbook from the catalogue")
    summariser = ChatAgent(chat_client=chat_client, instructions=summary_system,
                           name="automated-fix-summariser",
                           description="Summarises what the fix changed")
    return selector, summariser


async def select_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _SELECT_SCHEMA},
    )
    return json.loads(resp.text or "{}")


async def summarise_with_gateway(
    *, gateway: GatewayClient, cfg: AgentConfig,
    system_prompt: str, user_prompt: str,
) -> dict[str, Any]:
    resp = await gateway.chat_completion(
        model=cfg.model.alias,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=512,
        response_format={"type": "json_schema", "json_schema": _SUMMARY_SCHEMA},
    )
    return json.loads(resp.text or "{}")
