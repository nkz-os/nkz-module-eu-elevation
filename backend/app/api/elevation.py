"""
EU Elevation API endpoints.

Provides REST endpoints for:
- Listing pre-configured EU/UK DEM sources
- Initiating BBOX-based terrain ingestion (auto-resolves source by country code)
- Querying ingestion job status
- Managing custom terrain layers per tenant
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
from app.dem_sources import get_source, get_all_sources, get_sources_for_bbox, DEMSource
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.elevation_models import ElevationLayer
import uuid

logger = logging.getLogger(__name__)

router = APIRouter()

# ============================================================================
# Request/Response Models
# ============================================================================

class DEMSourceResponse(BaseModel):
    """Schema for a pre-configured DEM source."""
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
    """Schema for creating a new custom elevation layer."""
    name: str = Field(..., description="Display name for the terrain provider")
    url: str = Field(..., description="Base URL of the Cesium Terrain Provider")
    bbox_minx: Optional[float] = None
    bbox_miny: Optional[float] = None
    bbox_maxx: Optional[float] = None
    bbox_maxy: Optional[float] = None
    is_active: bool = True

class ElevationLayerResponse(ElevationLayerCreate):
    """Schema for returning an elevation layer."""
    id: uuid.UUID
    tenant_id: str

    class Config:
        from_attributes = True

class BboxIngestRequest(BaseModel):
    """Request to start Elevation processing for a specific BBOX."""
    country_code: str = Field(
        ...,
        description="ISO country code (e.g. 'ES', 'GB', 'NL') — auto-resolves WCS source from catalog"
    )
    bbox: Optional[tuple[float, float, float, float]] = Field(
        None,
        description="Optional BBOX override (west, south, east, north) in EPSG:4326. "
                    "If omitted, uses full country BBOX from catalog."
    )
    source_urls: Optional[List[str]] = Field(
        None,
        description="Optional override: custom WCS/GeoTIFF URLs. "
                    "If omitted, uses pre-configured source from catalog."
    )
    zoom_min: int = Field(8, ge=0, le=15, description="Minimum zoom level")
    zoom_max: int = Field(14, ge=0, le=15, description="Maximum zoom level")
    max_error: float = Field(0.5, gt=0, le=10, description="pydelatin max error for mesh decimation")


class ProcessResponse(BaseModel):
    """Response from starting an ingestion job."""
    job_id: str
    status: str
    message: str
    source: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response for job status query."""
    job_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


# ============================================================================
# DEM Source Catalog (read-only)
# ============================================================================

@router.get("/sources", response_model=List[DEMSourceResponse])
async def list_dem_sources(
    current_user: dict = Depends(require_auth)
):
    """
    List all pre-configured EU/UK DEM data sources.

    These are the national and pan-European WCS/WMS endpoints
    that the ingestion pipeline can download elevation data from.
    """
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
            requires_preprocessing=s.requires_preprocessing
        )
        for s in sources
    ]


@router.get("/sources/{country_code}", response_model=DEMSourceResponse)
async def get_dem_source(
    country_code: str,
    current_user: dict = Depends(require_auth)
):
    """Get a specific DEM source by ISO country code."""
    src = get_source(country_code)
    if not src:
        raise HTTPException(
            status_code=404,
            detail=f"No DEM source found for country code '{country_code}'. "
                   f"Use GET /sources to list available sources."
        )
    return DEMSourceResponse(
        country_code=src.country_code,
        country_name=src.country_name,
        service_url=src.service_url,
        service_type=src.service_type,
        format=src.format,
        resolution=src.resolution,
        bbox=src.bbox,
        layer_name=src.layer_name,
        notes=src.notes,
        fallback=src.fallback,
        requires_preprocessing=src.requires_preprocessing
    )


# ============================================================================
# Ingestion Endpoints
# ============================================================================

@router.post("/ingest", response_model=ProcessResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_ingestion(
    request: BboxIngestRequest,
    current_user: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id)
):
    """
    Start Elevation (Quantized Mesh) processing for a country/region.

    Auto-resolves the WCS/WMS endpoint from the pre-configured catalog
    using the country_code. Override with source_urls if needed.

    Returns immediately with job ID for WebSocket status polling.
    """
    # Resolve source from catalog
    dem_source = get_source(request.country_code)
    source_urls = request.source_urls

    if not source_urls:
        if not dem_source:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown country code '{request.country_code}' and no source_urls provided. "
                       f"Use GET /sources to list available country codes."
            )
        source_urls = [dem_source.service_url]

    # Resolve BBOX: use request override or catalog default
    bbox = request.bbox
    if not bbox:
        if dem_source:
            bbox = dem_source.bbox
        else:
            raise HTTPException(
                status_code=400,
                detail="BBOX is required when using custom source_urls without a known country_code."
            )

    source_label = dem_source.country_name if dem_source else "custom"
    logger.info(
        f"Ingestion request: {request.country_code} ({source_label}) "
        f"BBOX={bbox} zoom={request.zoom_min}-{request.zoom_max} "
        f"by tenant {tenant_id}"
    )

    try:
        task = process_dem_to_quantized_mesh.delay(
            request.country_code,
            source_urls,
            bbox,
            request.zoom_min,
            request.zoom_max,
            request.max_error
        )

        logger.info(f"Ingestion job enqueued (Celery Task ID: {task.id})")

        return ProcessResponse(
            job_id=task.id,
            status="queued",
            message=f"Ingestion job for {source_label} queued. "
                    f"Connect to WS /api/elevation/ws/status/{task.id} for live updates.",
            source=dem_source.service_url if dem_source else source_urls[0]
        )

    except Exception as e:
        logger.error(f"Failed to enqueue ingestion job: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Processing queue unavailable. Please try again later."
        )


@router.post("/upload", response_model=ProcessResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_dem(
    file: UploadFile = File(...),
    country_code: str = Form(...),
    bbox: Optional[str] = Form(None, description="Comma-separated optional bbox: west,south,east,north"),
    zoom_min: int = Form(8),
    zoom_max: int = Form(14),
    current_user: dict = Depends(require_auth),
    tenant_id: str = Depends(get_tenant_id)
):
    """
    Upload a local DEM (GeoTIFF, ASC) for immediate Quantized Mesh conversion.
    Saves file to temp directory and triggers local pipeline worker.
    """
    logger.info(f"Local file upload: {file.filename} (Tenant: {tenant_id})")

    if not file.filename.lower().endswith(('.tif', '.tiff', '.asc')):
        raise HTTPException(status_code=400, detail="Only .tif, .tiff, or .asc files are supported")

    # Save to ephemeral temp dir (not MinIO — worker will process and upload result)
    upload_dir = os.path.join(tempfile.gettempdir(), "terrain_uploads", country_code)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, file.filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Could not save file")

    # Parse BBOX if provided
    parsed_bbox = None
    if bbox:
        try:
            parts = [float(x.strip()) for x in bbox.split(',')]
            if len(parts) == 4:
                parsed_bbox = tuple(parts)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid BBOX format. Use: west,south,east,north")

    try:
        task = process_local_dem_to_quantized_mesh.delay(
            country_code,
            file_path,
            parsed_bbox,
            zoom_min,
            zoom_max
        )
        return ProcessResponse(
            job_id=task.id,
            status="queued",
            message="Upload job queued. Process will begin shortly."
        )
    except Exception as e:
        logger.error(f"Failed to enqueue upload job: {e}")
        raise HTTPException(status_code=503, detail="Processing queue unavailable.")


# ============================================================================
# Job Status
# ============================================================================

@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    current_user: dict = Depends(require_auth)
):
    """Get status of a Celery processing job (polling)."""
    from celery.result import AsyncResult
    from app.worker import celery_app

    task_result = AsyncResult(job_id, app=celery_app)

    response = JobStatusResponse(
        job_id=job_id,
        status=task_result.status,
        result=task_result.info if isinstance(task_result.info, dict) else None
    )

    if task_result.successful():
        response.result = task_result.result
    elif task_result.failed():
        response.error = str(task_result.result)

    return response


@router.websocket("/ws/status/{job_id}")
async def websocket_job_status(websocket: WebSocket, job_id: str):
    """Real-time WebSocket stream for Celery job status and progress."""
    await websocket.accept()
    from celery.result import AsyncResult
    from app.worker import celery_app

    task_result = AsyncResult(job_id, app=celery_app)

    try:
        while True:
            state = task_result.state
            info = task_result.info

            payload = {
                "job_id": job_id,
                "status": state,
                "progress": 0,
                "message": ""
            }

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
        logger.info(f"WebSocket client disconnected from job {job_id}")
    except Exception as e:
        logger.error(f"WebSocket error for job {job_id}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass

# ============================================================================
# Dynamic Multi-Tenant Terrain Layers
# ============================================================================

@router.get("/layers", response_model=List[ElevationLayerResponse])
async def get_elevation_layers(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth)
):
    """Get all configured elevation layers for the current tenant."""
    return db.query(ElevationLayer).filter(ElevationLayer.tenant_id == tenant_id).all()


@router.post("/layers", response_model=ElevationLayerResponse, status_code=status.HTTP_201_CREATED)
async def create_elevation_layer(
    layer_in: ElevationLayerCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth)
):
    """Create a new custom elevation layer for the current tenant."""
    new_layer = ElevationLayer(
        tenant_id=tenant_id,
        name=layer_in.name,
        url=layer_in.url,
        bbox_minx=layer_in.bbox_minx,
        bbox_miny=layer_in.bbox_miny,
        bbox_maxx=layer_in.bbox_maxx,
        bbox_maxy=layer_in.bbox_maxy,
        is_active=layer_in.is_active
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
    current_user: dict = Depends(require_auth)
):
    """Delete a custom elevation layer."""
    layer = db.query(ElevationLayer).filter(
        ElevationLayer.id == layer_id,
        ElevationLayer.tenant_id == tenant_id
    ).first()

    if not layer:
        raise HTTPException(status_code=404, detail="Elevation layer not found")

    db.delete(layer)
    db.commit()
    return None

@router.get("/sync/vectorial")
async def sync_vectorial(
    last_pulled_at: int = 0,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
    current_user: dict = Depends(require_auth)
):
    """
    Standard Offline Vector Sync Endpoint for the eu-elevation module.
    Returns WatermelonDB-compatible JSON for elevation_layers.
    """
    current_ts = int(time.time() * 1000)
    
    query = db.query(ElevationLayer).filter(ElevationLayer.tenant_id == tenant_id)
    if last_pulled_at > 0:
        # Convert ms timestamp to datetime 
        last_dt = datetime.fromtimestamp(last_pulled_at / 1000.0, tz=timezone.utc)
        # Assuming SQLAlchemy comparison with timezone-aware datetime works
        query = query.filter(ElevationLayer.updated_at >= last_dt)
        
    layers = query.all()
    
    updated_items = []
    created_items = []
    
    for layer in layers:
        item = {
            'remote_id': str(layer.id),
            'id': str(layer.id),
            'name': layer.name,
            'url': layer.url,
            'bbox_minx': layer.bbox_minx,
            'bbox_miny': layer.bbox_miny,
            'bbox_maxx': layer.bbox_maxx,
            'bbox_maxy': layer.bbox_maxy,
            'is_active': layer.is_active,
            'created_at': int(layer.created_at.timestamp() * 1000) if layer.created_at else current_ts,
            'updated_at': int(layer.updated_at.timestamp() * 1000) if layer.updated_at else current_ts
        }
        
        if last_pulled_at == 0:
            created_items.append(item)
        else:
            updated_items.append(item)
            
    return {
        "changes": {
            "elevation_layers": {
                "created": created_items,
                "updated": updated_items,
                "deleted": []
            }
        },
        "timestamp": current_ts
    }
