"""
Shared S3/MinIO client — single boto3 initialization point for the entire module.

Consolidates the three previously separate S3 client inits:
  - elevation_tasks.py (worker) → _get_s3_client()
  - elevation.py (API)         → _get_terrain_minio()
  - storage.py (service)       → StorageService.client

All share the same MinIO credentials from env vars:
  MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET, MINIO_SECURE
"""

import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ── Configuration from env ──────────────────────────────────────────

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "terrain-tilesets")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# ── Singleton client ────────────────────────────────────────────────

_s3_client = None


def get_s3_client():
    """Create or return the cached boto3 S3 client for MinIO-compatible storage.

    Thread-safe via module-level singleton.  Raises RuntimeError if credentials
    are missing and the client hasn't been initialised yet.

    Returns:
        boto3 S3 client instance
    """
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set")

    protocol = "https" if MINIO_SECURE else "http"
    _s3_client = boto3.client(
        "s3",
        endpoint_url=f"{protocol}://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=boto3.session.Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    logger.info("S3 client initialised (endpoint=%s, secure=%s)", MINIO_ENDPOINT, MINIO_SECURE)
    return _s3_client


# ── Bucket helpers ──────────────────────────────────────────────────

def ensure_bucket(client, bucket: str = None):
    """Ensure the target bucket exists (idempotent).

    Args:
        client: boto3 S3 client
        bucket: bucket name (defaults to MINIO_BUCKET)
    """
    target = bucket or MINIO_BUCKET
    try:
        client.head_bucket(Bucket=target)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=target)
            logger.info("Created S3 bucket: %s", target)
        else:
            raise


def ensure_public_bucket(client, bucket: str = None):
    """Ensure bucket exists AND has a public-read policy (for frontend serving).

    Used by StorageService for the tileset bucket.
    """
    target = bucket or MINIO_BUCKET
    ensure_bucket(client, target)

    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{target}/*"],
        }],
    }
    import json
    try:
        client.put_bucket_policy(Bucket=target, Policy=json.dumps(policy))
        logger.info("Set public-read policy on bucket %s", target)
    except ClientError as e:
        logger.warning("Could not set bucket policy on %s: %s", target, e)


# ── Object helpers ──────────────────────────────────────────────────

def upload_bytes(
    client,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
):
    """Upload bytes to S3/MinIO."""
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def file_exists(client, bucket: str, key: str) -> bool:
    """Check if an object exists in the bucket."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def get_object_bytes(client, bucket: str, key: str) -> Optional[bytes]:
    """Fetch object body as bytes. Returns None if not found."""
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    except ClientError:
        return None


# ── Prefix listing ──────────────────────────────────────────────────

def list_prefixes(
    client,
    bucket: str,
    prefix: str,
    delimiter: str = "/",
    strip_prefix: str = "",
) -> list[str]:
    """List all common prefixes under a given prefix.

    Used by _list_available_tilesets and _find_sub_tilesets in the API.

    Args:
        strip_prefix: If set, remove this string from the start of each result.
                      E.g., strip_prefix="terrain/" turns "terrain/ES" into "ES".

    Returns:
        List of prefix strings (with trailing delimiter stripped)
    """
    prefixes: list[str] = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter=delimiter):
            for cp in page.get("CommonPrefixes", []):
                p = cp["Prefix"].rstrip(delimiter)
                if strip_prefix and p.startswith(strip_prefix):
                    p = p[len(strip_prefix):]
                prefixes.append(p)
    except Exception:
        pass
    return sorted(prefixes)
