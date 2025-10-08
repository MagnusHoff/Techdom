from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage


logger = logging.getLogger(__name__)


def _smtp_config() -> dict[str, str]:
    return {
        "host": os.getenv("SMTP_HOST", ""),
        "port": os.getenv("SMTP_PORT", ""),
        "username": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "use_tls": os.getenv("SMTP_USE_TLS", "1"),
        "use_ssl": os.getenv("SMTP_USE_SSL", "0"),
        "sender": os.getenv("EMAIL_SENDER", "no-reply@techdom.ai"),
    }


def _should(value: str, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _parse_port(raw: str) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.error("SMTP_PORT må være et tall, men fikk %s", raw)
        return None


def send_password_reset_email(recipient: str, reset_url: str) -> None:
    config = _smtp_config()
    port = _parse_port(config["port"])
    host = config["host"].strip()

    message = EmailMessage()
    message["Subject"] = "Tilbakestill passordet ditt"
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                "Hei!",
                "",
                "Vi har mottatt en forespørsel om å tilbakestille passordet ditt hos Techdom.ai.",
                "Klikk lenken under for å velge et nytt passord:",
                reset_url,
                "",
                "Hvis du ikke ba om dette, kan du ignorere e-posten.",
                "",
                "Hilsen Team Techdom",
            ]
        )
    )

    if not host or not port:
        logger.info(
            "Skip sending password reset email via SMTP; missing SMTP_HOST/SMTP_PORT. Link for %s: %s",
            recipient,
            reset_url,
        )
        return

    use_ssl = _should(config["use_ssl"], default=False)
    use_tls = _should(config["use_tls"], default=not use_ssl)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                if config["username"] and config["password"]:
                    server.login(config["username"], config["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls:
                    server.starttls()
                if config["username"] and config["password"]:
                    server.login(config["username"], config["password"])
                server.send_message(message)
    except Exception:
        logger.exception("Kunne ikke sende passordreset-epost til %s", recipient)


def send_email_verification_email(recipient: str, verification_url: str) -> None:
    config = _smtp_config()
    port = _parse_port(config["port"])
    host = config["host"].strip()

    message = EmailMessage()
    message["Subject"] = "Bekreft e-posten din"
    message["From"] = config["sender"]
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                "Hei!",
                "",
                "Velkommen til Techdom.ai! Klikk lenken under for å bekrefte e-postadressen din:",
                verification_url,
                "",
                "Hvis du ikke opprettet en konto, kan du ignorere denne e-posten.",
                "",
                "Hilsen Team Techdom",
            ]
        )
    )

    if not host or not port:
        logger.info(
            "Skip sending email verification via SMTP; missing SMTP_HOST/SMTP_PORT. Link for %s: %s",
            recipient,
            verification_url,
        )
        return

    use_ssl = _should(config["use_ssl"], default=False)
    use_tls = _should(config["use_tls"], default=not use_ssl)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                if config["username"] and config["password"]:
                    server.login(config["username"], config["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls:
                    server.starttls()
                if config["username"] and config["password"]:
                    server.login(config["username"], config["password"])
                server.send_message(message)
    except Exception:
        logger.exception("Kunne ikke sende e-postverifisering til %s", recipient)
