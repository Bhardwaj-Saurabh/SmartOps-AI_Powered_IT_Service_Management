"""JWT verification via Keycloak JWKS for the A2A server.

Per DI AI Framework §7.1 every A2A request carries a Bearer token. We verify
against the Keycloak realm's JWKS and check the ``aud`` claim equals the
agent's OIDC client_id.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status
from jwt.algorithms import RSAAlgorithm


@dataclass
class JWTVerifier:
    """Caches the JWKS and verifies RS256 tokens."""

    jwks_url: str
    issuer: str
    audience: str
    cache_ttl_seconds: int = 600
    _jwks: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _fetched_at: float = field(default=0.0, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def _load_jwks(self) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            if self._jwks and (now - self._fetched_at) < self.cache_ttl_seconds:
                return self._jwks
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.jwks_url)
                resp.raise_for_status()
                self._jwks = resp.json()
                self._fetched_at = now
            return self._jwks

    def _key_for_kid(self, jwks: dict[str, Any], kid: str) -> Any:
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return RSAAlgorithm.from_jwk(key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown signing key")

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Bad token header: {exc}") from exc
        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no kid")
        jwks = await self._load_jwks()
        key = self._key_for_kid(jwks, kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        return claims


@dataclass
class KeycloakAuth:
    """Convenience factory for the Keycloak JWT verifier.

    Set ``dev_allow_unverified=True`` ONLY when Keycloak is unavailable in a
    purely local test loop — never in any built image.
    """

    realm_url: str  # e.g. http://keycloak:8080/realms/smartops
    audience: str
    dev_allow_unverified: bool = False
    _verifier: JWTVerifier | None = field(default=None, init=False, repr=False)

    def verifier(self) -> JWTVerifier | None:
        if self.dev_allow_unverified:
            return None
        if self._verifier is None:
            self._verifier = JWTVerifier(
                jwks_url=f"{self.realm_url.rstrip('/')}/protocol/openid-connect/certs",
                issuer=self.realm_url,
                audience=self.audience,
            )
        return self._verifier


async def extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    return auth.split(" ", 1)[1].strip()
