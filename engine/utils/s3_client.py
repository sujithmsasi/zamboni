"""
Zamboni — S3 Client
Prefix operations, batch delete, URI parsing.
"""
from __future__ import annotations

import boto3

from config.settings import AWS_REGION
from engine.utils.logger import get_logger

log = get_logger(__name__)

_client: boto3.client | None = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=AWS_REGION)
    return _client


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse 's3://bucket/prefix' → (bucket, prefix)."""
    assert uri.startswith("s3://"), f"Not an S3 URI: {uri}"
    parts = uri[5:].split("/", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def prefix_exists(bucket: str, prefix: str) -> bool:
    """Return True if at least one object exists under prefix."""
    resp = _get_client().list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return resp.get("KeyCount", 0) > 0


def list_keys(bucket: str, prefix: str) -> list[str]:
    """Return all object keys under prefix (paginated)."""
    keys = []
    paginator = _get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def get_prefix_size_bytes(bucket: str, prefix: str) -> int:
    """Return total size in bytes of all objects under prefix."""
    total = 0
    paginator = _get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            total += obj.get("Size", 0)
    return total


def delete_prefix(bucket: str, prefix: str, dry_run: bool = False) -> int:
    """
    Delete all objects under prefix.
    Returns count of objects deleted.
    """
    keys = list_keys(bucket, prefix)
    if not keys:
        log.info("s3.delete_prefix.empty", bucket=bucket, prefix=prefix)
        return 0

    if dry_run:
        log.info("s3.delete_prefix.dry_run", bucket=bucket, prefix=prefix, count=len(keys))
        return len(keys)

    client  = _get_client()
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i:i + 1000]]
        resp  = client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        deleted += len(resp.get("Deleted", []))
        if resp.get("Errors"):
            log.error("s3.delete_prefix.errors", bucket=bucket, errors=resp["Errors"])

    log.info("s3.delete_prefix.done", bucket=bucket, prefix=prefix, deleted=deleted)
    return deleted


def head_object(bucket: str, key: str) -> dict | None:
    """Return object metadata or None if not found."""
    try:
        return _get_client().head_object(Bucket=bucket, Key=key)
    except _get_client().exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise


def get_object_bytes(bucket: str, key: str) -> bytes:
    """Return the full body of an S3 object as bytes."""
    return _get_client().get_object(Bucket=bucket, Key=key)["Body"].read()


def upload_file(bucket: str, key: str, local_path: str) -> None:
    """Upload a local file to S3. Used by scripts/control_plane_backup.py
    for VACUUM INTO snapshots -- keeps every S3 call routed through this
    module rather than constructing a raw boto3 client inline elsewhere."""
    _get_client().upload_file(local_path, bucket, key)


def delete_keys(bucket: str, keys: list[str]) -> int:
    """Delete an explicit list of object keys (not a whole prefix -- see
    delete_prefix() for that). Returns count deleted."""
    if not keys:
        return 0
    client  = _get_client()
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i:i + 1000]]
        resp  = client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        deleted += len(resp.get("Deleted", []))
        if resp.get("Errors"):
            log.error("s3.delete_keys.errors", bucket=bucket, errors=resp["Errors"])
    return deleted
