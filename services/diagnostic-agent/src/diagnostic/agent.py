"""LLM-call wrappers for the Diagnostic Agent's evaluator-optimizer loop.

Two distinct roles, each modelled as its own ChatAgent so the role boundary
is explicit in the code (and so a future change can swap the evaluator to a
different model):

* Generator — produces a structured hypothesis with candidate causes.
* Evaluator — scores a hypothesis given freshly-collected validation evidence.
"""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from diagnostic.config import AgentConfig


_HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "name": "DiagnosticHypothesis",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_causes", "best_index"],
        "properties": {
            "candidate_causes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["cause", "cause_type", "evidence", "validation_idea"],
                    "properties": {
                        "cause":      {"type": "string"},
                        "cause_type": {"type": "string", "enum": ["infrastructure", "application", "configuration", "external"]},
                        "evidence":   {"type": "array", "items": {"type": "string"}},
                        "validation_idea": {"type": "string"},
                    },
                },
            },
            "best_index": {"type": "integer"},
        },
    },
}


_EVAL_SCHEMA: dict[str, Any] = {
    "name": "HypothesisEvaluation",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence", "supported", "reasoning"],
        "properties": {
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "supported":  {"type": "boolean"},
            "reasoning":  {"type": "string"},
        },
    },
}


def build_generator_agent(cfg: AgentConfig, gateway: GatewayClient, system_prompt: str) -> ChatAgent:
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    return ChatAgent(
        chat_client=chat_client, instructions=system_prompt,
        name="diagnostic-generator",
        description="Generator side of the evaluator-optimizer loop",
    )


def build_evaluator_agent(cfg: AgentConfig, gateway: GatewayClient, system_prompt: str) -> ChatAgent:
    chat_client = OpenAIChatClient(
        base_url=f"{gateway.base_url.rstrip('/')}/v1",
        api_key="placeholder-litellm-token",
        model_id=cfg.model.alias,
    )
    return ChatAgent(
        chat_client=chat_client, instructions=system_prompt,
        name="diagnostic-evaluator",
        description="Evaluator side of the evaluator-optimizer loop",
    )


async def generate_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _HYPOTHESIS_SCHEMA},
    )
    return json.loads(resp.text or "{}")


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
        temperature=0.0,                    # deterministic scoring
        max_tokens=512,
        response_format={"type": "json_schema", "json_schema": _EVAL_SCHEMA},
    )
    return json.loads(resp.text or "{}")
