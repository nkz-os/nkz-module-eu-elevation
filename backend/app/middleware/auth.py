"""
EU Elevation Module - Authentication Middleware

SOTA compliant with Nekazari platform:
- RS256 JWT validation via Keycloak JWKS
- Authorization: Bearer header -> nkz_token cookie fallback
- X-Tenant-ID header priority (from api-gateway)
- TRUST_API_GATEWAY mode for behind-gateway deployments
- ADR 003 system-gateway role support
"""

import os
import time
from typing import Optional

import httpx
from functools import lru_cache
from fastapi import HTTPException, Depends, Header, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, jwk, JWTError


# ── Configuration ──────────────────────────────────────────────────
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak-service:8080/auth")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "nekazari")
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "https://auth.robotika.cloud/auth")
JWT_ISSUER = os.getenv("JWT_ISSUER", f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "account")
JWKS_URL = os.getenv("JWKS_URL", f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs")
TRUST_API_GATEWAY = os.getenv("TRUST_API_GATEWAY", "true").lower() == "true"
SYSTEM_GATEWAY_ROLE = os.getenv("SYSTEM_GATEWAY_ROLE", "urn:nkz:role:system-gateway")

security = HTTPBearer(auto_error=False)


# ── JWKS Client ────────────────────────────────────────────────────
class JWKSClient:
    """Lightweight JWKS client with lazy refresh."""

    def __init__(self, jwks_url: str):
        self.jwks_url = jwks_url
        self._keys: dict = {}

    def get_signing_key(self, kid: str) -> dict:
        if kid not in self._keys:
            self._refresh_keys()
        if kid not in self._keys:
            raise HTTPException(status_code=401, detail="Unable to find signing key")
        return self._keys[kid]

    def _refresh_keys(self):
        resp = httpx.get(self.jwks_url, timeout=10.0)
        resp.raise_for_status()
        for key_data in resp.json().get("keys", []):
            key_kid = key_data.get("kid")
            if key_kid:
                self._keys[key_kid] = key_data


@lru_cache()
def get_jwks_client() -> JWKSClient:
    return JWKSClient(JWKS_URL)


# ── Token Payload ──────────────────────────────────────────────────
class TokenPayload:
    """Typed wrapper around JWT claims."""

    def __init__(self, payload: dict):
        self.sub: str = payload.get("sub", "")
        self.email: str = payload.get("email", "")
        self.preferred_username: str = payload.get("preferred_username", "")
        self.tenant_id: Optional[str] = payload.get("tenant_id")
        self.realm_access: dict = payload.get("realm_access", {})
        self._payload = payload

    @property
    def roles(self) -> list[str]:
        return self.realm_access.get("roles", [])

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_system_gateway_role(self) -> bool:
        return SYSTEM_GATEWAY_ROLE in self.roles


# ── Token Extraction ───────────────────────────────────────────────
def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> str:
    """Extract JWT from Bearer header or nkz_token cookie."""
    if credentials:
        return credentials.credentials
    cookie_token = request.cookies.get("nkz_token")
    if cookie_token:
        return cookie_token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authorization token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Core Auth Dependency ───────────────────────────────────────────
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenPayload:
    """Validate JWT and return typed payload."""
    token = _extract_token(request, credentials)

    # ADR 003 / Gateway trust: decode without signature when behind api-gateway
    if TRUST_API_GATEWAY and request.headers.get("X-Tenant-ID"):
        try:
            payload = jwt.get_unverified_claims(token)
            if payload.get("exp", 0) < time.time():
                raise HTTPException(status_code=401, detail="Token expired")
            return TokenPayload(payload)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    # Full JWKS validation
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Token missing key ID")

        jwks_client = get_jwks_client()
        key_data = jwks_client.get_signing_key(kid)
        public_key = jwk.construct(key_data)

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        return TokenPayload(payload)

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Tenant Extraction ──────────────────────────────────────────────
def get_tenant_id(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    ngsild_tenant: Optional[str] = Header(None, alias="NGSILD-Tenant"),
    user: TokenPayload = Depends(get_current_user),
) -> str:
    """Priority: X-Tenant-ID (gateway) > NGSILD-Tenant > JWT claim."""
    if x_tenant_id:
        return x_tenant_id
    if ngsild_tenant:
        return ngsild_tenant
    if user.tenant_id:
        return user.tenant_id
    raise HTTPException(status_code=400, detail="Tenant ID not found in request or token")


# ── Backwards compatibility aliases ────────────────────────────────
require_auth = get_current_user
