"""
EU Elevation API endpoints — SOTA multi-tier terrain provider.

Tiers:
  - Tier 0: Built-in providers (Cesium World Terrain, MapTiler)
  - Tier 1: Custom DEM sources (user-registered WCS/WMS/GeoTIFF)
  - Tier 2: Ingested layers (quantized mesh tiles in MinIO)
"""

import logging
import asyncio
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.middleware.auth import require_auth, get_tenant_id
from app.tasks.elevation_tasks import process_dem_to_quantized_mesh, process_local_dem_to_quantized_mesh
from app.dem_sources import get_source, get_all_sources
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.elevation_models import ElevationLayer, CustomDemSource, TenantTerrainPreferences
import uuid

logger = logging.getLogger(__name__)

router = APIRouter()

# ============================================================================
# Built-in terrain providers (Tier 0 — no ingestion needed)
# ============================================================================

BUILTIN_PROVIDERS = [
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
    provider_type: str = Field("off", description="off, cesium_world, maptiler, custom, auto")
    cesium_ion_token: Optional[str] = None
    maptiler_api_key: Optional[str] = None
    custom_terrain_url: Optional[str] = None
    auto_mode: bool = True


class TerrainPreferencesResponse(BaseModel):
    tenant_id: str
    provider_type: str
    has_cesium_token: bool = False
    has_maptiler_key: bool = False
    custom_terrain_url: Optional[str] = None
    auto_mode: bool = True


class TerrainTokensResponse(BaseModel):
    """Returns actual token values for the authenticated tenant's own preferences."""
    cesium_ion_token: Optional[str] = None
    maptiler_api_key: Optional[str] = None
    custom_terrain_url: Optional[str] = None
    provider_type: str = "off"


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
# DEM Source Catalog (read-only, built-in)
# ============================================================================

@router.get("/sources", response_model=List[DEMSourceResponse])
async def list_dem_sources(current_user: dict = Depends(require_auth)):
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


@router.get("/sources/custom", response_model=List[CustomDemSourceResponse])
async def list_custom_sources(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """List all custom DEM sources registered by the current tenant."""
    sources = db.query(CustomDemSource).filter(
        CustomDemSource.tenant_id == tenant_id
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
async def get_dem_source(country_code: str, current_user: dict = Depends(require_auth)):
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """Register a new custom DEM source for ingestion."""
    new_source = CustomDemSource(
        tenant_id=tenant_id,
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
        auth_header_value=source_in.auth_header_value,
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    source = db.query(CustomDemSource).filter(
        CustomDemSource.id == source_id,
        CustomDemSource.tenant_id == tenant_id,
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
    current_user: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id),
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
    logger.info(f"Ingestion: {request.country_code} ({source_label}) BBOX={bbox} tenant={tenant_id}")

    try:
        task = process_dem_to_quantized_mesh.delay(
            request.country_code, source_urls, bbox,
            request.zoom_min, request.zoom_max, request.max_error,
        )
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
    current_user: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id),
):
    logger.info(f"Local upload: {file.filename} tenant={tenant_id}")
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
async def get_job_status(job_id: str, current_user: dict = Depends(require_auth)):
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """Get all ingested elevation layers for the current tenant."""
    return db.query(ElevationLayer).filter(
        ElevationLayer.tenant_id == tenant_id
    ).all()


@router.post("/layers", response_model=ElevationLayerResponse, status_code=status.HTTP_201_CREATED)
async def create_elevation_layer(
    layer_in: ElevationLayerCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    new_layer = ElevationLayer(
        tenant_id=tenant_id, name=layer_in.name, url=layer_in.url,
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    layer = db.query(ElevationLayer).filter(
        ElevationLayer.id == layer_id, ElevationLayer.tenant_id == tenant_id,
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """List all available terrain providers with their active status."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == tenant_id,
    ).first()
    active_type = prefs.provider_type if prefs else "off"

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
        ElevationLayer.tenant_id == tenant_id, ElevationLayer.is_active,
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """Get current tenant terrain preferences (tokens masked)."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == tenant_id,
    ).first()
    if not prefs:
        return TerrainPreferencesResponse(tenant_id=tenant_id)
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """Return actual token values for the authenticated tenant. Used by ElevationLayer slot."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == tenant_id,
    ).first()
    if not prefs:
        return TerrainTokensResponse()
    return TerrainTokensResponse(
        cesium_ion_token=prefs.cesium_ion_token,
        maptiler_api_key=prefs.maptiler_api_key,
        custom_terrain_url=prefs.custom_terrain_url,
        provider_type=prefs.provider_type,
    )


@router.put("/preferences", response_model=TerrainPreferencesResponse)
async def update_preferences(
    prefs_in: TerrainPreferencesUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    """Update tenant terrain preferences and BYOK tokens."""
    prefs = db.query(TenantTerrainPreferences).filter(
        TenantTerrainPreferences.tenant_id == tenant_id,
    ).first()

    if not prefs:
        prefs = TenantTerrainPreferences(tenant_id=tenant_id)
        db.add(prefs)

    if prefs_in.provider_type is not None:
        prefs.provider_type = prefs_in.provider_type
    if prefs_in.cesium_ion_token is not None:
        prefs.cesium_ion_token = prefs_in.cesium_ion_token
    if prefs_in.maptiler_api_key is not None:
        prefs.maptiler_api_key = prefs_in.maptiler_api_key
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
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth),
):
    current_ts = int(time.time() * 1000)
    query = db.query(ElevationLayer).filter(ElevationLayer.tenant_id == tenant_id)
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
