# core/s3_upload.py  (KUN for proxy-filer)
from __future__ import annotations

import os
from pathlib import Path
try:
    import boto3  # type: ignore
    from botocore.exceptions import BotoCoreError as _BotoCoreError, ClientError as _ClientError  # type: ignore
except Exception:  # pragma: no cover
    boto3 = None  # type: ignore

    class _BotoCoreError(Exception):  # type: ignore
        pass

    class _ClientError(Exception):  # type: ignore
        pass


def _require_env(name: str, *, context: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f"Miljøvariabelen {name} er ikke satt – kreves for {context}.")
    return value


def _build_key(prefix: str, filename: str) -> str:
    prefix = prefix.strip().strip('/')
    return f"{prefix}/{filename}" if prefix else filename


def upload_good_proxies(
    local_path: str | Path = "data/raw/proxy/good_proxies.txt",
    url_expire: int = 3600,
):
    """
    Laster opp good_proxies.txt til proxy-bucketen og returnerer metadata + presigned URL.

    Args:
        local_path: Lokal sti til good_proxies.txt
        url_expire: Gyldighet (sekunder) for presigned GET-url (default 1 time)

    Returns:
        dict | None:
            {
              "s3_uri": "s3://<bucket>/<key>",
              "url": "<presigned-get-url>",
              "bucket": "<bucket>",
              "key": "<key>",
              "size_bytes": <int>
            }
        None hvis fil mangler.
    """
    if boto3 is None:  # pragma: no cover - forventes tilgjengelig i prod
        raise RuntimeError("boto3 er ikke installert – kan ikke laste opp proxier til S3.")

    local_path = Path(local_path)
    if not local_path.exists():
        print(f"⚠️ Finner ikke {local_path}")
        return None

    bucket = _require_env("S3_PROXY_BUCKET", context="proxy-opplasting")
    prefix = os.getenv("S3_PROXY_PREFIX", "proxy") or "proxy"
    region = os.getenv("AWS_PROXY_REGION", "eu-north-1") or "eu-north-1"
    access_key = _require_env("AWS_PROXY_ACCESS_KEY_ID", context="proxy-opplasting")
    secret_key = _require_env("AWS_PROXY_SECRET_ACCESS_KEY", context="proxy-opplasting")

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:  # pragma: no cover
        raise RuntimeError(f"Kunne ikke opprette S3-klient for proxy-opplasting: {exc}") from exc

    key = _build_key(prefix, "good_proxies.txt")

    try:
        s3.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={"ContentType": "text/plain; charset=utf-8"},
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:
        raise RuntimeError(f"Kunne ikke laste opp proxier til s3://{bucket}/{key}: {exc}") from exc

    size = local_path.stat().st_size
    print(f"✅ Lastet opp proxies ({size} bytes) → s3://{bucket}/{key}")

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=url_expire,
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:
        raise RuntimeError(f"Kunne ikke generere presigned URL for s3://{bucket}/{key}: {exc}") from exc

    return {
        "s3_uri": f"s3://{bucket}/{key}",
        "url": url,
        "bucket": bucket,
        "key": key,
        "size_bytes": size,
    }
