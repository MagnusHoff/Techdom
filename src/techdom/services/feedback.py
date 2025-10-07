from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional


class FeedbackConfigError(RuntimeError):
    """Raised when the feedback mail configuration is missing or invalid."""


class FeedbackDeliveryError(RuntimeError):
    """Raised when the feedback message could not be delivered."""


def _read_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class FeedbackMailConfig:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    use_tls: bool
    use_ssl: bool
    from_address: str
    to_address: str
    reply_to_override: Optional[str]

    @classmethod
    def from_env(cls) -> "FeedbackMailConfig":
        host = os.getenv("SMTP_HOST", "").strip()
        if not host:
            raise FeedbackConfigError("SMTP_HOST mangler for feedback-epost.")

        from_address = os.getenv("FEEDBACK_FROM_EMAIL", "").strip() or os.getenv("SMTP_FROM_EMAIL", "").strip()
        if not from_address:
            raise FeedbackConfigError("FEEDBACK_FROM_EMAIL mangler.")

        to_address = os.getenv("FEEDBACK_TO_EMAIL", "").strip() or "support@techdom.ai"

        username = os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD") or os.getenv("SMTP_PASS")

        port_raw = os.getenv("SMTP_PORT", "587").strip()
        try:
            port = int(port_raw)
        except ValueError as exc:  # pragma: no cover - validated via tests
            raise FeedbackConfigError(f"Ugyldig SMTP_PORT verdi: {port_raw!r}") from exc

        use_ssl = _read_bool_env("SMTP_USE_SSL", False)
        use_tls = _read_bool_env("SMTP_USE_TLS", not use_ssl)

        reply_to_override = os.getenv("FEEDBACK_REPLY_TO", "").strip() or None

        return cls(
            host=host,
            port=port,
            username=username.strip() if username else None,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
            from_address=from_address,
            to_address=to_address,
            reply_to_override=reply_to_override,
        )


def send_feedback_email(
    subject: str,
    body: str,
    *,
    reply_to: Optional[str] = None,
    config: Optional[FeedbackMailConfig] = None,
) -> None:
    """Send a feedback email using the configured SMTP transport."""
    cfg = config or FeedbackMailConfig.from_env()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg.from_address
    message["To"] = cfg.to_address
    if cfg.reply_to_override:
        message["Reply-To"] = cfg.reply_to_override
    elif reply_to:
        message["Reply-To"] = reply_to

    message.set_content(body)

    try:
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=20) as client:
                if cfg.username and cfg.password:
                    client.login(cfg.username, cfg.password)
                client.send_message(message)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=20) as client:
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    client.starttls(context=context)
                if cfg.username and cfg.password:
                    client.login(cfg.username, cfg.password)
                client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:  # pragma: no cover - network failures
        raise FeedbackDeliveryError(str(exc)) from exc


__all__ = [
    "FeedbackConfigError",
    "FeedbackDeliveryError",
    "FeedbackMailConfig",
    "send_feedback_email",
]
