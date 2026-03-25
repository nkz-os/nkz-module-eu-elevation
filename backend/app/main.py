"""
FastAPI main application for EU Elevation Module.
"""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import elevation
from app.db import init_db

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# CORS: explicit whitelist from env var, never wildcard
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting EU Elevation Module API...")

    # Initialize database tables
    try:
        init_db()
        logger.info("Database tables initialized")
    except Exception as e:
        logger.warning(f"Database initialization warning: {e}")

    yield

    logger.info("Shutting down EU Elevation Module API...")


# Create FastAPI app
app = FastAPI(
    title="EU Elevation Module API",
    description="""
    EU 3D Elevation and Terrain processing for Nekazari Platform.

    ## Features
    - BBOX Selective Ingestion of WCS/GeoTIFF
    - Generation of Quantized Mesh (.terrain)
    - Geometry Decimation
    - Static Hosting via MinIO
    """,
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware — explicit origins only
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
    )
else:
    logger.warning("ALLOWED_ORIGINS not set — CORS middleware disabled. Set it for production.")

# Include routers
app.include_router(elevation.router, prefix="/api/elevation", tags=["Elevation Processing"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "module": "eu-elevation",
        "version": "1.0.0"
    }


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "module": "nkz-module-eu-elevation",
        "version": "1.0.0",
        "description": "EU Elevation Data Processing Module for Nekazari",
        "docs": "/docs",
        "health": "/health"
    }
