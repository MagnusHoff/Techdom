# core/s3_prospekt_store.py
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


def _build_key(prefix: str, finnkode: str, sha256: str | None = None) -> str:
    prefix = prefix.strip().strip('/')
    if sha256:
        stem = sha256.strip().lower()
        filename = f"{stem}.pdf"
        if prefix:
            return f"{prefix}/{finnkode}/{filename}"
        return f"{finnkode}/{filename}"
    filename = f"{finnkode}.pdf"
    return f"{prefix}/{filename}" if prefix else filename


def upload_prospekt(
    local_path: str | Path,
    finnkode: str,
    *,
    sha256: str | None = None,
    url_expire: int = 3600,
):
    """
    Laster opp et prospekt-PDF til S3 og returnerer en presigned URL.

    Args:
        local_path (str | Path): Sti til lokal PDF-fil som skal lastes opp.
        finnkode (str): FINN-kode (brukes som filnavn i S3).
        url_expire (int): Antall sekunder presigned URL skal være gyldig (default 1 time).

    Returns:
        dict: {
            "s3_uri": "s3://bucket/key",
            "url": "https://... (presigned)",
            "bucket": bucket,
            "key": key
        }
    """
    if boto3 is None:  # pragma: no cover - forventes tilgjengelig i prod
        raise RuntimeError("boto3 er ikke installert – kan ikke laste opp prospekter til S3.")

    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"⚠️ Finner ikke {local_path}")

    bucket = _require_env("PROSPEKT_BUCKET", context="prospekt-opplasting")
    prefix = os.getenv("PROSPEKT_PREFIX", "prospekt") or "prospekt"
    region = os.getenv("AWS_PROSPEKT_REGION", "eu-north-1") or "eu-north-1"
    access_key = _require_env("AWS_PROSPEKT_ACCESS_KEY_ID", context="prospekt-opplasting")
    secret_key = _require_env("AWS_PROSPEKT_SECRET_ACCESS_KEY", context="prospekt-opplasting")
    cdn_base = (
        os.getenv("SALGSOPPGAVE_CDN_BASE_URL")
        or os.getenv("PROSPEKT_CDN_BASE_URL")
        or ""
    ).strip()

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:  # pragma: no cover
        raise RuntimeError(f"Kunne ikke opprette S3-klient for prospekt-opplasting: {exc}") from exc

    key = _build_key(prefix, finnkode, sha256)

    try:
        s3.upload_file(
            str(local_path),
            bucket,
            key,
            ExtraArgs={
                "ContentType": "application/pdf",
                "ACL": "public-read",
            },
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:
        raise RuntimeError(f"Kunne ikke laste opp prospekt til s3://{bucket}/{key}: {exc}") from exc

    print(f"✅ Lastet opp prospekt: s3://{bucket}/{key}")

    def _build_stable_url() -> str:
        if cdn_base:
            return f"{cdn_base.rstrip('/')}/{key}"
        return f"https://{bucket}.s3.amazonaws.com/{key}"

    try:
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=url_expire,
        )
    except (_BotoCoreError, _ClientError, Exception) as exc:
        raise RuntimeError(f"Kunne ikke generere presigned URL for s3://{bucket}/{key}: {exc}") from exc

    return {
        "s3_uri": f"s3://{bucket}/{key}",
        "url": presigned_url,
        "cdn_url": _build_stable_url(),
        "bucket": bucket,
        "key": key,
    }
