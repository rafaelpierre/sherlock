from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from sherlock.api.app import create_app
from sherlock.api.auth import CognitoTokenVerifier

ISSUER = "https://cognito-idp.eu-west-2.amazonaws.com/pool"
CLIENT_ID = "client-id"


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(signing_key: rsa.RSAPrivateKey) -> CognitoTokenVerifier:
    verifier = CognitoTokenVerifier(ISSUER, CLIENT_ID)
    public_jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    verifier._keys = {"test-key": {**public_jwk, "kid": "test-key", "alg": "RS256"}}
    verifier._keys_expires_at = time.monotonic() + 60
    return verifier


def access_token(signing_key: rsa.RSAPrivateKey, **claims: object) -> str:
    return jwt.encode(
        {
            "iss": ISSUER,
            "client_id": CLIENT_ID,
            "token_use": "access",
            "exp": int(time.time()) + 60,
            **claims,
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


@pytest.mark.asyncio
async def test_verifier_accepts_configured_cognito_access_token(
    verifier: CognitoTokenVerifier, signing_key: rsa.RSAPrivateKey
) -> None:
    claims = await verifier.verify_authorization(f"Bearer {access_token(signing_key)}")

    assert claims["client_id"] == CLIENT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic token", "Bearer malformed"],
)
async def test_verifier_rejects_missing_or_malformed_bearer_token(
    verifier: CognitoTokenVerifier, authorization: str | None
) -> None:
    with pytest.raises(HTTPException, match="Authentication is required") as exc_info:
        await verifier.verify_authorization(authorization)

    assert exc_info.value.status_code == 401


def test_protected_routes_reject_before_the_route_can_process_a_request() -> None:
    class RejectingVerifier:
        async def verify_authorization(
            self, authorization: str | None
        ) -> dict[str, object]:
            raise HTTPException(
                status_code=401,
                detail="Authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    app = create_app()
    with TestClient(app) as client:
        app.state.auth_verifier = RejectingVerifier()
        response = client.post("/v1/query", json={"question": ""})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Authentication is required."}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 1},
        {"iss": "https://wrong.example/pool"},
        {"client_id": "wrong-client"},
        {"token_use": "id"},
    ],
)
async def test_verifier_rejects_invalid_access_token_claims(
    verifier: CognitoTokenVerifier,
    signing_key: rsa.RSAPrivateKey,
    claims: dict[str, object],
) -> None:
    with pytest.raises(HTTPException, match="Authentication is required") as exc_info:
        await verifier.verify_authorization(
            f"Bearer {access_token(signing_key, **claims)}"
        )

    assert exc_info.value.status_code == 401
