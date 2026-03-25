"""
Upload EU Elevation module frontend to MinIO.

Usage: MINIO_ACCESS_KEY=xxx MINIO_SECRET_KEY=xxx python upload_module.py
"""
import boto3
import os
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.getenv("MINIO_ENDPOINT_URL", "http://localhost:9000"),
    aws_access_key_id=os.getenv("MINIO_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("MINIO_SECRET_KEY"),
    config=Config(signature_version="s3v4"),
    region_name="us-east-1"
)

MODULE_ID = "nkz-module-eu-elevation"
local_path = f"dist/nekazari-module.js"
s3_key = f"modules/{MODULE_ID}/nekazari-module.js"

if not os.path.exists(local_path):
    print(f"Error: {local_path} not found. Run 'pnpm run build:module' first.")
    exit(1)

with open(local_path, "rb") as f:
    s3.put_object(
        Bucket="nekazari-frontend",
        Key=s3_key,
        Body=f.read(),
        ContentType="application/javascript"
    )
print(f"Uploaded {local_path} → s3://nekazari-frontend/{s3_key}")
