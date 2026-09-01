"""Cognito access-token validation at Sherlock's HTTP boundary."""

from __future__ import annotations

import time
from typing import Any, NoReturn, cast

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import HTTPException, status
from jwt.algorithms import RSAAlgorithm


class CognitoTokenVerifier:
    """Verify access tokens using the configured Cognito issuer's JWKS."""

    def __init__(
        self, issuer: str, client_id: str, *, jwks_ttl_seconds: int = 3600
    ) -> None:
        self._issuer = issuer
        self._client_id = client_id
        self._jwks_url = f"{issuer}/.well-known/jwks.json"
        self._jwks_ttl_seconds = jwks_ttl_seconds
        self._keys: dict[str, dict[str, Any]] = {}
        self._keys_expires_at = 0.0

    async def verify_authorization(self, authorization: str | None) -> dict[str, Any]:
        """Return verified claims or raise a deliberately non-specific 401."""

        if not authorization:
            self._reject()
        assert authorization is not None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            self._reject()

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                self._reject()
            key = await self._key(header["kid"])
            claims = jwt.decode(
                token,
                cast(RSAPublicKey, RSAAlgorithm.from_jwk(key)),
                algorithms=["RS256"],
                issuer=self._issuer,
                options={"verify_aud": False},
            )
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError):
            self._reject()

        if (
            claims.get("token_use") != "access"
            or claims.get("client_id") != self._client_id
        ):
            self._reject()
        return claims

    async def _key(self, key_id: str) -> dict[str, Any]:
        if time.monotonic() >= self._keys_expires_at or key_id not in self._keys:
            await self._refresh_keys()
        key = self._keys.get(key_id)
        if key is None:
            self._reject()
        assert key is not None
        return key

    async def _refresh_keys(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._jwks_url)
            response.raise_for_status()
        payload = response.json()
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise TypeError("Cognito JWKS did not contain keys")
        self._keys = {
            key["kid"]: key
            for key in keys
            if isinstance(key, dict) and isinstance(key.get("kid"), str)
        }
        self._keys_expires_at = time.monotonic() + self._jwks_ttl_seconds

    @staticmethod
    def _reject() -> NoReturn:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
