"""Minimal SMTP transport for password-reset links.

`send_email` never raises: a broken/unconfigured mail server must not turn a
password-reset request (or any other action) into a 500. With no SMTP_HOST
configured the body is logged at INFO in development and withheld in
production, where the link must not end up in the logs.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger("cloudcare.mailer")


def send_email(to: str, subject: str, body: str) -> bool:
    if not settings.smtp_host:
        # Only the explicit development environment may print the message
        # body (which can contain a live password-reset link). Any other
        # environment — staging, preview, production — logs only that the
        # email was skipped.
        if settings.env.strip().lower() == "development":
            logger.info(
                "SMTP disabled; email to %s was not sent.\nSubject: %s\n%s",
                to,
                subject,
                body,
            )
            return True
        logger.warning(
            "SMTP is not configured; email to %s was not sent.", to
        )
        return False

    try:
        message = EmailMessage()
        message["From"] = (
            settings.smtp_from
            or settings.smtp_user
            or "no-reply@cloudcare.local"
        )
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)
        return True
    except Exception as exc:  # noqa: BLE001 — never break the caller
        logger.warning("Could not send email to %s: %s", to, exc)
        return False
