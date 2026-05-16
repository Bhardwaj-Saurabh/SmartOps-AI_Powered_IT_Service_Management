"""Client-credentials JWT fetch for downstream A2A + LiteLLM calls.

Caches the token until just before expiry. Service-account secret comes from
``OIDC_CLIENT_SECRET`` env var, never committed.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from di_framework_core import AgentError


@dataclass
class OIDCTokenProvider:
    token_url: str
    client_id: str
    client_secret: str
    audience: str | None = None
    leeway_seconds: int = 60

    def __post_init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def __call__(self) -> str:
        now = time.time()
        if self._token and now < (self._expires_at - self.leeway_seconds):
            return self._token

        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.audience:
            data["audience"] = self.audience

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.token_url, data=data)
        if resp.status_code != 200:
            raise AgentError(f"Token endpoint {self.token_url} -> {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = now + int(body.get("expires_in", 1800))
        return self._token


def build_default_provider() -> OIDCTokenProvider | None:
    """Build a provider from env. Returns None if DEV bypass is enabled."""
    if os.environ.get("DEV_ALLOW_UNVERIFIED_JWT", "false").lower() == "true":
        return None
    realm_url = os.environ.get("KEYCLOAK_REALM_URL", "")
    client_id = os.environ.get("OIDC_CLIENT_ID", "")
    secret = os.environ.get("OIDC_CLIENT_SECRET", "")
    if not (realm_url and client_id and secret):
        raise AgentError("KEYCLOAK_REALM_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET must be set")
    return OIDCTokenProvider(
        token_url=f"{realm_url.rstrip('/')}/protocol/openid-connect/token",
        client_id=client_id,
        client_secret=secret,
    )
