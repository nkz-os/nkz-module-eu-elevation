"""A browser WebSocket handshake carries the page Origin; sibling subdomains are same-site."""

import pytest

from app.middleware.ws_auth import allowed_origins, origin_allowed


def test_browser_origin_must_be_allowed():
    assert origin_allowed("https://ui.example", ["https://ui.example"])
    assert not origin_allowed("https://evil.example", ["https://ui.example"])


def test_null_origin_is_rejected():
    assert not origin_allowed("null", ["https://ui.example"])


def test_non_browser_client_without_origin_is_allowed():
    assert origin_allowed(None, ["https://ui.example"])


def test_empty_allowlist_rejects_every_browser(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    assert allowed_origins() == []
    assert not origin_allowed("https://ui.example", allowed_origins())


def test_allowlist_parses_csv(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.example, https://b.example ")
    assert allowed_origins() == ["https://a.example", "https://b.example"]


def test_handshake_from_foreign_origin_is_closed(monkeypatch):
    pytest.importorskip("app.main", reason="app.main import failed (missing deps)")
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import app

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ui.example")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/api/elevation/ws/status/job-1",
            headers={"Origin": "https://evil.example", "Cookie": "nkz_token=x"},
        ) as ws:
            ws.receive_text()
    assert exc.value.code == 4003


def test_handshake_from_allowed_origin_still_needs_the_cookie(monkeypatch):
    pytest.importorskip("app.main", reason="app.main import failed (missing deps)")
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import app

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ui.example")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/api/elevation/ws/status/job-1", headers={"Origin": "https://ui.example"}
        ) as ws:
            ws.receive_text()
    assert exc.value.code == 4001
