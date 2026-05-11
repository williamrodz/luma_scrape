import boto3
import json
import os
import uuid
from datetime import datetime, timezone

def _env():
    bucket = os.environ.get("AWS_S3_BUFFER_BUCKET")
    key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    return bucket, key_id, secret, bool(bucket and key_id and secret)


# Module-level names for test_s3_buffer.py compatibility (read at import time,
# but functions always re-read via _env() so import order doesn't matter).
BUFFER_BUCKET, _, _, S3_CONFIGURED = _env()


def _client():
    _, key_id, secret, _ = _env()
    return boto3.client(
        "s3",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
    )


def write_to_buffer(table: str, row: dict) -> bool:
    """Save a row as JSON to the S3 buffer. Returns True on success."""
    bucket, _, _, configured = _env()
    if not configured:
        print("⚠️  S3 buffer not configured; cannot buffer row.")
        return False
    try:
        key = f"{table}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex}.json"
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(row),
            ContentType="application/json",
        )
        print(f"📦 Buffered row to s3://{bucket}/{key}")
        return True
    except Exception as e:
        print(f"❌ Failed to write to S3 buffer: {e}")
        return False


def flush_buffer(table: str, insert_fn) -> None:
    """
    Replay all buffered rows for `table` into Supabase via insert_fn(row).
    Deletes each file from S3 only after a successful insert.
    insert_fn must raise on failure.
    """
    bucket, _, _, configured = _env()
    if not configured:
        return
    try:
        s3 = _client()
        paginator = s3.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=bucket, Prefix=f"{table}/")
            for obj in page.get("Contents", [])
        ]
        if not keys:
            return
        print(f"⏳ Flushing {len(keys)} buffered row(s) for '{table}'...")
        for key in sorted(keys):  # oldest first
            try:
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                row = json.loads(body)
                insert_fn(row)
                s3.delete_object(Bucket=bucket, Key=key)
                print(f"  ✅ Flushed {key}")
            except Exception as e:
                print(f"  ❌ Failed to flush {key}: {e}")
    except Exception as e:
        print(f"❌ Error accessing S3 buffer: {e}")
