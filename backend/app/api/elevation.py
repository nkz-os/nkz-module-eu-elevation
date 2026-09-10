"""
EU Elevation API endpoints — SOTA multi-tier terrain provider.

Tiers:
  - Tier 0: Built-in providers (Cesium World Terrain, MapTiler)
  - Tier 1: Custom DEM sources (user-registered WCS/WMS/GeoTIFF)
  - Tier 2: Ingested layers (quantized mesh tiles in MinIO)
"""

import io
import gzip
import logging
import asyncio
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Optional
import httpx
import rasterio
from fastapi import APIRouter, Depends, HTTPException, Query, status, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.middleware import require_auth
from app.middleware.reader_auth import require_elevation_reader
from nkz_platform_sdk import AuthContext
from app.tasks.elevation_tasks import process_dem_to_quantized_mesh, process_local_dem_to_quantized_mesh
from app.dem_sources import get_source, get_all_sources
from app.services.point_query import (
    resolve_source, build_wcs_url, WCS_PARAMS,
    sample_tiff_point, sample_copernicus_point, point_source_plan,
)
from app.services.source_registry import SourceRegistry, _parse_resolution
from enum import Enum
from app.config import settings
from app.common.crypto import encrypt_token, decrypt_token
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.elevation_models import ElevationLayer, CustomDemSource, TenantTerrainPreferences
import json
import uuid

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Purpose parameter enum ────────────────────────────────────────

class PurposeEnum(str, Enum):
    auto = "auto"
    precision = "precision"
    routing = "routing"
    weather = "weather"
    visualization = "visualization"

# SourceRegistry singleton (lazy init)
_source_registry: Optional[SourceRegistry] = None

def _get_registry() -> SourceRegistry:
    global _source_registry
    if _source_registry is None:
        _source_registry = SourceRegistry()
    return _source_registry

# ============================================================================
# Built-in terrain providers (Tier 0 — no ingestion needed)
# ============================================================================

BUILTIN_PROVIDERS = [
    {
        "id": "builtin_europe_copernicus",
        "name": "Copernicus EU Terrain",
        "type": "europe_copernicus",
        "description": "Copernicus GLO-30 terrain served on demand by the module backend — no ingestion, no token. Only viewed tiles are cached.",
        "resolution": "30m",
        "coverage": "EU + UK (Global fallback)",
        "requires_token": False,
    },
    {
        "id": "builtin_cesium_world",
        "name": "Cesium World Terrain",
        "type": "cesium_world",
        "description": "Global ~30m terrain from Cesium Ion (free)",
        "resolution": "~30m",
        "coverage": "Global",
        "requires_token": False,
    },
    {
        "id": "builtin_maptiler",
        "name": "MapTiler Terrain",
        "type": "maptiler",
        "description": "High-resolution EU/UK terrain (up to 50cm)",
        "resolution": "Up to 50cm",
        "coverage": "EU + UK",
        "requires_token": True,
    },
]


# ============================================================================
# Request/Response Models
# ============================================================================

class DEMSourceResponse(BaseModel):
    country_code: str
    country_name: str
    service_url: str
    service_type: str
    format: str
    resolution: str
    bbox: tuple[float, float, float, float]
    layer_name: Optional[str] = None
    notes: str = ""
    fallback: bool = False
    requires_preprocessing: str = ""


class ElevationLayerCreate(BaseModel):
    name: str = Field(..., description="Display name for the terrain provider")
    url: str = Field(..., description="Base URL of the Cesium Terrain Provider")
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None
    is_active: bool = True


class ElevationLayerResponse(BaseModel):
    id: uuid.UUID
    tenant_id: str
    name: str
    url: str
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None
    is_active: bool = True

    class Config:
        from_attributes = True


class BboxIngestRequest(BaseModel):
    country_code: str = Field(..., description="ISO country code")
    bbox: Optional[tuple[float, float, float, float]] = Field(None)
    source_urls: Optional[List[str]] = Field(None)
    zoom_min: int = Field(8, ge=0, le=15)
    zoom_max: int = Field(14, ge=0, le=15)
    max_error: float = Field(0.5, gt=0, le=10)


class ProcessResponse(BaseModel):
    job_id: str
    status: str
    message: str
    source: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


class CustomDemSourceCreate(BaseModel):
    name: str = Field(..., description="Display name for the DEM source")
    country_code: Optional[str] = Field(None, description="Optional ISO country code")
    service_url: str = Field(..., description="WCS/WMS/GeoTIFF endpoint URL")
    service_type: str = Field("WCS", description="WCS, WMS, DOWNLOAD, or REST")
    format: str = Field("GeoTIFF")
    resolution: Optional[str] = None
    layer_name: Optional[str] = None
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None
    auth_header_name: Optional[str] = Field(None, description="e.g. X-API-Key, Authorization")
    auth_header_value: Optional[str] = Field(None, description="Token or key value")
    notes: Optional[str] = None


class CustomDemSourceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: str
    name: str
    country_code: Optional[str] = None
    service_url: str
    service_type: str
    format: str
    resolution: Optional[str] = None
    layer_name: Optional[str] = None
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None
    has_auth: bool = Field(False, description="Whether auth headers are configured")
    is_active: bool = True
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class TerrainPreferencesUpdate(BaseModel):
    provider_type: str = Field("europe_copernicus", description="off, europe_copernicus, cesium_world, maptiler, custom, auto")
    cesium_ion_token: Optional[str] = None
    maptiler_api_key: Optional[str] = None
    custom_terrain_url: Optional[str] = None
    auto_mode: bool = True


class TerrainPreferencesResponse(BaseModel):
    tenant_id: str
    provider_type: str = "europe_copernicus"
    has_cesium_token: bool = False
    has_maptiler_key: bool = False
    custom_terrain_url: Optional[str] = None
    auto_mode: bool = True


class TerrainTokensResponse(BaseModel):
    """Returns actual token values for the authenticated tenant's own preferences."""
    cesium_ion_token: Optional[str] = None
    maptiler_api_key: Optional[str] = None
    custom_terrain_url: Optional[str] = None
    europe_copernicus_url: Optional[str] = None
    provider_type: str = "europe_copernicus"


class TerrainProviderInfo(BaseModel):
    id: str
    name: str
    type: str
    description: str
    resolution: str
    coverage: str
    requires_token: bool
    is_active: bool = False


# ============================================================================
# Health (accessible through ingress prefix path)
# ============================================================================

@router.get("/health")
async def router_health_check():
    """Health check accessible via ingress /api/elevation/health.

    Verifies connectivity to MinIO (terrain tile storage) and reports
    available tilesets. Returns 200 with dependency status — the pod
    remains live even if MinIO is down (degraded mode).
    """
    deps = {"minio": "unknown", "redis": "unknown"}
    tilesets = []

    # MinIO connectivity check
    try:
        s3 = get_s3_client()
        bucket = os.getenv("MINIO_BUCKET", "terrain-tilesets")
        s3.head_bucket(Bucket=bucket)
        deps["minio"] = "ok"
        tilesets = _list_available_tilesets(s3, bucket)
    except Exception as e:
        deps["minio"] = f"error: {e}"

    # Redis connectivity check
    try:
        r = await _get_redis()
        if r:
            await r.ping()
            deps["redis"] = "ok"
        else:
            deps["redis"] = "not_configured"
    except Exception as e:
        deps["redis"] = f"error: {e}"

    return {
        "status": "healthy",
        "module": "eu-elevation",
        "version": "1.0.0",
        "dependencies": deps,
        "available_tilesets": tilesets,
    }


# ============================================================================
# DEM Source Catalog (read-only, built-in)
# ============================================================================

@router.get("/sources", response_model=List[DEMSourceResponse])
async def list_dem_sources(_auth: AuthContext = require_auth()):
    """List all pre-configured EU/UK DEM data sources for ingestion."""
    sources = get_all_sources(include_fallback=True)
    return [
        DEMSourceResponse(
            country_code=s.country_code,
            country_name=s.country_name,
            service_url=s.service_url,
            service_type=s.service_type,
            format=s.format,
            resolution=s.resolution,
            bbox=s.bbox,
            layer_name=s.layer_name,
            notes=s.notes,
            fallback=s.fallback,
            requires_preprocessing=s.requires_preprocessing,
        )
        for s in sources
    ]


@router.get("/sources/catalog", response_model=List[DEMSourceResponse])
async def list_catalog_sources(_auth: AuthContext = require_auth()):
    """List all pre-configured DEM sources (Tier 1 catalog)."""
    sources = get_all_sources(include_fallback=True)
    return [
        DEMSourceResponse(
            country_code=s.country_code, country_name=s.country_name,
            service_url=s.service_url, service_type=s.service_type,
            format=s.format, resolution=s.resolution, bbox=s.bbox,
            layer_name=s.layer_name, notes=s.notes, fallback=s.fallback,
            requires_preprocessing=s.requires_preprocessing,
        ) for s in sources
    ]


@router.get("/sources/custom", response_model=List[CustomDemSourceResponse])
async def list_custom_sources(
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """List all custom DEM sources registered by the current tenant."""
    sources = db.query(CustomDemSource).filter(
        CustomDemSource.tenant_id == auth.tenant_id
    ).all()
    result = []
    for s in sources:
        resp = CustomDemSourceResponse(
            id=s.id, tenant_id=s.tenant_id, name=s.name,
            country_code=s.country_code, service_url=s.service_url,
            service_type=s.service_type, format=s.format,
            resolution=s.resolution, layer_name=s.layer_name,
            bbox_minx=s.bbox_minx, bbox_miny=s.bbox_miny,
            bbox_maxx=s.bbox_maxx, bbox_maxy=s.bbox_maxy,
            has_auth=bool(s.auth_header_name and s.auth_header_value),
            is_active=s.is_active, notes=s.notes,
        )
        result.append(resp)
    return result


@router.get("/sources/{country_code}", response_model=DEMSourceResponse)
async def get_dem_source(country_code: str, _auth: AuthContext = require_auth()):
    src = get_source(country_code)
    if not src:
        raise HTTPException(status_code=404, detail=f"No DEM source for '{country_code}'")
    return DEMSourceResponse(
        country_code=src.country_code, country_name=src.country_name,
        service_url=src.service_url, service_type=src.service_type,
        format=src.format, resolution=src.resolution, bbox=src.bbox,
        layer_name=src.layer_name, notes=src.notes, fallback=src.fallback,
        requires_preprocessing=src.requires_preprocessing,
    )


@router.post("/sources/custom", response_model=CustomDemSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_source(
    source_in: CustomDemSourceCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """Register a new custom DEM source for ingestion."""
    new_source = CustomDemSource(
        tenant_id=auth.tenant_id,
        name=source_in.name,
        country_code=source_in.country_code,
        service_url=source_in.service_url,
        service_type=source_in.service_type,
        format=source_in.format,
        resolution=source_in.resolution,
        layer_name=source_in.layer_name,
        bbox_minx=source_in.bbox_minx,
        bbox_miny=source_in.bbox_miny,
        bbox_maxx=source_in.bbox_maxx,
        bbox_maxy=source_in.bbox_maxy,
        auth_header_name=source_in.auth_header_name,
        auth_header_value=encrypt_token(source_in.auth_header_value) if source_in.auth_header_value else None,
        notes=source_in.notes,
    )
    db.add(new_source)
    db.commit()
    db.refresh(new_source)
    return CustomDemSourceResponse(
        id=new_source.id, tenant_id=new_source.tenant_id,
        name=new_source.name, country_code=new_source.country_code,
        service_url=new_source.service_url, service_type=new_source.service_type,
        format=new_source.format, resolution=new_source.resolution,
        layer_name=new_source.layer_name,
        bbox_minx=new_source.bbox_minx, bbox_miny=new_source.bbox_miny,
        bbox_maxx=new_source.bbox_maxx, bbox_maxy=new_source.bbox_maxy,
        has_auth=bool(new_source.auth_header_name and new_source.auth_header_value),
        is_active=new_source.is_active, notes=new_source.notes,
    )


@router.delete("/sources/custom/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    source = db.query(CustomDemSource).filter(
        CustomDemSource.id == source_id,
        CustomDemSource.tenant_id == auth.tenant_id,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Custom DEM source not found")
    db.delete(source)
    db.commit()
    return None


# ============================================================================
# Ingestion Endpoints
# ============================================================================

@router.post("/ingest", response_model=ProcessResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_ingestion(
    request: BboxIngestRequest,
    auth: AuthContext = require_auth(),
):
    dem_source = get_source(request.country_code)
    source_urls = request.source_urls

    if not source_urls:
        if not dem_source:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown country code '{request.country_code}'. Use GET /sources or /sources/custom.",
            )
        source_urls = [dem_source.service_url]

    bbox = request.bbox
    if not bbox:
        if dem_source:
            bbox = dem_source.bbox
        else:
            raise HTTPException(status_code=400, detail="BBOX required for custom sources")

    source_label = dem_source.country_name if dem_source else "custom"
    logger.info(f"Ingestion: {request.country_code} ({source_label}) BBOX={bbox} tenant={auth.tenant_id}")

    try:
        task_kwargs = {
            "country_code": request.country_code,
            "source_urls": source_urls,
            "bbox": bbox,
            "zoom_min": request.zoom_min,
            "zoom_max": request.zoom_max,
            "max_error": request.max_error,
        }

        # ── If using a national WCS source, pass WCS params ──
        if dem_source and dem_source.service_type == "WCS" and not dem_source.fallback:
            task_kwargs.update({
                "_wcs_service_url": dem_source.service_url,
                "_wcs_layer_name": dem_source.layer_name or "",
                "_wcs_resolution_m": float(dem_source.resolution.replace("m", ""))
                if dem_source.resolution else 25.0,
            })

        task = process_dem_to_quantized_mesh.delay(**task_kwargs)
        return ProcessResponse(
            job_id=task.id, status="queued",
            message=f"Ingestion for {source_label} queued. WS: /api/elevation/ws/status/{task.id}",
            source=dem_source.service_url if dem_source else source_urls[0],
        )
    except Exception as e:
        logger.error(f"Failed to enqueue ingestion: {e}")
        raise HTTPException(status_code=503, detail="Processing queue unavailable")


@router.post("/upload", response_model=ProcessResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_dem(
    file: UploadFile = File(...),
    country_code: str = Form(...),
    bbox: Optional[str] = Form(None),
    zoom_min: int = Form(8),
    zoom_max: int = Form(14),
    auth: AuthContext = require_auth(),
):
    logger.info(f"Local upload: {file.filename} tenant={auth.tenant_id}")
    if not file.filename.lower().endswith(('.tif', '.tiff', '.asc')):
        raise HTTPException(status_code=400, detail="Only .tif, .tiff, or .asc files supported")

    upload_dir = os.path.join(tempfile.gettempdir(), "terrain_uploads", country_code)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"Failed to save upload: {e}")
        raise HTTPException(status_code=500, detail="Could not save file")

    parsed_bbox = None
    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(',')]
            if len(parts) == 4:
                parsed_bbox = tuple(parts)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid BBOX format")

    try:
        task = process_local_dem_to_quantized_mesh.delay(
            country_code, file_path, parsed_bbox, zoom_min, zoom_max,
        )
        return ProcessResponse(
            job_id=task.id, status="queued",
            message="Upload job queued.",
        )
    except Exception as e:
        logger.error(f"Failed to enqueue upload: {e}")
        raise HTTPException(status_code=503, detail="Processing queue unavailable")


# ============================================================================
# Job Status
# ============================================================================

@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, _auth: AuthContext = require_auth()):
    from celery.result import AsyncResult
    from app.worker import celery_app
    task_result = AsyncResult(job_id, app=celery_app)
    response = JobStatusResponse(
        job_id=job_id, status=task_result.status,
        result=task_result.info if isinstance(task_result.info, dict) else None,
    )
    if task_result.successful():
        response.result = task_result.result
    elif task_result.failed():
        response.error = str(task_result.result)
    return response


@router.websocket("/ws/status/{job_id}")
async def websocket_job_status(websocket: WebSocket, job_id: str):
    await websocket.accept()

    token = websocket.cookies.get("nkz_token")
    if not token:
        await websocket.close(code=4001, reason="Missing auth token")
        return
    try:
        from jose import JWTError
        from app.middleware.ws_auth import verify_websocket_token

        verify_websocket_token(token)
    except JWTError as e:
        await websocket.close(code=4001, reason=f"Invalid token: {e}")
        return
    except Exception:
        await websocket.close(code=4001, reason="Auth failed")
        return

    from celery.result import AsyncResult
    from app.worker import celery_app
    task_result = AsyncResult(job_id, app=celery_app)
    try:
        while True:
            state = task_result.state
            info = task_result.info
            payload = {"job_id": job_id, "status": state, "progress": 0, "message": ""}
            if isinstance(info, dict):
                payload["progress"] = info.get("progress", 0)
                payload["message"] = info.get("message", "")
                if state == "SUCCESS":
                    payload["result"] = info
            elif isinstance(info, Exception):
                payload["message"] = str(info)
                payload["error"] = True
            await websocket.send_json(payload)
            if state in ["SUCCESS", "FAILURE", "REVOKED"]:
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info(f"WS disconnect: job {job_id}")
    except Exception as e:
        logger.error(f"WS error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


# ============================================================================
# Terrain Layers (ingested tilesets in MinIO)
# ============================================================================

@router.get("/layers", response_model=List[ElevationLayerResponse])
async def get_elevation_layers(
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """Get all ingested elevation layers for the current tenant."""
    return db.query(ElevationLayer).filter(
        ElevationLayer.tenant_id == auth.tenant_id
    ).all()


@router.post("/layers", response_model=ElevationLayerResponse, status_code=status.HTTP_201_CREATED)
async def create_elevation_layer(
    layer_in: ElevationLayerCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    new_layer = ElevationLayer(
        tenant_id=auth.tenant_id, name=layer_in.name, url=layer_in.url,
        bbox_minx=layer_in.bbox_minx, bbox_miny=layer_in.bbox_miny,
        bbox_maxx=layer_in.bbox_maxx, bbox_maxy=layer_in.bbox_maxy,
        is_active=layer_in.is_active,
    )
    db.add(new_layer)
    db.commit()
    db.refresh(new_layer)
    return new_layer


@router.delete("/layers/{layer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_elevation_layer(
    layer_id: uuid.UUID,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    layer = db.query(ElevationLayer).filter(
        ElevationLayer.id == layer_id, ElevationLayer.tenant_id == auth.tenant_id,
    ).first()
    if not layer:
        raise HTTPException(status_code=404, detail="Layer not found")
    db.delete(layer)
    db.commit()
    return None


# ============================================================================
# Terrain Provider Preferences (BYOK + tier selection)
# ============================================================================

@router.get("/providers", response_model=List[TerrainProviderInfo])
async def list_providers(
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """List all available terrain providers with their active status."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == auth.tenant_id,
    ).first()
    active_type = prefs.provider_type if prefs else "europe_copernicus"

    providers = []
    for bp in BUILTIN_PROVIDERS:
        providers.append(TerrainProviderInfo(
            id=bp["id"], name=bp["name"], type=bp["type"],
            description=bp["description"], resolution=bp["resolution"],
            coverage=bp["coverage"], requires_token=bp["requires_token"],
            is_active=(active_type == bp["type"]),
        ))

    # Custom ingested layers
    layers = db.query(ElevationLayer).filter(
        ElevationLayer.tenant_id == auth.tenant_id, ElevationLayer.is_active,
    ).all()
    for layer in layers:
        is_layer_active = bool(active_type == "custom" and prefs and prefs.custom_terrain_url == layer.url)
        providers.append(TerrainProviderInfo(
            id=f"layer_{layer.id}", name=layer.name, type="custom",
            description=f"Custom terrain: {layer.url}",
            resolution="Variable", coverage="Custom BBOX",
            requires_token=False, is_active=is_layer_active,
        ))

    return providers


@router.get("/preferences", response_model=TerrainPreferencesResponse)
async def get_preferences(
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """Get current tenant terrain preferences (tokens masked)."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == auth.tenant_id,
    ).first()
    if not prefs:
        return TerrainPreferencesResponse(tenant_id=auth.tenant_id)
    return TerrainPreferencesResponse(
        tenant_id=prefs.tenant_id,
        provider_type=prefs.provider_type,
        has_cesium_token=bool(prefs.cesium_ion_token),
        has_maptiler_key=bool(prefs.maptiler_api_key),
        custom_terrain_url=prefs.custom_terrain_url,
        auto_mode=prefs.auto_mode,
    )


@router.get("/preferences/tokens", response_model=TerrainTokensResponse)
async def get_tokens(
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """Return actual token values for the authenticated tenant. Used by ElevationLayer slot."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == auth.tenant_id,
    ).first()
    if not prefs:
        return TerrainTokensResponse()
    return TerrainTokensResponse(
        cesium_ion_token=decrypt_token(prefs.cesium_ion_token or ""),
        maptiler_api_key=decrypt_token(prefs.maptiler_api_key or ""),
        custom_terrain_url=prefs.custom_terrain_url,
        europe_copernicus_url=settings.EU_COPERNICUS_TERRAIN_URL,
        provider_type=prefs.provider_type,
    )


@router.put("/preferences", response_model=TerrainPreferencesResponse)
async def update_preferences(
    prefs_in: TerrainPreferencesUpdate,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    """Update tenant terrain preferences and BYOK tokens."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == auth.tenant_id,
    ).first()

    if not prefs:
        prefs = TenantTerrainPreferences(tenant_id=auth.tenant_id)
        db.add(prefs)

    if prefs_in.provider_type is not None:
        prefs.provider_type = prefs_in.provider_type
    if prefs_in.cesium_ion_token is not None:
        prefs.cesium_ion_token = encrypt_token(prefs_in.cesium_ion_token)
    if prefs_in.maptiler_api_key is not None:
        prefs.maptiler_api_key = encrypt_token(prefs_in.maptiler_api_key)
    if prefs_in.custom_terrain_url is not None:
        prefs.custom_terrain_url = prefs_in.custom_terrain_url
    if prefs_in.auto_mode is not None:
        prefs.auto_mode = prefs_in.auto_mode

    db.commit()
    db.refresh(prefs)

    return TerrainPreferencesResponse(
        tenant_id=prefs.tenant_id,
        provider_type=prefs.provider_type,
        has_cesium_token=bool(prefs.cesium_ion_token),
        has_maptiler_key=bool(prefs.maptiler_api_key),
        custom_terrain_url=prefs.custom_terrain_url,
        auto_mode=prefs.auto_mode,
    )


# ============================================================================
# Offline Vector Sync
# ============================================================================

@router.get("/sync/vectorial")
async def sync_vectorial(
    last_pulled_at: int = 0,
    db: Session = Depends(get_db),
    auth: AuthContext = require_auth(),
):
    current_ts = int(time.time() * 1000)
    query = db.query(ElevationLayer).filter(ElevationLayer.tenant_id == auth.tenant_id)
    if last_pulled_at > 0:
        last_dt = datetime.fromtimestamp(last_pulled_at / 1000.0, tz=timezone.utc)
        query = query.filter(ElevationLayer.updated_at >= last_dt)
    layers = query.all()
    updated_items, created_items = [], []
    for layer in layers:
        item = {
            'remote_id': str(layer.id), 'id': str(layer.id),
            'name': layer.name, 'url': layer.url,
            'bbox_minx': layer.bbox_minx, 'bbox_miny': layer.bbox_miny,
            'bbox_maxx': layer.bbox_maxx, 'bbox_maxy': layer.bbox_maxy,
            'is_active': layer.is_active,
            'created_at': int(layer.created_at.timestamp() * 1000) if layer.created_at else current_ts,
            'updated_at': int(layer.updated_at.timestamp() * 1000) if layer.updated_at else current_ts,
        }
        if last_pulled_at == 0:
            created_items.append(item)
        else:
            updated_items.append(item)
    return {
        "changes": {"elevation_layers": {"created": created_items, "updated": updated_items, "deleted": []}},
        "timestamp": current_ts,
    }


# ============================================================================
# Point Elevation Query
# ============================================================================


# Redis cache (lazy init, shared across requests)
import redis.asyncio as redis
import json as _json

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            redis_url = settings.effective_redis_url
            _redis_client = redis.from_url(
                redis_url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
        except Exception:
            _redis_client = False
    return _redis_client if _redis_client is not False else None


async def _cache_get(r, key: str) -> dict | None:
    try:
        val = await r.get(key)
        return _json.loads(val) if val else None
    except Exception:
        return None


async def _cache_set(r, key: str, value: dict, ttl: int = 86400):
    try:
        await r.set(key, _json.dumps(value), ex=ttl)
    except Exception:
        pass


def _source_object(dem) -> dict:
    """Describe the DEM source that actually answered, for the response body."""
    category = (
        "copernicus_s3" if dem.service_type == "DOWNLOAD"
        else f"custom_wcs:{dem.country_code}"
    )
    return {
        "id": f"builtin:{dem.country_code}",
        "name": dem.country_name,
        "category": category,
        "is_bare_earth": True,
        "accuracy_v_m": None,
        "resolution_m": _parse_resolution(dem.resolution),
    }


def _point_unavailable(lat: float, lon: float, code: str, message: str) -> dict:
    return {
        "lat": lat,
        "lon": lon,
        "elevation_m": None,
        "status": "unavailable",
        "source": None,
        "error": {"code": code, "message": message},
    }


async def _wcs_point_sample(dem, lat: float, lon: float) -> float | None:
    """Fetch a small WCS grid and sample the point's pixel. None on failure."""
    url = build_wcs_url(dem, lat, lon)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers={"User-Agent": "Nekazari/2.0"})
        resp.raise_for_status()
    return sample_tiff_point(resp.content, lat, lon)


@router.get("/point")
async def get_elevation_point(
    lat: float,
    lon: float,
    purpose: PurposeEnum = PurposeEnum.auto,
    source: str = "auto",
    refresh: bool = Query(False, description="Bypass cache and fetch fresh"),
    _tenant_id: str = Depends(require_elevation_reader),
):
    """Return elevation (m) for a WGS84 point.

    elevation_m is null and status is "unavailable" when no DEM source can
    answer — never a fabricated 0.0. Cached 1h on success, 5m on failure.
    """
    lat_r = round(lat, 5)
    lon_r = round(lon, 5)

    r = await _get_redis()
    cache_key = f"elev:point:{source}:{purpose.value}:{lat_r}:{lon_r}"
    if r and not refresh:
        cached = await _cache_get(r, cache_key)
        if cached:
            return cached

    try:
        plan = point_source_plan(source, lat, lon)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    if not plan:
        result = _point_unavailable(
            lat, lon, "no_dem_coverage",
            f"Point ({lon}, {lat}) outside all DEM coverage areas",
        )
        if r:
            await _cache_set(r, cache_key, result, ttl=300)
        return result

    elevation = None
    answered = None
    errors: list[str] = []
    for dem in plan:
        try:
            if dem.service_type == "DOWNLOAD":
                value = await asyncio.to_thread(sample_copernicus_point, lat, lon)
            elif dem.service_type == "WCS":
                value = await _wcs_point_sample(dem, lat, lon)
            else:
                errors.append(
                    f"{dem.country_code}: service_type {dem.service_type} not point-queryable"
                )
                continue
        except Exception as e:
            errors.append(f"{dem.country_code}: {e}")
            continue
        if value is not None:
            elevation, answered = value, dem
            break
        errors.append(f"{dem.country_code}: no usable elevation at point")

    if answered is None:
        logger.warning(
            "elevation point unavailable at (%s, %s) source=%s: %s",
            lat, lon, source, "; ".join(errors),
        )
        result = _point_unavailable(
            lat, lon, "source_unavailable",
            f"No DEM source could answer: {'; '.join(errors)}",
        )
        if r:
            await _cache_set(r, cache_key, result, ttl=300)
        return result

    result = {
        "lat": lat,
        "lon": lon,
        "elevation_m": round(elevation, 2),
        "status": "ok",
        "source": _source_object(answered),
        "error": None,
    }
    if r:
        await _cache_set(r, cache_key, result, ttl=3600)
    return result


# ---------------------------------------------------------------------------
# Raster grid endpoint — builds a DEM grid dict for pathfinding consumers
# ---------------------------------------------------------------------------

async def _wcs_bbox_query(source: dict, bbox: tuple, width: int, height: int) -> bytes:
    """WCS GetCoverage for a full bbox returning raw GeoTIFF bytes."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = WCS_PARAMS.get(source.get("country_code", ""), {})
    version = params.get("VERSION", "2.0.1")
    coverage = source.get("layer_name") or "elevation"

    if version == "1.0.0":
        fmt = params.get("FORMAT", source.get("format", "GEOTIFFINT16"))
        crs = params.get("CRS", "EPSG:4326")
        coverage_param = params.get("COVERAGE_PARAM", "COVERAGE")
        bbox_str = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        url = (
            f"{source['service_url']}?"
            f"SERVICE=WCS&VERSION=1.0.0&REQUEST=GetCoverage&"
            f"{coverage_param}={coverage}&FORMAT={fmt}&"
            f"BBOX={bbox_str}&CRS={crs}&WIDTH={width}&HEIGHT={height}"
        )
    else:
        url = (
            f"{source['service_url']}?"
            f"SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&"
            f"COVERAGEID={coverage}&FORMAT=image/tiff&"
            f"SUBSET=Long({min_lon},{max_lon})&SUBSET=Lat({min_lat},{max_lat})"
            f"&SCALEFACTOR=1"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"User-Agent": "Nekazari/2.0"})
        resp.raise_for_status()
        return resp.content


@router.get("/raster")
async def get_elevation_raster(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    resolution_m: float = 10,
    purpose: PurposeEnum = PurposeEnum.auto,
    _tenant_id: str = Depends(require_elevation_reader),
):
    """Return a DEM grid dict {elevations, origin_lon, origin_lat, pixel_size_deg, cols, rows}
    for the requested bounding box. Uses the best available DEM source."""
    bbox = (min_lon, min_lat, max_lon, max_lat)

    # Find a DEM source covering the bbox centre
    centre_lat = (min_lat + max_lat) / 2.0
    centre_lon = (min_lon + max_lon) / 2.0
    dem = resolve_source(centre_lat, centre_lon)
    if not dem:
        raise HTTPException(status_code=404, detail={
            "error": "no_dem_coverage",
            "message": f"Bbox centre ({centre_lon}, {centre_lat}) outside all DEM coverage areas"
        })

    # Resolve coverage by requested resolution (e.g. ES: 5m -> Elevacion4258_5,
    # 25m -> Elevacion4258_25). Falls back to the source's default layer.
    from app.dem_sources import coverage_for_resolution
    chosen_layer = coverage_for_resolution(dem.country_code, resolution_m) or dem.layer_name

    source_dict = {
        "service_url": dem.service_url,
        "layer_name": chosen_layer,
        "format": dem.format,
        "country_code": dem.country_code,
    }

    # Compute grid dimensions
    pixel_size_deg = resolution_m / 111320.0
    cols = max(2, int(round((max_lon - min_lon) / pixel_size_deg)))
    rows = max(2, int(round((max_lat - min_lat) / pixel_size_deg)))
    # Recalculate from integer dimensions for consistent step
    pixel_size_deg = (max_lon - min_lon) / cols

    try:
        raw = await _wcs_bbox_query(source_dict, bbox, cols, rows)
        with rasterio.open(io.BytesIO(raw)) as ds:
            band = ds.read(1)
            elevations = band.tolist()
    except httpx.HTTPError as e:
        logger.error("WCS raster query failed: %s", e)
        raise HTTPException(status_code=502, detail={
            "error": "wcs_unavailable",
            "message": f"DEM source {dem.country_code} unreachable"
        })
    except Exception as e:
        logger.error("Failed to build raster grid from %s: %s", dem.country_code, e)
        raise HTTPException(status_code=502, detail={
            "error": "invalid_response",
            "message": f"Could not parse elevation raster from {dem.country_code}"
        })

    return {
        "elevations": elevations,
        "origin_lon": min_lon,
        "origin_lat": min_lat,
        "pixel_size_deg": pixel_size_deg,
        "cols": cols,
        "rows": rows,
        "source": {
            "id": f"builtin:{dem.country_code}",
            "name": dem.country_name,
            "category": f"custom_wcs:{dem.country_code}",
            "resolution_m": _parse_resolution(dem.resolution),
        },
    }


# ============================================================================
# Terrain Tile Serving (Quantized Mesh from MinIO → Cesium)
# ============================================================================
# Serves pre-ingested Cesium Quantized Mesh tiles (.terrain) and layer.json
# from the terrain-tilesets MinIO bucket. Tiles are generated by the elevation
# worker (Celery tasks) and uploaded by process_dem_to_quantized_mesh.

# Shared S3 client (lazy init, shared across tile requests)
from app.services.s3_client import get_s3_client, list_prefixes  # noqa: E402


# ── Composite tileset helpers ─────────────────────────────────────

def _try_get_layer_json(s3, bucket: str, prefix: str) -> dict | None:
    """Try to fetch and parse a single tileset's layer.json.
    
    Returns the parsed layer.json dict, or None if not found.
    """
    try:
        resp = s3.get_object(Bucket=bucket, Key=f"{prefix}/layer.json")
        return json.loads(resp["Body"].read())
    except Exception:
        return None


def _merge_layer_json(
    country: str,
    subs: list[str],
    sub_layers: list[dict],
    s3,
    bucket: str,
) -> dict:
    """Merge multiple tileset layer.json files into a single composite.

    Each sub-tileset (e.g., EU_56_-7, EU_55_-8) contributes its available
    tile ranges. The merged layer.json exposes a unified tile URL template
    pointing to the composite country prefix (e.g., terrain/EU/).

    Tile requests for individual tiles are resolved by the tile endpoint
    which checks sub-tilesets when the composite prefix has no tile.
    """
    merged_available: dict[int, list[tuple[int, int]]] = {}
    min_zoom = 99
    max_zoom = 0
    bounds: list[float] = [180, 90, -180, -90]  # west, south, east, north — expand

    # Collect names/descriptions from the first tileset for metadata
    name = f"Nekazari {country} Composite Terrain"
    description = f"Composite terrain for {country} ({len(subs)} sub-tilesets)"
    description_subs = []

    for sub_prefix, sub_layer in zip(subs, sub_layers):
        if sub_layer is None:
            continue

        # Expand bounding box
        sub_bounds = sub_layer.get("bounds", [])
        if len(sub_bounds) == 4:
            bounds[0] = min(bounds[0], sub_bounds[0])  # west
            bounds[1] = min(bounds[1], sub_bounds[1])  # south
            bounds[2] = max(bounds[2], sub_bounds[2])  # east
            bounds[3] = max(bounds[3], sub_bounds[3])  # north

        # Track zoom range
        z_min = sub_layer.get("minzoom", 0)
        z_max = sub_layer.get("maxzoom", 0)
        if z_min < min_zoom:
            min_zoom = z_min
        if z_max > max_zoom:
            max_zoom = z_max

        # Merge available tile ranges
        available = sub_layer.get("available", [])
        for zi, z_tiles in enumerate(available):
            z = z_min + zi
            if z not in merged_available:
                merged_available[z] = []
            if isinstance(z_tiles, list):
                for tile_range in z_tiles:
                    if isinstance(tile_range, dict):
                        merged_available[z].extend([
                            (x, y)
                            for x in range(tile_range.get("startX", 0), tile_range.get("endX", 0) + 1)
                            for y in range(tile_range.get("startY", 0), tile_range.get("endY", 0) + 1)
                        ])

        description_subs.append(sub_prefix.replace("terrain/", ""))

    if description_subs:
        description += f": {', '.join(sorted(description_subs))}"

    if not merged_available:
        return None

    # Format available back to ranges
    available_formatted = []
    for z in range(min_zoom, max_zoom + 1):
        tiles = merged_available.get(z, [])
        if not tiles:
            available_formatted.append([])
            continue
        cols = sorted(set(t[0] for t in tiles))
        rows = sorted(set(t[1] for t in tiles))
        available_formatted.append([{
            "startX": min(cols), "startY": min(rows),
            "endX": max(cols), "endY": max(rows)
        }])

    return {
        "tilejson": "2.1.0",
        "name": name,
        "description": description,
        "version": "1.0.0",
        "format": "quantized-mesh-1.0",
        "scheme": "tms",
        "tiles": ["{z}/{x}/{y}.terrain"],
        "projection": "EPSG:4326",
        "bounds": bounds,
        "minzoom": min_zoom if min_zoom != 99 else 0,
        "maxzoom": max_zoom,
        "available": available_formatted,
        "extensions": ["octvertexnormals"],
        "_composite": True,
        "_sub_tilesets": sorted(description_subs),
    }


def _find_sub_tilesets(s3, bucket: str, country: str) -> list[str]:
    """Find all tilesets under terrain/ that start with {country}_

    E.g., for country='EU' returns prefixes like:
      ['terrain/EU_56_-7', 'terrain/EU_55_-8', ...]

    Results are cached in memory with a 300s TTL — the tileset list changes
    only when new tiles are ingested, which is infrequent.
    """
    return list_prefixes(s3, bucket, f"terrain/{country}_")


# ══════════════════════════════════════════════════════════════════

@router.get("/terrain/{country}/layer.json")
async def get_terrain_layer_json(country: str):
    """Serve the Cesium layer.json for an ingested terrain tileset.

    Resolves tilesets using this order:
    1. Exact match (e.g., terrain/ES/ → terrain/ES/layer.json)
    2. Composite from sub-tilesets (e.g., terrain/EU_* for 'EU')

    Tileset names correspond to the 'country_code' used during ingestion:
      ES       → Spain (IGN PNOA or Copernicus fallback)
      EU_56_-7 → Copernicus GLO-30 single tile (Scotland, lat 56 lon -7)
      EU       → Composite of all EU_* tiles (auto-generated)
    """
    try:
        s3 = get_s3_client()
    except Exception:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    bucket = os.getenv("MINIO_BUCKET", "terrain-tilesets")
    key = f"terrain/{country}/layer.json"

    # ── Try exact match first ─────────────────────────────
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        data = resp["Body"].read()
        return JSONResponse(
            content=json.loads(data),
            media_type="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            },
        )
    except Exception:
        pass  # Fall through to composite resolution

    # ── Try composite from sub-tilesets ───────────────────
    subs = _find_sub_tilesets(s3, bucket, country)
    if subs:
        sub_layers = [_try_get_layer_json(s3, bucket, p) for p in subs]
        composite = _merge_layer_json(country, subs, sub_layers, s3, bucket)
        if composite:
            logger.info(f"Serving composite layer.json for '{country}' from {len(subs)} sub-tilesets")
            return JSONResponse(
                content=composite,
                media_type="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=600",  # Shorter TTL for composites
                },
            )

    # ── Not found (exact or composite) ────────────────────
    raise HTTPException(
        status_code=404,
        detail={
            "error": "tileset_not_found",
            "message": (
                f"No terrain tileset found for '{country}'. "
                f"Ingest terrain first via POST /api/elevation/ingest "
                f"or run the Copernicus bootstrap script."
            ),
            "available_tilesets": _list_available_tilesets(s3, bucket),
        },
    )


@router.get("/terrain/{country}/{z}/{x}/{y}.terrain")
async def get_terrain_tile(
    country: str,
    z: int,
    x: int,
    y: int,
):
    """Serve a single Cesium Quantized Mesh terrain tile.

    Cesium fetches tiles on-demand as the camera moves. Each tile is a
    gzip-compressed Quantized Mesh binary covering one tile at the given
    zoom level in the TMS geographic scheme (EPSG:4326).

    Resolves tiles using this order:
    1. Exact path: terrain/{country}/{z}/{x}/{y}.terrain
    2. Sub-tileset: terrain/{country}_*/{z}/{x}/{y}.terrain (for composites)

    The tile coordinates (z/x/y) follow the Cesium Geographic Tiling scheme:
      - (0, 0, 0) at zoom 0 covers the whole world
      - x = column (0 = westmost), y = row (0 = southmost)
    """
    try:
        s3 = get_s3_client()
    except Exception:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    bucket = os.getenv("MINIO_BUCKET", "terrain-tilesets")
    key = f"terrain/{country}/{z}/{x}/{y}.terrain"

    # ── Try exact path ────────────────────────────────────
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        data = gzip.decompress(resp["Body"].read())
        return Response(
            content=data,
            media_type="application/vnd.quantized-mesh",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400, immutable",
            },
        )
    except Exception:
        pass  # Fall through to sub-tileset search

    # ── Try sub-tilesets ──────────────────────────────────
    # Optimisation: compute likely Copernicus tiles from the Cesium tile
    # coordinates instead of listing all 1721+ EU_* prefixes.
    # Cesium tile bounds → Copernicus 1°×1° tile prefixes (max 4).

    # Compute tile geographic bounds
    num_cols = 2 ** (z + 1)
    num_rows = 2 ** z
    tile_west = -180.0 + x * (360.0 / num_cols)
    tile_south = -90.0 + y * (180.0 / num_rows)
    tile_east = tile_west + (360.0 / num_cols)
    tile_north = tile_south + (180.0 / num_rows)

    # Determine which Copernicus 1° tiles cover this area
    import math
    lat_start = int(math.floor(tile_south))
    lat_end = int(math.floor(tile_north))
    lon_start = int(math.floor(tile_west))
    lon_end = int(math.floor(tile_east))

    # Try each candidate prefix (≤4)
    for lat in range(lat_start, lat_end + 1):
        for lon in range(lon_start, lon_end + 1):
            # Prefix format: terrain/EU_{lat}_{lon} where lat/lon are signed ints
            # e.g., terrain/EU_48_2, terrain/EU_51_0, terrain/EU_56_-7
            cand = f"terrain/{country}_{lat}_{lon}"
            cand_key = f"{cand}/{z}/{x}/{y}.terrain"
            try:
                resp = s3.get_object(Bucket=bucket, Key=cand_key)
                data = gzip.decompress(resp["Body"].read())
                return Response(
                    content=data,
                    media_type="application/vnd.quantized-mesh",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "public, max-age=86400, immutable",
                    },
                )
            except Exception:
                continue

    # Fallback: full sub-tileset search (only if computed prefixes failed)
    subs = _find_sub_tilesets(s3, bucket, country)
    for sub_prefix in subs:
        sub_key = f"{sub_prefix}/{z}/{x}/{y}.terrain"
        try:
            resp = s3.get_object(Bucket=bucket, Key=sub_key)
            data = gzip.decompress(resp["Body"].read())
            return Response(
                content=data,
                media_type="application/vnd.quantized-mesh",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=86400, immutable",
                },
            )
        except Exception:
            continue

    # ── No tile found anywhere ────────────────────────────
    return Response(status_code=204)


@router.get("/heightmap/{z}/{x}/{y}.png")
def get_heightmap_tile(z: int, x: int, y: int):
    """Serve a terrarium-encoded heightmap PNG (WebMercator z/x/y), on demand.

    Sovereign replacement for the bulk terrain pre-ingestion: the tile is
    generated live from the public Copernicus GLO-30 S3 COGs via windowed
    reads and cached write-through to MinIO — only tiles actually viewed
    are ever stored.

    Served zoom window is [MIN_ZOOM, MAX_ZOOM] (see services/heightmap.py).
    Below MIN_ZOOM the COG cover explodes; the frontend delegates those
    levels to a global open-data fallback.

    Sync def on purpose: rasterio/PIL are blocking; FastAPI runs this in
    the threadpool.
    """
    from app.services.heightmap import (
        MAX_ZOOM,
        MIN_ZOOM,
        generate_heightmap_png,
        get_cached_tile,
        store_tile,
    )

    n = 2 ** z
    if not (MIN_ZOOM <= z <= MAX_ZOOM) or not (0 <= x < n) or not (0 <= y < n):
        raise HTTPException(status_code=404, detail="heightmap zoom/x/y out of supported range")

    cached = get_cached_tile(z, x, y)
    if cached is not None:
        return Response(
            content=cached,
            media_type="image/png",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=2592000, immutable",
            },
        )

    png = generate_heightmap_png(z, x, y)
    if png is None:
        # Too many covering COGs or no data — signal unavailability so the
        # client can fall back (Cesium upsamples from the parent level).
        raise HTTPException(status_code=404, detail="heightmap tile not generatable")

    store_tile(z, x, y, png)  # best-effort write-through
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=2592000, immutable",
        },
    )


def _list_available_tilesets(s3, bucket: str) -> list[str]:
    """List available terrain tilesets in MinIO for user guidance.

    Delegates to the shared list_prefixes helper.  Preserved as a thin
    wrapper for backward compatibility with existing callers/tests.
    """
    return list_prefixes(s3, bucket, "terrain/", strip_prefix="terrain/")
