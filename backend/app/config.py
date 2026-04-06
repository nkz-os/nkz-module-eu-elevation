"""
Configuration module for EU Elevation backend.
Loads settings from environment variables. No hardcoded defaults for secrets.
"""

import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database (PostgreSQL) — NO hardcoded credentials (public repo)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Redis (for Celery task queue)
    REDIS_URL: str = "redis://redis:6379/0"

    # MinIO / S3 Storage — NO hardcoded credentials (public repo)
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "terrain-tilesets")
    MINIO_SECURE: bool = False

    # Public URL for serving tilesets (used by frontend)
    TILESET_PUBLIC_URL: str = "/terrain-tilesets"

    # Orion-LD Context Broker
    ORION_URL: str = "http://orion-ld:1026"

    # Keycloak (for token validation)
    KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak-service:8080/auth")
    KEYCLOAK_REALM: str = "nekazari"

    # Processing settings
    DEFAULT_MAX_ERROR: float = 0.5  # pydelatin max error for mesh decimation
    DEFAULT_ZOOM_RANGE: str = "8-14"  # min-max zoom levels for terrain tile generation

    # Worker settings
    WORKER_QUEUE_NAME: str = "elevation-processing"
    WORKER_TIMEOUT: int = 3600  # 60 minutes max per job

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Singleton settings instance
settings = Settings()
