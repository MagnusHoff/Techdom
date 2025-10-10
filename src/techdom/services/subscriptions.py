from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal, TYPE_CHECKING, Protocol, cast

from types import ModuleType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_STRIPE_IMPORT_ERROR: Exception | None = None

stripe: ModuleType | None

try:
    import stripe as _stripe_module  # type: ignore[import-not-found]
    from stripe.error import SignatureVerificationError, StripeError  # type: ignore[import-not-found]
    stripe = cast(ModuleType, _stripe_module)
except ImportError as exc:  # pragma: no cover - optional dependency
    stripe = None
    SignatureVerificationError = Exception  # type: ignore[assignment]
    StripeError = Exception  # type: ignore[assignment]
    _STRIPE_IMPORT_ERROR = exc

class StripeLike(Protocol):
    api_key: str
    Customer: Any
    checkout: Any
    billing_portal: Any
    Subscription: Any
    Webhook: Any


if TYPE_CHECKING:  # pragma: no cover - typing helper
    import stripe as _stripe_types

from techdom.domain.auth.models import User, UserRole
from techdom.services.auth import UserNotFoundError


logger = logging.getLogger(__name__)

BillingInterval = Literal["monthly", "yearly"]


class StripeConfigurationError(RuntimeError):
    """Raised when Stripe is not correctly configured."""


class StripeSignatureError(RuntimeError):
    """Raised when webhook signature validation fails."""


class StripeOperationError(RuntimeError):
    """Raised when Stripe operations fail."""


@dataclass(frozen=True)
class StripeSettings:
    api_key: str
    monthly_price_id: str
    yearly_price_id: str
    success_url: str
    cancel_url: str
    portal_return_url: str
    portal_configuration_id: str | None
    webhook_secret: str | None

    def price_for(self, interval: BillingInterval) -> str:
        return self.monthly_price_id if interval == "monthly" else self.yearly_price_id


_DEFAULT_FRONTEND_BASE = "http://localhost:3000"


def _clean_env_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _frontend_base_url() -> str:
    value = _clean_env_value(os.getenv("FRONTEND_BASE_URL"))
    return value.rstrip("/") if value else _DEFAULT_FRONTEND_BASE


@lru_cache(maxsize=1)
def get_stripe_settings() -> StripeSettings:
    api_key = _clean_env_value(os.getenv("STRIPE_API_KEY"))
    if not api_key:
        raise StripeConfigurationError("STRIPE_API_KEY mangler i miljøvariablene")

    monthly_price_id = _clean_env_value(os.getenv("STRIPE_PRICE_ID_MONTHLY"))
    yearly_price_id = _clean_env_value(os.getenv("STRIPE_PRICE_ID_YEARLY"))
    if not monthly_price_id or not yearly_price_id:
        raise StripeConfigurationError(
            "STRIPE_PRICE_ID_MONTHLY og STRIPE_PRICE_ID_YEARLY må være satt"
        )

    base_url = _frontend_base_url().rstrip("/")
    success_url = _clean_env_value(os.getenv("STRIPE_SUCCESS_URL")) or (
        f"{base_url}/profile?section=subscription&checkout=success"
    )
    cancel_url = _clean_env_value(os.getenv("STRIPE_CANCEL_URL")) or (
        f"{base_url}/profile?section=subscription&checkout=cancelled"
    )
    portal_return_url = _clean_env_value(os.getenv("STRIPE_PORTAL_RETURN_URL")) or (
        f"{base_url}/profile?section=subscription"
    )
    portal_configuration_id = _clean_env_value(os.getenv("STRIPE_PORTAL_CONFIGURATION_ID"))
    webhook_secret = _clean_env_value(os.getenv("STRIPE_WEBHOOK_SECRET"))

    return StripeSettings(
        api_key=api_key,
        monthly_price_id=monthly_price_id,
        yearly_price_id=yearly_price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        portal_return_url=portal_return_url,
        portal_configuration_id=portal_configuration_id,
        webhook_secret=webhook_secret,
    )


@lru_cache(maxsize=1)
def _ensure_stripe_module(settings: StripeSettings) -> StripeLike:
    if stripe is None:  # pragma: no cover - defensive guard when dependency missing
        message = "stripe-biblioteket er ikke installert. Legg til 'stripe' i requirements."
        if _STRIPE_IMPORT_ERROR:
            message = f"{message} ({_STRIPE_IMPORT_ERROR})"
        raise StripeConfigurationError(message)
    stripe_module = cast(StripeLike, stripe)
    stripe_module.api_key = settings.api_key
    return stripe_module


async def _ensure_customer(
    session: AsyncSession,
    *,
    user: User,
    stripe_module: StripeLike,
) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id

    metadata: dict[str, str] = {"user_id": str(user.id)}
    customer = await asyncio.to_thread(
        stripe_module.Customer.create,
        email=user.email,
        name=user.username or None,
        metadata=metadata,
    )
    user.stripe_customer_id = customer.id
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return customer.id


async def create_checkout_session(
    session: AsyncSession,
    *,
    user_id: int,
    billing_interval: BillingInterval,
) -> str:
    settings = get_stripe_settings()
    stripe_module = _ensure_stripe_module(settings)

    user = await session.get(User, user_id)
    if not user:
        raise UserNotFoundError(user_id)

    customer_id = await _ensure_customer(session, user=user, stripe_module=stripe_module)

    price_id = settings.price_for(billing_interval)
    metadata = {"user_id": str(user.id), "billing_interval": billing_interval}

    try:
        checkout = await asyncio.to_thread(
            stripe_module.checkout.Session.create,
            customer=customer_id,
            mode="subscription",
            billing_address_collection="auto",
            automatic_tax={"enabled": False},
            success_url=settings.success_url,
            cancel_url=settings.cancel_url,
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={"metadata": metadata},
            metadata=metadata,
        )
    except StripeError as exc:  # pragma: no cover - integration error path
        logger.exception("Stripe checkout-session mislyktes for bruker %s", user.email)
        raise StripeOperationError("Kunne ikke starte Stripe Checkout akkurat nå.") from exc

    url = getattr(checkout, "url", None)
    if not url:
        raise StripeOperationError("Stripe returnerte ikke noen Checkout URL.")

    return url


async def create_billing_portal_session(
    session: AsyncSession,
    *,
    user_id: int,
) -> str:
    settings = get_stripe_settings()
    stripe_module = _ensure_stripe_module(settings)

    user = await session.get(User, user_id)
    if not user:
        raise UserNotFoundError(user_id)

    if not user.stripe_customer_id:
        raise StripeOperationError("Ingen Stripe-kunde er knyttet til brukeren.")

    try:
        portal_payload: dict[str, Any] = {
            "customer": user.stripe_customer_id,
            "return_url": settings.portal_return_url,
        }
        if settings.portal_configuration_id:
            portal_payload["configuration"] = settings.portal_configuration_id
        portal = await asyncio.to_thread(
            stripe_module.billing_portal.Session.create,
            **portal_payload,
        )
    except StripeError as exc:  # pragma: no cover - integration error path
        logger.exception(
            "Stripe Billing Portal-session mislyktes for bruker %s", user.email
        )
        raise StripeOperationError(
            "Kunne ikke åpne Stripe-portalen akkurat nå. Prøv igjen senere."
        ) from exc

    url = getattr(portal, "url", None)
    if not url:
        raise StripeOperationError("Stripe returnerte ikke noen portal-URL.")
    return url


def construct_event(payload: bytes, signature: str | None) -> Any:
    settings = get_stripe_settings()
    if not settings.webhook_secret:
        raise StripeConfigurationError("STRIPE_WEBHOOK_SECRET er ikke konfigurert")
    stripe_module = _ensure_stripe_module(settings)
    if not signature:
        raise StripeSignatureError("Mangler Stripe-signatur i header")
    try:
        return stripe_module.Webhook.construct_event(payload, signature, settings.webhook_secret)
    except SignatureVerificationError as exc:
        raise StripeSignatureError("Verifisering av webhook-signatur mislyktes") from exc


def _timestamp_to_datetime(timestamp: int | None) -> datetime | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)


def _should_have_plus_role(status: str | None) -> bool:
    if not status:
        return False
    return status in {"trialing", "active", "past_due"}


async def _find_user_for_subscription(
    session: AsyncSession,
    *,
    subscription_id: str | None = None,
    customer_id: str | None = None,
    user_id: str | None = None,
) -> User | None:
    if user_id:
        try:
            numeric_id = int(user_id)
        except (TypeError, ValueError):
            numeric_id = None
        if numeric_id:
            user = await session.get(User, numeric_id)
            if user:
                return user

    if subscription_id:
        result = await session.execute(
            select(User).where(User.stripe_subscription_id == subscription_id)
        )
        user = result.scalar_one_or_none()
        if user:
            return user

    if customer_id:
        result = await session.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalar_one_or_none()
        if user:
            return user

    return None


async def _apply_subscription_update(
    session: AsyncSession,
    *,
    subscription: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not subscription:
        return

    subscription_id = getattr(subscription, "id", None) or subscription.get("id")
    customer_id = getattr(subscription, "customer", None) or subscription.get("customer")
    metadata = metadata or getattr(subscription, "metadata", None) or {}
    user_id_value = metadata.get("user_id") if isinstance(metadata, dict) else None

    user = await _find_user_for_subscription(
        session,
        subscription_id=subscription_id,
        customer_id=customer_id,
        user_id=user_id_value,
    )
    if not user:
        logger.warning(
            "Fant ingen bruker for Stripe-abonnement %s (kunde %s)", subscription_id, customer_id
        )
        return

    first_item = None
    items = getattr(subscription, "items", None) or {}
    if isinstance(items, dict):
        data = items.get("data")
        if isinstance(data, list) and data:
            first_item = data[0]
    elif isinstance(items, list) and items:
        first_item = items[0]

    price_id = None
    interval = None
    if first_item:
        price = getattr(first_item, "price", None) or first_item.get("price")
        if price:
            price_id = getattr(price, "id", None) or price.get("id")
            recurring = getattr(price, "recurring", None) or price.get("recurring")
            if isinstance(recurring, dict):
                interval = recurring.get("interval")
            else:
                interval = getattr(recurring, "interval", None)

    status = getattr(subscription, "status", None) or subscription.get("status")
    current_period_end = getattr(subscription, "current_period_end", None) or subscription.get(
        "current_period_end"
    )
    cancel_at_period_end = getattr(subscription, "cancel_at_period_end", None) or subscription.get(
        "cancel_at_period_end"
    )

    user.stripe_subscription_id = subscription_id or user.stripe_subscription_id
    user.stripe_customer_id = customer_id or user.stripe_customer_id
    user.subscription_status = status
    user.subscription_price_id = price_id
    user.subscription_current_period_end = _timestamp_to_datetime(current_period_end)
    user.subscription_cancel_at_period_end = bool(cancel_at_period_end)

    if user.role != UserRole.ADMIN:
        current_role = user.role
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role.lower())
            except (KeyError, ValueError):
                current_role = UserRole.USER
        if _should_have_plus_role(status):
            user.role = UserRole.PLUS
        else:
            user.role = UserRole.USER

    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info(
        "Oppdaterte Stripe-abonnement %s for bruker %s (%s)",
        subscription_id,
        user.email,
        interval or "ukjent interval",
    )


async def handle_event(session: AsyncSession, event: Any) -> None:
    settings = get_stripe_settings()
    event_type = getattr(event, "type", None) or event.get("type")
    data_object = None
    data = getattr(event, "data", None) or event.get("data")
    if isinstance(data, dict):
        data_object = data.get("object")

    if event_type == "checkout.session.completed":
        subscription_id = data_object.get("subscription") if isinstance(data_object, dict) else None
        customer_id = data_object.get("customer") if isinstance(data_object, dict) else None
        metadata = data_object.get("metadata") if isinstance(data_object, dict) else None

        if not subscription_id:
            logger.warning("Checkout-event mangler subscription-id")
            return

        stripe_module = _ensure_stripe_module(settings)
        try:
            subscription = await asyncio.to_thread(
                stripe_module.Subscription.retrieve,
                subscription_id,
                expand=["items.data.price"],
            )
        except StripeError as exc:  # pragma: no cover - integration error path
            logger.exception("Kunne ikke hente Stripe-subscription %s", subscription_id)
            raise StripeOperationError("Kunne ikke hente abonnement fra Stripe") from exc

        await _apply_subscription_update(
            session,
            subscription=subscription,
            metadata=metadata,
        )
        return

    if event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
    }:
        await _apply_subscription_update(session, subscription=data_object)
        return

    logger.debug("Ignorerer Stripe-event %s", event_type)
