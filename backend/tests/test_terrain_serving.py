"""Tests for terrain tile serving endpoints — layer.json, .terrain, composite."""

import gzip
import json
import pytest

pytest.importorskip("rasterio", reason="rasterio not installed (non-container env)")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ── Helpers ────────────────────────────────────────────────────────

def _make_s3_no_object():
    """Simulate a boto3 S3 client that raises NoSuchKey on get_object."""
    s3 = MagicMock()
    from botocore.exceptions import ClientError
    err = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        "GetObject",
    )
    s3.get_object.side_effect = err
    ls_resp = {"CommonPrefixes": []}
    s3.get_paginator.return_value.paginate.return_value = [ls_resp]
    return s3


def _make_s3_with_layer(layer_data: dict):
    """Simulate a boto3 client serving one layer.json."""
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(layer_data).encode()))
    }

    paginator = MagicMock()
    s3.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {"CommonPrefixes": [{"Prefix": f"terrain/EU_56_-7/"}]}
    ]
    return s3


def _make_basic_layer_json(bounds=(-10, 35, 5, 44), minzoom=8, maxzoom=14):
    """Minimal valid layer.json."""
    available = []
    for z in range(minzoom, maxzoom + 1):
        available.append([{"startX": 0, "startY": 0, "endX": 10, "endY": 10}])
    return {
        "tilejson": "2.1.0",
        "name": "Test Tileset",
        "description": "Test terrain",
        "version": "1.0.0",
        "format": "quantized-mesh-1.0",
        "scheme": "tms",
        "tiles": ["{z}/{x}/{y}.terrain"],
        "projection": "EPSG:4326",
        "bounds": list(bounds),
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "available": available,
        "extensions": ["octvertexnormals"],
    }


class TestLayerJsonEndpoint:

    @patch("app.api.elevation.get_s3_client")
    def test_exact_match_served_directly(self, mock_minio):
        """When an exact tileset exists, its layer.json is served directly."""
        layer_data = _make_basic_layer_json()
        s3 = _make_s3_with_layer(layer_data)
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/EU/layer.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Tileset"

    @patch("app.api.elevation.get_s3_client")
    def test_get_returns_cors_headers(self, mock_minio):
        """layer.json responses include CORS headers for browser access."""
        layer_data = _make_basic_layer_json()
        s3 = _make_s3_with_layer(layer_data)
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/EU/layer.json")
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "max-age" in resp.headers.get("Cache-Control", "")

    @patch("app.api.elevation.get_s3_client")
    def test_no_tileset_returns_404_with_list(self, mock_minio):
        """When no tileset exists at all, return 404 with available tilesets list."""
        s3 = _make_s3_no_object()
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/XX/layer.json")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "tileset_not_found"
        assert "available_tilesets" in detail

    @patch("app.api.elevation.get_s3_client")
    def test_composite_generated_from_subs(self, mock_minio):
        """When 'EU' has no direct layer.json but EU_* subs exist, generate composite."""
        s3 = MagicMock()

        # First get_object call fails (NoSuchKey for terrain/EU/layer.json)
        from botocore.exceptions import ClientError
        err = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject",
        )
        # Build properly structured mock: resp['Body'].read() must return bytes
        body_mock = MagicMock()
        body_mock.read.return_value = json.dumps(_make_basic_layer_json()).encode()
        s3.get_object.side_effect = [
            err,                              # terrain/EU/layer.json → NoSuchKey
            {"Body": body_mock},             # terrain/EU_56_-7/layer.json → success
        ]

        # Find sub-tilesets
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {"CommonPrefixes": [{"Prefix": "terrain/EU_56_-7/"}]}
        ]

        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/EU/layer.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "_composite" in data
        assert data["_composite"] is True
        assert data["name"] == "Nekazari EU Composite Terrain"


class TestTileEndpoint:

    @patch("app.api.elevation.get_s3_client")
    def test_tile_served_directly(self, mock_minio):
        """Exact path tile is served with correct headers."""
        payload = b"quantized-mesh-bytes"
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=gzip.compress(payload)))
        }
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/ES/8/120/80.terrain")
        assert resp.status_code == 200
        assert resp.content == payload  # served decompressed (raw quantized mesh)
        assert resp.headers.get("Content-Type").startswith("application/vnd.quantized-mesh")
        assert "immutable" in resp.headers.get("Cache-Control", "")

    @patch("app.api.elevation.get_s3_client")
    def test_missing_tile_returns_204(self, mock_minio):
        """No tile anywhere returns 204 (no content, not an error for Cesium)."""
        s3 = _make_s3_no_object()
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/XX/0/0/0.terrain")
        assert resp.status_code == 204

    @patch("app.api.elevation.get_s3_client")
    def test_tile_resolved_from_sub_tileset(self, mock_minio):
        """When tile not at exact path, sub-tileset search finds it."""
        s3 = MagicMock()
        from botocore.exceptions import ClientError
        err = ClientError(
            {"Error": {"Code": "NoSuchKey"}}, "GetObject"
        )
        # First call fails (exact path), second succeeds (sub-tileset)
        payload = b"sub-tile-quantized-mesh"
        body_mock = MagicMock()
        body_mock.read.return_value = gzip.compress(payload)
        s3.get_object.side_effect = [
            err,
            {"Body": body_mock},
        ]

        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {"CommonPrefixes": [{"Prefix": "terrain/EU_56_-7/"}]}
        ]
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/EU/8/120/80.terrain")
        assert resp.status_code == 200
        assert resp.content == payload

    @patch("app.api.elevation.get_s3_client")
    def test_tile_returns_cors_headers(self, mock_minio):
        """Tile responses include CORS headers."""
        s3 = MagicMock()
        s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=gzip.compress(b"tile-data")))
        }
        mock_minio.return_value = s3

        resp = client.get("/api/elevation/terrain/EU/8/0/0.terrain")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


class TestListAvailableTilesets:

    def test_list_returns_tileset_names(self):
        """_list_available_tilesets returns clean names from CommonPrefixes."""
        from app.api.elevation import _list_available_tilesets

        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [
            {"CommonPrefixes": [
                {"Prefix": "terrain/ES/"},
                {"Prefix": "terrain/FR/"},
                {"Prefix": "terrain/EU_56_-7/"},
            ]}
        ]

        result = _list_available_tilesets(s3, "bucket")
        assert "ES" in result
        assert "FR" in result
        assert "EU_56_-7" in result
        assert len(result) == 3

    def test_list_handles_empty(self):
        """Empty bucket returns empty list, not an error."""
        from app.api.elevation import _list_available_tilesets

        s3 = MagicMock()
        paginator = MagicMock()
        s3.get_paginator.return_value = paginator
        paginator.paginate.return_value = [{}]

        result = _list_available_tilesets(s3, "bucket")
        assert result == []

    def test_list_handles_s3_error(self):
        """S3 error returns empty list gracefully."""
        from app.api.elevation import _list_available_tilesets

        s3 = MagicMock()
        s3.get_paginator.side_effect = Exception("S3 down")

        result = _list_available_tilesets(s3, "bucket")
        assert result == []
