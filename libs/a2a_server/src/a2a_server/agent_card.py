"""Serve the Agent Card at /.well-known/agent-card.json (Google A2A spec).

Also serves the legacy path /.well-known/agent.json for older clients during
the spec rename transition.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from a2a_server.models import AgentCard


@dataclass(frozen=True)
class AgentCardSpec:
    card: AgentCard


def build_agent_card_router(spec: AgentCardSpec) -> APIRouter:
    router = APIRouter()
    card_json = spec.card.model_dump(mode="json", exclude_none=True)

    @router.get("/.well-known/agent-card.json", include_in_schema=False)
    async def _card() -> dict:
        return card_json

    @router.get("/.well-known/agent.json", include_in_schema=False)
    async def _card_legacy() -> dict:
        return card_json

    return router
