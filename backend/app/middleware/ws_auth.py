"""WebSocket auth — cookie JWT only (browser cannot send gateway HMAC headers)."""

from __future__ import annotations

import os

import httpx
from jose import JWTError, jwk, jwt

JWT_ISSUER = os.getenv("JWT_ISSUER", "https://auth.robotika.cloud/auth/realms/nekazari")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "account")
JWKS_URL = os.getenv(
    "JWKS_URL",
    "https://auth.robotika.cloud/auth/realms/nekazari/protocol/openid-connect/certs",
)


class JWKSClient:
    def __init__(self, jwks_url: str):
        self.jwks_url = jwks_url
        self._keys: dict = {}

    def get_signing_key(self, kid: str) -> dict:
        if kid not in self._keys:
            self._refresh_keys()
        if kid not in self._keys:
            raise JWTError("Unable to find signing key")
        return self._keys[kid]

    def _refresh_keys(self) -> None:
        resp = httpx.get(self.jwks_url, timeout=10.0)
        resp.raise_for_status()
        for key_data in resp.json().get("keys", []):
            key_kid = key_data.get("kid")
            if key_kid:
                self._keys[key_kid] = key_data


_jwks_client: JWKSClient | None = None


def _jwks() -> JWKSClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = JWKSClient(JWKS_URL)
    return _jwks_client


def verify_websocket_token(token: str) -> None:
    """Raise JWTError when the browser cookie token is invalid."""
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")
    if not kid:
        raise JWTError("Token missing key ID")
    key_data = _jwks().get_signing_key(kid)
    public_key = jwk.construct(key_data)
    jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )


def allowed_origins() -> list[str]:
    """Origins the UI is served from (same ``ALLOWED_ORIGINS`` the CORS middleware uses)."""
    return [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """Gate a WebSocket handshake on the page Origin.

    Browsers always send it and scripts cannot forge it; without this check a
    page on a sibling subdomain (same-site, so the session cookie travels with
    the handshake) could open the socket. Non-browser clients send no Origin
    and carry no ambient cookie, so they pass.
    """
    if origin is None:
        return True
    return origin in allowed
