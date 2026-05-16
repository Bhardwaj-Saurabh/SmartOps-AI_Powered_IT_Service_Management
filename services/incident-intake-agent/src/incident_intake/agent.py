"""Anthropic pattern: prompt chaining (workflow, not autonomous agent).

The Incident Intake Agent is a deterministic 12-step chain. Step 3 — entity
extraction from a free-text incident report — is the only step that uses an
LLM, and it's modelled here using Microsoft Agent Framework's ChatAgent.

The remaining 11 steps are plain Python in workflow.py — there is no
LLM-driven control flow, so per Anthropic's "Building Effective Agents" they
do not warrant the agent abstraction.
"""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from incident_intake.config import AgentConfig


_EXTRACTION_SCHEMA: dict[str, Any] = {
    "name": "ExtractedIncident",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "reporter", "affected_service", "service_area",
            "symptoms_verbatim", "symptoms_summary", "urgency", "reported_at",
        ],
        "properties": {
            "reporter": {"type": ["string", "null"]},
            "affected_service": {"type": ["string", "null"]},
            "service_area": {"type": ["string", "null"]},
            "symptoms_verbatim": {"type": "string"},
            "symptoms_summary": {"type": "string"},
            "urgency": {"type": ["string", "null"], "enum": ["low", "medium", "high", "critical", None]},
            "reported_at": {"type": ["string", "null"]},
        },
    },
}


def build_chat_agent(cfg: AgentConfig, gateway: GatewayClient, system_prompt: str) -> ChatAgent:
    """Build the MS Agent Framework ChatAgent used by workflow step 3.

    The ``OpenAIChatClient`` is pointed at LiteLLM (OpenAI-compatible). Auth
    headers and correlation propagation actually happen at the gateway_client
    layer; the OpenAIChatClient is used here as a thin LLM-call wrapper.
    """
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        # api_key is ignored when our gateway_client provides the bearer token,
        # but the SDK requires a non-empty value.
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    return ChatAgent(
        chat_client=chat_client,
        instructions=system_prompt,
        name="incident-intake-extractor",
        description="Extracts canonical incident fields from raw report text",
    )


async def extract_with_gateway(
    *,
    gateway: GatewayClient,
    cfg: AgentConfig,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any]:
    """LLM call used when running outside of the Agent Framework path.

    This is the actual code path the workflow uses today: a single LLM call
    with json_schema response_format. The ChatAgent above exists so the
    project remains conformant to the MS Agent Framework requirement and can
    grow into a multi-turn pattern (e.g. tool-augmented intake) without
    rewiring transport.
    """
    response = await gateway.chat_completion(
        model=cfg.model.alias,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=cfg.model.temperature,
        max_tokens=cfg.model.max_tokens,
        response_format={"type": "json_schema", "json_schema": _EXTRACTION_SCHEMA},
    )
    text = response.text or "{}"
    return json.loads(text)
