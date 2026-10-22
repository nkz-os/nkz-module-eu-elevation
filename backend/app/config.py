"""
Configuration module for EU Elevation backend.
Loads settings from environment variables. No hardcoded defaults for secrets.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database (PostgreSQL) — NO hardcoded credentials (public repo)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Redis (for Celery task queue + elevation point cache)
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-service:6379/0")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    # Encryption key for sensitive DB columns (Fernet symmetric)
    ELEVATION_ENCRYPTION_KEY: str = os.getenv("ELEVATION_ENCRYPTION_KEY", "")

    @property
    def effective_redis_url(self) -> str:
        """Redis URL with password injected if REDIS_PASSWORD is set.
        
        If REDIS_URL already contains a password, REDIS_PASSWORD is ignored.
        Otherwise, the password is inserted into the URL.
        """
        url = self.REDIS_URL
        if self.REDIS_PASSWORD and "@" not in url.split("://", 1)[-1]:
            # No password in URL — inject REDIS_PASSWORD
            if "://" in url:
                scheme, rest = url.split("://", 1)
                url = f"{scheme}://:{self.REDIS_PASSWORD}@{rest}"
        return url

    # MinIO / S3 Storage — NO hardcoded credentials (public repo)
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "terrain-tilesets")
    MINIO_SECURE: bool = False

    # Public URL for serving tilesets (used by frontend)
    TILESET_PUBLIC_URL: str = "/terrain-tilesets"

    # Default Copernicus GLO-30 terrain tileset URL for Europe
    # Pre-ingested once by platform admin into MinIO — no per-tenant API key needed
    EU_COPERNICUS_TERRAIN_URL: str = os.getenv(
        "EU_COPERNICUS_TERRAIN_URL",
        "/api/elevation/terrain/EU/layer.json"
    )

    # Orion-LD Context Broker
    ORION_URL: str = "http://orion-ld:1026"

    # Keycloak (for token validation)
    KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak-service:8080/auth")
    KEYCLOAK_REALM: str = "nekazari"

    # Gateway HMAC (shared jwt-secret/secret)
    HMAC_SECRET: str = os.getenv("HMAC_SECRET", "")
    REQUIRE_HMAC_SIGNATURE: bool = os.getenv("REQUIRE_HMAC_SIGNATURE", "true").lower() == "true"

    def enforce_required_secrets(self) -> None:
        if self.REQUIRE_HMAC_SIGNATURE and not self.HMAC_SECRET:
            raise RuntimeError(
                "HMAC_SECRET is required when REQUIRE_HMAC_SIGNATURE=true"
            )

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
