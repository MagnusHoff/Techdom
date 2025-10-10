"""Helpers for persisting globale tellere i DynamoDB."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

try:
    import boto3  # type: ignore
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    boto3 = None  # type: ignore

    class BotoCoreError(Exception):  # type: ignore
        pass

    class ClientError(Exception):  # type: ignore
        pass


_TABLE_NAME = os.getenv("DYNAMODB_TABLE", "").strip()


def _get_table():
    if not _TABLE_NAME or boto3 is None:
        raise RuntimeError("DYNAMODB_TABLE er ikke satt")
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(_TABLE_NAME)


def fetch_total_count(default: int = 0) -> int:
    """Hent totalt antall analyser. Faller tilbake til default ved feil."""
    if not _TABLE_NAME or boto3 is None:
        return default
    try:
        table = _get_table()
        resp = table.get_item(Key={"pk": "total_analyses"})
    except (ClientError, BotoCoreError, RuntimeError):
        return default

    item = resp.get("Item") if isinstance(resp, dict) else None
    if not item:
        return default

    value = item.get("total")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, Decimal):
        return int(value)
    try:
        return int(value)
    except Exception:
        return default


def increment_total_count() -> Optional[int]:
    """Øk telleren i DynamoDB. Returnerer ny verdi eller None ved feil."""
    if not _TABLE_NAME or boto3 is None:
        return None
    try:
        table = _get_table()
        resp = table.update_item(
            Key={"pk": "total_analyses"},
            UpdateExpression="SET #t = if_not_exists(#t, :zero) + :inc",
            ExpressionAttributeNames={"#t": "total"},
            ExpressionAttributeValues={
                ":zero": Decimal(0),
                ":inc": Decimal(1),
            },
            ReturnValues="UPDATED_NEW",
        )
        attrs = resp.get("Attributes") if isinstance(resp, dict) else None
        value = attrs.get("total") if isinstance(attrs, dict) else None
        if isinstance(value, Decimal):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        return int(value) if value is not None else None
    except (ClientError, BotoCoreError, RuntimeError, ValueError, TypeError):
        return None
