from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from techdom.infrastructure.db import get_session
from techdom.services import subscriptions as subscription_service


router = APIRouter(prefix="/stripe", tags=["stripe"])
logger = logging.getLogger(__name__)


@router.post("/webhook", status_code=status.HTTP_200_OK, include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    payload = await request.body()
    signature = request.headers.get("stripe-signature")

    try:
        event = subscription_service.construct_event(payload, signature)
    except subscription_service.StripeConfigurationError as exc:
        logger.exception("Stripe-webhook mangler konfigurasjon")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except subscription_service.StripeSignatureError as exc:
        logger.warning("Stripe-webhook signaturfeil: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        await subscription_service.handle_event(session, event)
    except subscription_service.StripeOperationError as exc:
        logger.exception("Stripe-webhook behandling feilet")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return {"status": "ok"}
