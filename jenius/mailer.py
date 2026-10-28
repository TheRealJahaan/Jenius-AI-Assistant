"""Sending email (only password resets for now). Without SMTP settings the message goes to the log,
which is fine on a laptop and makes a missing setting obvious in production."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from .config import Settings

log = logging.getLogger("jenius.mail")


def send_email(settings: Settings, to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        log.warning("SMTP_HOST is not set, so this email was not sent. To: %s | Subject: %s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def send_password_reset(settings: Settings, to: str, name: str, link: str) -> None:
    send_email(settings, to, "Reset your Jenius password", (
        f"Hi {name},\n\n"
        f"Someone asked to reset the password for your Jenius account. If it was you, open this link "
        f"within 1 hour:\n\n{link}\n\n"
        f"If it wasn't you, ignore this email; your password stays the same.\n\n— Jenius"
    ))
