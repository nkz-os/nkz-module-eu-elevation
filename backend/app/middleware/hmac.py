"""Gateway HMAC signature verification (mirrors api-gateway keycloak_auth)."""

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

_SIGNATURE_WINDOW_SECONDS = 300


def verify_gateway_hmac(
    signature_header: str,
    token: str,
    tenant_id: str,
    *,
    secret: str,
    require: bool = True,
    now: int | None = None,
) -> bool:
    if not secret:
        if require:
            logger.error("HMAC secret missing while require=True - rejecting (fail-closed)")
            return False
        return True

    if not signature_header or ":" not in signature_header:
        return False

    provided, _, ts_str = signature_header.partition(":")
    try:
        ts = int(ts_str)
    except ValueError:
        return False

    if now is None:
        now = int(time.time())
    if abs(now - ts) > _SIGNATURE_WINDOW_SECONDS:
        return False

    message = f"{token}|{tenant_id}|{ts}"
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)
