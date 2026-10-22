import os
from celery import Celery
from loguru import logger

# Initialize Celery app
# Defaults to Redis running on localhost for local dev
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "elevation_worker",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.tasks.elevation_tasks"]
)

# Optional configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1, # Important for long-running ETL tasks (GDAL)
    task_track_started=True
)

logger.info(f"Initialized Elevation Celery Worker pointing to {CELERY_BROKER_URL}")

if __name__ == "__main__":
    celery_app.start()
