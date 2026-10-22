"""Auth for read-only elevation endpoints (/point, /raster).

Accepts either:
- Cluster-internal callers with X-Internal-Service-Secret (weather-map, hydrology, workers)
- Browser/module callers proxied via api-gateway (X-Tenant-ID + X-User-ID + X-Auth-Signature)
"""

from __future__ import annotations

import hmac as hmac_lib
import logging
import os

from fastapi import Header, HTTPException, Request

from app.middleware.hmac import verify_gateway_hmac

logger = logging.getLogger(__name__)


def _settings() -> tuple[bool, str, str, bool]:
    require = os.getenv("REQUIRE_ELEVATION_READER_AUTH", "true").lower() == "true"
    hmac_secret = os.getenv("HMAC_SECRET", "")
    internal_secret = os.getenv("INTERNAL_SERVICE_SECRET", "")
    require_hmac = os.getenv("REQUIRE_HMAC_SIGNATURE", "true").lower() == "true"
    return require, hmac_secret, internal_secret, require_hmac


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :]
    return ""


async def require_elevation_reader(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    x_internal_service_secret: str | None = Header(default=None, alias="X-Internal-Service-Secret"),
) -> str:
    """Return tenant id for observability; raises 401 when auth fails."""
    require_auth, hmac_secret, internal_secret, require_hmac = _settings()
    if not require_auth:
        return x_tenant_id or "dev"

    internal = x_internal_service_secret or ""
    if internal_secret and internal and hmac_lib.compare_digest(internal, internal_secret):
        return x_tenant_id or "platform"

    if not x_tenant_id or not x_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not verify_gateway_hmac(
        request.headers.get("X-Auth-Signature", ""),
        _bearer_token(request),
        x_tenant_id,
        secret=hmac_secret,
        require=require_hmac,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")

    return x_tenant_id
