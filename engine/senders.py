"""Outbound email over SMTP (alerts, and notice sending when dry run is off). Nothing here runs unless SMTP_HOST is set."""
import asyncio
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from engine.clients.base import NotConfigured
from engine.settings import get_settings


def smtp_ready() -> bool:
    return bool(get_settings().smtp_host)


def _send(msg: EmailMessage) -> None:
    s = get_settings()
    ctx = ssl.create_default_context()
    if s.smtp_ssl:
        server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
    with server:
        if not s.smtp_ssl:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=ctx)
                server.ehlo()
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password or "")
        server.send_message(msg)


async def send_email(to: str | list[str], subject: str, body: str, attachments: list[tuple[str, bytes, str]] = (), reply_to: str | None = None) -> str:
    """Send one email. Returns the Message-ID. Raises NotConfigured without SMTP_HOST, or the SMTP error."""
    s = get_settings()
    if not s.smtp_host:
        raise NotConfigured("SMTP_HOST is not set")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.email_from, ", ".join([to] if isinstance(to, str) else to), subject
    if reply_to or s.email_reply_to:
        msg["Reply-To"] = reply_to or s.email_reply_to
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@haul16>"
    msg.set_content(body)
    for name, data, mime in attachments:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main or "application", subtype=sub or "octet-stream", filename=name)
    await asyncio.to_thread(_send, msg)
    return msg["Message-ID"]
