"""Test-session fixtures for the eu-elevation backend.

The reader auth gate (commit a1571c6) requires either X-Internal-Service-Secret
or a gateway HMAC signature for /point and /raster. Endpoint-logic tests assert
contract/sampling behaviour, not auth — auth is covered separately by
test_reader_auth.py and test_gateway_auth.py (which build their own local apps
and patch os.environ themselves). Disabling the gate here keeps app.main-based
tests deterministic and offline-friendly.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_reader_auth(monkeypatch):
    monkeypatch.setenv("REQUIRE_ELEVATION_READER_AUTH", "false")
