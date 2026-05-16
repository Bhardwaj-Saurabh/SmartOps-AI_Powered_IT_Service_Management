"""LLM re-rank step for the Knowledge Search Agent."""
from __future__ import annotations

import json
from typing import Any

from agent_framework import ChatAgent
from agent_framework.openai import OpenAIChatClient

from gateway_client import GatewayClient

from knowledge_search.config import AgentConfig


_RERANK_SCHEMA: dict[str, Any] = {
    "name": "KnowledgeRanking",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranked", "applicability_summary"],
        "properties": {
            "applicability_summary": {"type": "string"},
            "ranked": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["article_id", "relevance_score", "reasoning"],
                    "properties": {
                        "article_id":      {"type": "string"},
                        "relevance_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "reasoning":       {"type": "string"},
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
        chat_client=chat_client, instructions=system_prompt,
        name="knowledge-search-reranker",
        description="LLM re-rank step over candidate articles",
    )


async def rerank_with_gateway(
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
        response_format={"type": "json_schema", "json_schema": _RERANK_SCHEMA},
    )
    return json.loads(resp.text or "{}")
