"""S3-integrasjon for prospekter og failcases."""
from __future__ import annotations

import os
from typing import Optional

try:
    import boto3  # type: ignore
    from botocore.exceptions import (  # type: ignore
        BotoCoreError as _BotoCoreError,
        ClientError as _ClientError,
    )
except Exception:
    boto3 = None  # type: ignore
    _BotoCoreError = _ClientError = Exception  # type: ignore

from .prospect_paths import PROSPEKT_DIR

PROSPEKT_BUCKET = os.getenv("PROSPEKT_BUCKET", "").strip()
PROSPEKT_PREFIX = (
    (os.getenv("PROSPEKT_PREFIX", "prospekt") or "prospekt").strip().strip("/")
)
AWS_PROSPEKT_REGION = os.getenv("AWS_PROSPEKT_REGION", "eu-north-1").strip()
AWS_PROSPEKT_ACCESS_KEY_ID = os.getenv("AWS_PROSPEKT_ACCESS_KEY_ID", "").strip()
AWS_PROSPEKT_SECRET_ACCESS_KEY = os.getenv("AWS_PROSPEKT_SECRET_ACCESS_KEY", "").strip()

FAILCASE_BUCKET = os.getenv("FAILCASE_BUCKET", "").strip()
FAILCASE_PREFIX = (
    (os.getenv("FAILCASE_PREFIX", "failcases") or "failcases").strip().strip("/")
)


def prospekt_s3_enabled() -> bool:
    return bool(
        boto3
        and PROSPEKT_BUCKET
        and PROSPEKT_PREFIX
        and AWS_PROSPEKT_ACCESS_KEY_ID
        and AWS_PROSPEKT_SECRET_ACCESS_KEY
    )


def failcase_s3_enabled() -> bool:
    return bool(
        boto3
        and FAILCASE_BUCKET
        and AWS_PROSPEKT_ACCESS_KEY_ID
        and AWS_PROSPEKT_SECRET_ACCESS_KEY
    )


def _client():
    assert boto3 is not None, "boto3 mangler"
    return boto3.client(
        "s3",
        region_name=AWS_PROSPEKT_REGION,
        aws_access_key_id=AWS_PROSPEKT_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_PROSPEKT_SECRET_ACCESS_KEY,
    )


def prospekt_key(finnkode: str) -> str:
    return f"{PROSPEKT_PREFIX}/{finnkode}.pdf"


def failcase_key(stem: str, suffix: str) -> str:
    if FAILCASE_PREFIX:
        return f"{FAILCASE_PREFIX}/{stem}{suffix}"
    return f"{stem}{suffix}"


def s3_head(key: str) -> Optional[dict]:
    if not prospekt_s3_enabled():
        return None
    try:
        c = _client()
        return c.head_object(Bucket=PROSPEKT_BUCKET, Key=key)
    except Exception:
        return None


def s3_get_bytes(key: str) -> Optional[bytes]:
    if not prospekt_s3_enabled():
        return None
    try:
        c = _client()
        obj = c.get_object(Bucket=PROSPEKT_BUCKET, Key=key)
        return obj["Body"].read()
    except Exception:
        return None


def presigned_get(key: str, expire: int = 3600) -> Optional[str]:
    if not prospekt_s3_enabled():
        return None
    try:
        c = _client()
        return c.generate_presigned_url(
            "get_object",
            Params={"Bucket": PROSPEKT_BUCKET, "Key": key},
            ExpiresIn=expire,
        )
    except Exception:
        return None


__all__ = [
    "_BotoCoreError",
    "_ClientError",
    "PROSPEKT_BUCKET",
    "PROSPEKT_PREFIX",
    "PROSPEKT_DIR",
    "FAILCASE_BUCKET",
    "FAILCASE_PREFIX",
    "prospekt_s3_enabled",
    "failcase_s3_enabled",
    "prospekt_key",
    "failcase_key",
    "s3_head",
    "s3_get_bytes",
    "presigned_get",
]
