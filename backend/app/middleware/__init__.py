"""Gateway-trusted auth for admin HTTP endpoints (SDK headers + HMAC)."""

from fastapi import Depends, HTTPException, Request

from nkz_platform_sdk import AuthContext, require_auth as _sdk_require_auth

from app.config import settings
from app.middleware.hmac import verify_gateway_hmac


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :]
    return ""


def require_auth(roles=None):
    """FastAPI dependency: SDK header validation + gateway HMAC gate."""
    sdk_dep = _sdk_require_auth(roles)

    async def _dependency(ctx: AuthContext = sdk_dep, request: Request = None) -> AuthContext:
        if not verify_gateway_hmac(
            request.headers.get("X-Auth-Signature", ""),
            _bearer_token(request),
            ctx.tenant_id,
            secret=settings.HMAC_SECRET,
            require=settings.REQUIRE_HMAC_SIGNATURE,
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return ctx

    return Depends(_dependency)
