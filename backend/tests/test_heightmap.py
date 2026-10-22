"""Tests for the on-demand heightmap service and endpoint.

Covers: tile bbox math, terrarium encode/decode roundtrip, COG cover caps,
endpoint validation (zoom window / tile coords), cache hit vs generation,
and cache write-through behaviour.
"""

import pytest

pytest.importorskip("rasterio", reason="rasterio not installed (non-container env)")
pytest.importorskip("PIL", reason="Pillow not installed")

import numpy as np
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import heightmap as hm

client = TestClient(app)


# ── Tile math ────────────────────────────────────────────────────────

def test_tile_bbox_root_covers_world():
    west, south, east, north = hm.tile_bbox(0, 0, 0)
    assert west == -180.0 and east == 180.0
    assert abs(south - -85.0511287798066) < 1e-9
    assert abs(north - 85.0511287798066) < 1e-9


def test_tile_bbox_quadrant_z1():
    # z1 x0 y0 = NW quadrant (85.05 -> 0.0 exactly)
    west, south, east, north = hm.tile_bbox(1, 0, 0)
    assert west == -180.0 and east == 0.0
    assert north == pytest.approx(85.0511287798066)
    assert south == 0.0


def test_covering_copernicus_paths_single_cell():
    # bbox fully inside the 1x1 deg cell [-2,-1) x [41,42)
    paths = hm.covering_copernicus_paths((-1.2, 41.0, -1.05, 41.5))
    assert paths == [
        "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_N41_00_W002_00_DEM/"
        "Copernicus_DSM_COG_10_N41_00_W002_00_DEM.tif"
    ]


def test_covering_copernicus_paths_spans_two_cells():
    # bbox straddling lon -1 -> covers cells [-2,-1) and [-1,0)
    paths = hm.covering_copernicus_paths((-1.2, 41.0, -0.8, 41.5))
    assert len(paths) == 2


def test_covering_copernicus_paths_west_south_cells():
    # SW hemisphere naming: cell floor(-3.5)=-4 (W004), floor(-2.5)=-3 (S03)
    paths = hm.covering_copernicus_paths((-3.5, -2.5, -3.1, -2.1))
    assert paths == [
        "/vsis3/copernicus-dem-30m/Copernicus_DSM_COG_10_S03_00_W004_00_DEM/"
        "Copernicus_DSM_COG_10_S03_00_W004_00_DEM.tif"
    ]


# ── Terrarium encoding ───────────────────────────────────────────────

def test_terrarium_roundtrip_within_precision():
    grid = np.array([[0.0, 123.4], [-45.6, 3210.7]], dtype=np.float64)
    png = hm.encode_terrarium_png(grid)
    decoded = hm.decode_terrarium_png(png)
    assert decoded.shape == (2, 2)
    np.testing.assert_allclose(decoded, grid, atol=1.0 / 256.0)


def test_terrarium_clamps_out_of_range():
    grid = np.array([[-40000.0, 40000.0]], dtype=np.float64)
    decoded = hm.decode_terrarium_png(hm.encode_terrarium_png(grid))
    assert decoded[0][0] >= -32768.0
    assert decoded[0][1] <= 32768.0


def test_terrarium_nan_becomes_zero():
    grid = np.array([[float("nan"), 10.0]], dtype=np.float64)
    decoded = hm.decode_terrarium_png(hm.encode_terrarium_png(grid))
    assert decoded[0][0] == pytest.approx(0.0, abs=1e-6)


# ── Generation guards ────────────────────────────────────────────────

def test_generate_returns_none_when_too_many_cogs(monkeypatch):
    # bbox spanning the whole world -> cover explodes past the cap
    monkeypatch.setattr(hm, "MAX_COGS_PER_TILE", 10)
    assert hm.generate_heightmap_png(6, 0, 0) is None


# ── Endpoint ─────────────────────────────────────────────────────────

def test_endpoint_rejects_below_min_zoom():
    resp = client.get("/api/elevation/heightmap/3/4/5.png")
    assert resp.status_code == 404


def test_endpoint_rejects_above_max_zoom():
    resp = client.get("/api/elevation/heightmap/18/0/0.png")
    assert resp.status_code == 404


def test_endpoint_rejects_out_of_range_coords():
    resp = client.get("/api/elevation/heightmap/8/999/0.png")
    assert resp.status_code == 404


def test_endpoint_serves_generated_tile_and_caches():
    fake_png = hm.encode_terrarium_png(np.zeros((4, 4)))
    with patch.object(hm, "get_cached_tile", return_value=None), \
         patch.object(hm, "store_tile") as store, \
         patch.object(hm, "generate_heightmap_png", return_value=fake_png) as gen:
        resp = client.get("/api/elevation/heightmap/8/134/97.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=2592000, immutable"
    assert resp.content == fake_png
    gen.assert_called_once_with(8, 134, 97)
    store.assert_called_once_with(8, 134, 97, fake_png)


def test_endpoint_cache_hit_skips_generation():
    fake_png = hm.encode_terrarium_png(np.zeros((4, 4)))
    with patch.object(hm, "get_cached_tile", return_value=fake_png), \
         patch.object(hm, "generate_heightmap_png") as gen:
        resp = client.get("/api/elevation/heightmap/8/134/97.png")
    assert resp.status_code == 200
    assert resp.content == fake_png
    gen.assert_not_called()


def test_endpoint_404_when_not_generatable():
    with patch.object(hm, "get_cached_tile", return_value=None), \
         patch.object(hm, "generate_heightmap_png", return_value=None):
        resp = client.get("/api/elevation/heightmap/8/134/97.png")
    assert resp.status_code == 404
