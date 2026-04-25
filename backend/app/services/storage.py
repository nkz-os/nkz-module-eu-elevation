"""
MinIO/S3 Storage Service for EU Elevation Module.

Handles uploading and managing terrain tiles and related assets.
"""

import logging
from typing import Optional, BinaryIO
from pathlib import Path
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """
    Service for managing terrain assets in MinIO/S3.
    
    Handles:
    - Uploading quantized mesh tile directories
    - Managing layer.json and .terrain files
    - Generating public URLs for frontend
    """
    
    def __init__(self):
        self._client = None
        self.bucket = settings.MINIO_BUCKET
    
    @property
    def client(self):
        """Lazy-init boto3 client to avoid failures during import."""
        if self._client is None:
            self._client = boto3.client(
                's3',
                endpoint_url=f"{'https' if settings.MINIO_SECURE else 'http'}://{settings.MINIO_ENDPOINT}",
                aws_access_key_id=settings.MINIO_ACCESS_KEY,
                aws_secret_access_key=settings.MINIO_SECRET_KEY,
                config=Config(signature_version='s3v4'),
                region_name='us-east-1',
            )
            self._ensure_bucket()
        return self._client
    
    def _ensure_bucket(self):
        """Ensure the bucket exists."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            logger.debug(f"Bucket {self.bucket} exists")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchBucket'):
                logger.info(f"Creating bucket {self.bucket}")
                self.client.create_bucket(Bucket=self.bucket)
                # Set public read policy for tilesets
                self._set_public_read_policy()
            else:
                raise
    
    def _set_public_read_policy(self):
        """Set bucket policy to allow public read access."""
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self.bucket}/*"]
                }
            ]
        }
        import json
        self.client.put_bucket_policy(
            Bucket=self.bucket,
            Policy=json.dumps(policy)
        )
        logger.info(f"Set public read policy on bucket {self.bucket}")
    
    def upload_directory(
        self,
        local_dir: str,
        prefix: str,
        content_type_map: Optional[dict] = None
    ) -> str:
        """
        Upload a directory (e.g., 3D Tiles hierarchy) to storage.
        
        Args:
            local_dir: Local directory path containing files
            prefix: S3 prefix (folder path) in the bucket
            content_type_map: Optional mapping of extensions to content types
        
        Returns:
            Public URL to the tileset.json
        """
        if content_type_map is None:
            content_type_map = {
                '.json': 'application/json',
                '.pnts': 'application/octet-stream',
                '.b3dm': 'application/octet-stream',
                '.i3dm': 'application/octet-stream',
                '.cmpt': 'application/octet-stream',
                '.glb': 'model/gltf-binary',
                '.gltf': 'model/gltf+json',
            }
        
        local_path = Path(local_dir)
        if not local_path.exists():
            raise FileNotFoundError(f"Directory not found: {local_dir}")
        
        uploaded_files = []
        
        for file_path in local_path.rglob('*'):
            if file_path.is_file():
                # Calculate relative path for S3 key
                relative = file_path.relative_to(local_path)
                s3_key = f"{prefix}/{relative}".replace('\\', '/')
                
                # Determine content type
                ext = file_path.suffix.lower()
                content_type = content_type_map.get(ext, 'application/octet-stream')
                
                # Upload file
                logger.debug(f"Uploading {file_path} to {s3_key}")
                self.client.upload_file(
                    str(file_path),
                    self.bucket,
                    s3_key,
                    ExtraArgs={'ContentType': content_type}
                )
                uploaded_files.append(s3_key)
        
        logger.info(f"Uploaded {len(uploaded_files)} files to {prefix}")
        
        # Return URL to tileset.json
        tileset_url = f"{settings.TILESET_PUBLIC_URL}/{prefix}/tileset.json"
        return tileset_url
    
    def delete_prefix(self, prefix: str) -> int:
        """
        Delete all objects under a prefix (folder).
        
        Args:
            prefix: S3 prefix to delete
        
        Returns:
            Number of objects deleted
        """
        paginator = self.client.get_paginator('list_objects_v2')
        
        deleted_count = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            if 'Contents' not in page:
                continue
            
            objects = [{'Key': obj['Key']} for obj in page['Contents']]
            if objects:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={'Objects': objects}
                )
                deleted_count += len(objects)
        
        logger.info(f"Deleted {deleted_count} objects from {prefix}")
        return deleted_count
    
    def get_public_url(self, key: str) -> str:
        """Get the public URL for an object."""
        return f"{settings.TILESET_PUBLIC_URL}/{key}"
    
    def file_exists(self, key: str) -> bool:
        """Check if a file exists in storage."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False
    
    def ensure_bucket(self, bucket: str):
        """Ensure a specific bucket exists (for source tiles cache)."""
        try:
            self.client.head_bucket(Bucket=bucket)
            logger.debug(f"Bucket {bucket} exists")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchBucket'):
                logger.info(f"Creating bucket {bucket}")
                self.client.create_bucket(Bucket=bucket)
            else:
                raise
    
    def download_file(self, bucket: str, key: str, local_path: str):
        """
        Download a file from storage to local path.
        
        Args:
            bucket: Bucket name
            key: Object key in bucket
            local_path: Local file path to save to
        """
        logger.debug(f"Downloading {bucket}/{key} to {local_path}")
        self.client.download_file(bucket, key, local_path)
        logger.debug(f"Downloaded to {local_path}")
    
    def upload_file(
        self,
        bucket: str = None,
        key: str = None,
        file_path: str = None,
        file_obj: 'BinaryIO' = None,
        content_type: str = 'application/octet-stream'
    ) -> str:
        """
        Upload a file to storage.
        
        Args:
            bucket: Bucket name (defaults to self.bucket)
            key: S3 key (path in bucket)
            file_path: Local file path to upload (use this OR file_obj)
            file_obj: File-like object to upload (use this OR file_path)
            content_type: MIME type of the file
        
        Returns:
            Public URL to the file
        """
        target_bucket = bucket or self.bucket
        
        if file_path:
            self.client.upload_file(
                file_path,
                target_bucket,
                key,
                ExtraArgs={'ContentType': content_type}
            )
        elif file_obj:
            self.client.upload_fileobj(
                file_obj,
                target_bucket,
                key,
                ExtraArgs={'ContentType': content_type}
            )
        else:
            raise ValueError("Either file_path or file_obj must be provided")
        
        return f"{settings.TILESET_PUBLIC_URL}/{key}"
    
    def file_exists_in_bucket(self, bucket: str, key: str) -> bool:
        """Check if a file exists in a specific bucket."""
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError:
            return False


# Singleton instance
storage_service = StorageService()

