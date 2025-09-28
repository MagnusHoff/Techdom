# core/s3_upload.py  (KUN for proxy-filer)
import os
from pathlib import Path
import boto3


def upload_good_proxies(
    local_path: str | Path = "data/proxies/good_proxies.txt",
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
    bucket = os.environ["S3_PROXY_BUCKET"]
    prefix = os.environ.get("S3_PROXY_PREFIX", "proxy")
    region = os.environ.get("AWS_PROXY_REGION", "eu-north-1")

    local_path = Path(local_path)
    if not local_path.exists():
        print(f"⚠️ Finner ikke {local_path}")
        return None

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_PROXY_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_PROXY_SECRET_ACCESS_KEY"],
        region_name=region,
    )

    key = f"{prefix.rstrip('/')}/good_proxies.txt"

    # Last opp
    s3.upload_file(str(local_path), bucket, key)
    size = local_path.stat().st_size
    print(f"✅ Lastet opp proxies ({size} bytes) → s3://{bucket}/{key}")

    # Presigned GET-url (fin til verifisering)
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
        "size_bytes": size,
    }
