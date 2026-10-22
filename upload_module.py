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
files_to_upload = [
    ("dist/nkz-module.js", f"modules/{MODULE_ID}/nkz-module.js", "application/javascript"),
    ("dist/nkz-module.js.map", f"modules/{MODULE_ID}/nkz-module.js.map", "application/json"),
]

for local_path, s3_key, content_type in files_to_upload:
    if not os.path.exists(local_path):
        print(f"Warning: {local_path} not found. Skipping.")
        continue

    with open(local_path, "rb") as f:
        s3.put_object(
            Bucket="nekazari-frontend",
            Key=s3_key,
            Body=f.read(),
            ContentType=content_type
        )
    print(f"Uploaded {local_path} → s3://nekazari-frontend/{s3_key}")
