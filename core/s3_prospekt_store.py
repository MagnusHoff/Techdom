# core/s3_prospekt_store.py
import os
from pathlib import Path
import boto3


def upload_prospekt(local_path: str | Path, finnkode: str, url_expire: int = 3600):
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
    bucket = os.environ["PROSPEKT_BUCKET"]
    prefix = os.environ.get("PROSPEKT_PREFIX", "prospekt")
    region = os.environ.get("AWS_PROSPEKT_REGION", "eu-north-1")

    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"⚠️ Finner ikke {local_path}")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_PROSPEKT_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_PROSPEKT_SECRET_ACCESS_KEY"],
        region_name=region,
    )

    key = f"{prefix.rstrip('/')}/{finnkode}.pdf"

    # Last opp fil
    s3.upload_file(str(local_path), bucket, key)
    print(f"✅ Lastet opp prospekt: s3://{bucket}/{key}")

    # Lag presigned URL (trygt å gi til UI-brukere)
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=url_expire,
    )

    return {
        "s3_uri": f"s3://{bucket}/{key}",
        "url": url,
        "bucket": bucket,
        "key": key,
    }
