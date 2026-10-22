"""Tests for elevation reader auth (/point, /raster)."""

import hashlib
import hmac
import time
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.middleware.reader_auth import require_elevation_reader

SECRET = "test-internal-secret"
HMAC_SECRET = "test-hmac-secret"


def _app():
    app = FastAPI()

    @app.get("/probe")
    async def probe(tenant: str = Depends(require_elevation_reader)):
        return {"tenant": tenant}

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
        "Authorization": f"Bearer {token}",
        "X-Auth-Signature": f"{sig}:{ts}",
    }


def test_reader_rejects_unauthenticated():
    with patch.dict(
        "os.environ",
        {
            "REQUIRE_ELEVATION_READER_AUTH": "true",
            "INTERNAL_SERVICE_SECRET": SECRET,
            "HMAC_SECRET": HMAC_SECRET,
            "REQUIRE_HMAC_SIGNATURE": "true",
        },
        clear=False,
    ):
        client = TestClient(_app())
        assert client.get("/probe").status_code == 401


def test_reader_accepts_internal_secret():
    with patch.dict(
        "os.environ",
        {
            "REQUIRE_ELEVATION_READER_AUTH": "true",
            "INTERNAL_SERVICE_SECRET": SECRET,
            "HMAC_SECRET": HMAC_SECRET,
            "REQUIRE_HMAC_SIGNATURE": "true",
        },
        clear=False,
    ):
        client = TestClient(_app())
        resp = client.get(
            "/probe",
            headers={
                "X-Internal-Service-Secret": SECRET,
                "X-Tenant-ID": "weather-worker",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tenant"] == "weather-worker"


def test_reader_accepts_gateway_hmac():
    with patch.dict(
        "os.environ",
        {
            "REQUIRE_ELEVATION_READER_AUTH": "true",
            "INTERNAL_SERVICE_SECRET": SECRET,
            "HMAC_SECRET": HMAC_SECRET,
            "REQUIRE_HMAC_SIGNATURE": "true",
        },
        clear=False,
    ):
        client = TestClient(_app())
        resp = client.get("/probe", headers=_gateway_headers())
        assert resp.status_code == 200
        assert resp.json()["tenant"] == "tenant-a"
