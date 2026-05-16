"""Seed Qdrant with synthetic historical incidents so the duplicate-detection
step has something to match against on first boot.

Uses LiteLLM (via gateway_client shape) for embeddings — keeps the seeding
path consistent with the runtime path. Requires:
  * docker compose up (LiteLLM, Qdrant, Keycloak healthy)
  * .env.local with valid Azure Foundry credentials
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


_HISTORICAL: list[dict] = [
    {
        "incident_id": "INC-HIST001",
        "title": "VPN drops every few minutes for sales users",
        "service": "vpn",
        "category": "network",
        "reporter_department": "sales",
        "resolution_summary": "Cisco AnyConnect MTU mismatch after firewall upgrade — reverted firewall rule.",
        "text": "VPN keeps disconnecting every couple of minutes, Cisco AnyConnect, sales department",
        "created_at": 1731200000,
    },
    {
        "incident_id": "INC-HIST002",
        "title": "Salesforce SSO returns AADSTS50105",
        "service": "salesforce",
        "category": "application",
        "reporter_department": "finance",
        "resolution_summary": "Conditional Access group membership lapsed; readded affected users.",
        "text": "Cannot log in to Salesforce, SSO error AADSTS50105 not assigned to a role",
        "created_at": 1731300000,
    },
    {
        "incident_id": "INC-HIST003",
        "title": "Office wifi drops in conference rooms",
        "service": "wifi",
        "category": "network",
        "reporter_department": "engineering",
        "resolution_summary": "AP firmware downgrade resolved roaming issue on 5GHz channel.",
        "text": "Wifi keeps dropping in meeting rooms, especially during video calls",
        "created_at": 1731400000,
    },
    {
        "incident_id": "INC-HIST004",
        "title": "Internal GitHub Enterprise 403s for platform-team org",
        "service": "github-enterprise",
        "category": "application",
        "reporter_department": "engineering",
        "resolution_summary": "SAML group sync delayed after IdP change; re-sync resolved.",
        "text": "Getting 403 errors from GitHub Enterprise platform-team org, other repos fine",
        "created_at": 1731500000,
    },
    {
        "incident_id": "INC-HIST005",
        "title": "Floor 4 colour printer offline",
        "service": "print-services",
        "category": "endpoint",
        "reporter_department": "operations",
        "resolution_summary": "Print server queue stuck — restarted spooler.",
        "text": "HP colour printer 4F-CP01 offline, need it for client packets",
        "created_at": 1731600000,
    },
    {
        "incident_id": "INC-HIST006",
        "title": "Okta redirect loop for SaaS apps",
        "service": "okta-sso",
        "category": "security",
        "reporter_department": "marketing",
        "resolution_summary": "Misconfigured authorization server URI after Okta upgrade.",
        "text": "Okta SSO sends me into a redirect loop when accessing Workday and other SaaS",
        "created_at": 1731700000,
    },
    {
        "incident_id": "INC-HIST007",
        "title": "CEO laptop unresponsive before board meeting",
        "service": "macbook-fleet",
        "category": "endpoint",
        "reporter_department": "executive",
        "resolution_summary": "Battery drained below recovery threshold; trickle-charged.",
        "text": "MacBook completely unresponsive, board meeting in 90 minutes",
        "created_at": 1731800000,
    },
]


async def main() -> None:
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    gateway_url = os.environ.get("AI_GATEWAY_URL", "http://localhost:4000")
    gateway_token = os.environ["GATEWAY_TOKEN"]   # client-credentials JWT; fetch separately
    embedding_alias = os.environ.get("EMBEDDING_ALIAS", "di-embedding")
    collection = os.environ.get("QDRANT_COLLECTION", "historical_incidents")

    collections_spec = Path(__file__).resolve().parents[1] / "infra" / "qdrant" / "collections.yaml"
    print(f"collections.yaml: {collections_spec}")

    async with httpx.AsyncClient(timeout=60.0) as http:
        embed_resp = await http.post(
            f"{gateway_url.rstrip('/')}/v1/embeddings",
            json={"model": embedding_alias, "input": [item["text"] for item in _HISTORICAL]},
            headers={"Authorization": f"Bearer {gateway_token}"},
        )
        embed_resp.raise_for_status()
        vectors = [d["embedding"] for d in embed_resp.json()["data"]]

    client = AsyncQdrantClient(url=qdrant_url)
    try:
        await client.get_collection(collection)
    except Exception:
        await client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )

    points = [
        PointStruct(id=idx + 1, vector=vec, payload={k: v for k, v in item.items() if k != "text"})
        for idx, (item, vec) in enumerate(zip(_HISTORICAL, vectors, strict=True))
    ]
    await client.upsert(collection_name=collection, points=points)
    print(f"Seeded {len(points)} incidents into {collection}")


if __name__ == "__main__":
    asyncio.run(main())
