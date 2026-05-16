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


_KB_ARTICLES: list[dict] = [
    {
        "article_id": "KB-001",
        "title": "AnyConnect VPN session drops after firewall MTU change",
        "category": "vpn",
        "service": "vpn",
        "effectiveness_score": 0.92,
        "updated_at_days_ago": 14,
        "text": "AnyConnect VPN sessions drop every 2 minutes after firewall patch reverts MTU. Workaround: set firewall MTU rule 8 to 1492.",
    },
    {
        "article_id": "KB-002",
        "title": "Okta SSO AADSTS50105 — user not assigned to application role",
        "category": "okta-sso",
        "service": "okta-sso",
        "effectiveness_score": 0.90,
        "updated_at_days_ago": 32,
        "text": "AADSTS50105 means the user lacks an application role. Conditional Access SSO-Eligible group sync lapsed; re-add user.",
    },
    {
        "article_id": "KB-003",
        "title": "Salesforce SSO error following Okta upgrade",
        "category": "salesforce",
        "service": "salesforce",
        "effectiveness_score": 0.78,
        "updated_at_days_ago": 58,
        "text": "Salesforce SSO breaks after Okta upgrade because the SAML authorization-server URI changed. Update Salesforce SSO settings.",
    },
    {
        "article_id": "KB-004",
        "title": "Conference-room wifi drops during meetings (5GHz roaming)",
        "category": "wifi",
        "service": "wifi",
        "effectiveness_score": 0.84,
        "updated_at_days_ago": 92,
        "text": "AP firmware revisions cause 5GHz roaming failures. Downgrade affected APs to firmware 8.10.6.",
    },
    {
        "article_id": "KB-005",
        "title": "GitHub Enterprise 403s after SAML group sync delay",
        "category": "github-enterprise",
        "service": "github-enterprise",
        "effectiveness_score": 0.81,
        "updated_at_days_ago": 240,
        "text": "GitHub Enterprise SAML group sync can delay by an hour after IdP changes. Trigger out-of-band sync.",
    },
    {
        "article_id": "KB-006",
        "title": "Print services — HP colour spooler stuck",
        "category": "printer",
        "service": "print-services",
        "effectiveness_score": 0.70,
        "updated_at_days_ago": 7,
        "text": "Print spooler queue stops processing. Restart Print Spooler on the print server; re-add printer.",
    },
]


async def _seed_collection(
    *, name: str, items: list[dict], text_field: str, point_id_prefix: int,
    qdrant_url: str, gateway_url: str, gateway_token: str, embedding_alias: str,
) -> int:
    """Embed via LiteLLM and upsert into Qdrant. Returns the count seeded."""
    async with httpx.AsyncClient(timeout=60.0) as http:
        embed_resp = await http.post(
            f"{gateway_url.rstrip('/')}/v1/embeddings",
            json={"model": embedding_alias, "input": [item[text_field] for item in items]},
            headers={"Authorization": f"Bearer {gateway_token}"},
        )
        embed_resp.raise_for_status()
        vectors = [d["embedding"] for d in embed_resp.json()["data"]]

    client = AsyncQdrantClient(url=qdrant_url)
    try:
        await client.get_collection(name)
    except Exception:
        await client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )

    points = [
        PointStruct(
            id=point_id_prefix + idx + 1,
            vector=vec,
            payload={k: v for k, v in item.items() if k != text_field},
        )
        for idx, (item, vec) in enumerate(zip(items, vectors, strict=True))
    ]
    await client.upsert(collection_name=name, points=points)
    return len(points)


async def main() -> None:
    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    gateway_url = os.environ.get("AI_GATEWAY_URL", "http://localhost:4000")
    gateway_token = os.environ["GATEWAY_TOKEN"]
    embedding_alias = os.environ.get("EMBEDDING_ALIAS", "di-embedding")

    n_inc = await _seed_collection(
        name="historical_incidents", items=_HISTORICAL, text_field="text", point_id_prefix=0,
        qdrant_url=qdrant_url, gateway_url=gateway_url,
        gateway_token=gateway_token, embedding_alias=embedding_alias,
    )
    print(f"Seeded {n_inc} historical incidents")

    n_kb = await _seed_collection(
        name="knowledge_articles", items=_KB_ARTICLES, text_field="text", point_id_prefix=1000,
        qdrant_url=qdrant_url, gateway_url=gateway_url,
        gateway_token=gateway_token, embedding_alias=embedding_alias,
    )
    print(f"Seeded {n_kb} knowledge-base articles")


if __name__ == "__main__":
    asyncio.run(main())
