"""Tests for gateway-trusted admin auth."""

import hashlib
import hmac
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from nkz_platform_sdk import AuthContext

from app.middleware import require_auth

HMAC_SECRET = "test-hmac-secret"


def _app():
    app = FastAPI()

    @app.get("/admin-probe")
    async def admin_probe(auth: AuthContext = require_auth()):
        return {"tenant": auth.tenant_id, "user": auth.user_id}

    return app


def _gateway_headers(tenant: str = "tenant-a", token: str = "tok") -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(
        HMAC_SECRET.encode(),
        f"{token}|{tenant}|{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Tenant-ID": tenant,
        "X-User-ID": "user-1",
        "X-User-Roles": "Farmer",
        "Authorization": f"Bearer {token}",
        "X-Auth-Signature": f"{sig}:{ts}",
    }


def test_admin_rejects_unsigned():
    with patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "HMAC_SECRET",
        HMAC_SECRET,
    ), patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "REQUIRE_HMAC_SIGNATURE",
        True,
    ):
        client = TestClient(_app())
        assert client.get("/admin-probe").status_code == 401


def test_admin_accepts_gateway_hmac():
    with patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "HMAC_SECRET",
        HMAC_SECRET,
    ), patch.object(
        __import__("app.config", fromlist=["settings"]).settings,
        "REQUIRE_HMAC_SIGNATURE",
        True,
    ):
        client = TestClient(_app())
        resp = client.get("/admin-probe", headers=_gateway_headers())
        assert resp.status_code == 200
        assert resp.json()["tenant"] == "tenant-a"
