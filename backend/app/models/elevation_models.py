"""
SQLAlchemy models for EU Elevation module.

Models:
- ElevationLayer: Pre-built terrain tilesets (Cesium Quantized Mesh)
- CustomDemSource: User-registered WCS/WMS/GeoTIFF sources for ingestion
- TenantTerrainPreferences: BYOK tokens + provider selection per tenant
"""

import uuid
from sqlalchemy import Column, String, Float, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.database import Base


class ElevationLayer(Base):
    """
    Pre-built Cesium Quantized Mesh tileset URL.
    Populated by the ingestion pipeline when a user processes a DEM source.
    """
    __tablename__ = "elevation_layers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, index=True, nullable=False)

    name = Column(String, nullable=False)
    url = Column(String, nullable=False)

    # BBOX for auto-matching
    bbox_minx = Column(Float, nullable=True)
    bbox_miny = Column(Float, nullable=True)
    bbox_maxx = Column(Float, nullable=True)
    bbox_maxy = Column(Float, nullable=True)

    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CustomDemSource(Base):
    """
    User-registered custom DEM source for ingestion pipeline.
    Stores WCS/WMS/GeoTIFF endpoints with optional auth headers.
    """
    __tablename__ = "custom_dem_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, index=True, nullable=False)

    name = Column(String, nullable=False)
    country_code = Column(String, nullable=True)

    service_url = Column(String, nullable=False)
    service_type = Column(String, nullable=False, default="WCS")
    format = Column(String, nullable=False, default="GeoTIFF")
    resolution = Column(String, nullable=True)
    layer_name = Column(String, nullable=True)

    bbox_minx = Column(Float, nullable=True)
    bbox_miny = Column(Float, nullable=True)
    bbox_maxx = Column(Float, nullable=True)
    bbox_maxy = Column(Float, nullable=True)

    # Optional auth (stored encrypted at rest in production)
    auth_header_name = Column(String, nullable=True)
    auth_header_value = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TenantTerrainPreferences(Base):
    """
    Per-tenant terrain provider preferences and BYOK tokens.
    Controls which provider tier is active and stores API keys.
    """
    __tablename__ = "tenant_terrain_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, index=True, nullable=False, unique=True)

    # Active provider tier
    provider_type = Column(String, nullable=False, default="off")
    # Values: "off" | "cesium_world" | "maptiler" | "custom" | "auto"

    # BYOK tokens (encrypted at rest in production via K8s secrets)
    cesium_ion_token = Column(Text, nullable=True)
    maptiler_api_key = Column(Text, nullable=True)

    # Custom terrain URL (for self-hosted or third-party providers)
    custom_terrain_url = Column(String, nullable=True)

    # Auto mode: use BBOX to match against elevation_layers
    auto_mode = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
