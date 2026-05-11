import boto3
import json
import os
import uuid
from datetime import datetime, timezone

BUFFER_BUCKET = os.environ.get("AWS_S3_BUFFER_BUCKET")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

S3_CONFIGURED = bool(BUFFER_BUCKET and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)


def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


def write_to_buffer(table: str, row: dict) -> bool:
    """Save a row as JSON to the S3 buffer. Returns True on success."""
    if not S3_CONFIGURED:
        print("⚠️  S3 buffer not configured; cannot buffer row.")
        return False
    try:
        key = f"{table}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex}.json"
        _client().put_object(
            Bucket=BUFFER_BUCKET,
            Key=key,
            Body=json.dumps(row),
            ContentType="application/json",
        )
        print(f"📦 Buffered row to s3://{BUFFER_BUCKET}/{key}")
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
    if not S3_CONFIGURED:
        return
    try:
        s3 = _client()
        paginator = s3.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=BUFFER_BUCKET, Prefix=f"{table}/")
            for obj in page.get("Contents", [])
        ]
        if not keys:
            return
        print(f"⏳ Flushing {len(keys)} buffered row(s) for '{table}'...")
        for key in sorted(keys):  # oldest first
            try:
                body = s3.get_object(Bucket=BUFFER_BUCKET, Key=key)["Body"].read()
                row = json.loads(body)
                insert_fn(row)
                s3.delete_object(Bucket=BUFFER_BUCKET, Key=key)
                print(f"  ✅ Flushed {key}")
            except Exception as e:
                print(f"  ❌ Failed to flush {key}: {e}")
    except Exception as e:
        print(f"❌ Error accessing S3 buffer: {e}")
