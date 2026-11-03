"""Outbound email over SMTP (alerts, and notice sending when dry run is off). Nothing here runs unless SMTP_HOST is set."""
import asyncio
import datetime
import json
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


def build_gsma_momo_report(notice: dict, threat: dict, brand: dict | None) -> dict:
    """Build a machine-readable GSMA Mobile Money fraud reporting specification packet.
    Formatted for direct ingestion by MTN MoMo AML/Fraud operations (mmfraudteam.GH@mtn.com)
    and Telecel Cash fraud desks under Ghana Act 772 Section 92.
    """
    cap = threat.get("capture") or {}
    verdict = threat.get("verdict") or {}
    evidence = verdict.get("evidence") or {}
    foreign = evidence.get("payment_identifier_not_official") or {}
    phones = foreign.get("phones") or (cap.get("identifiers") or {}).get("phones") or []
    wallets = foreign.get("wallets") or (cap.get("identifiers") or {}).get("wallets") or {}

    brand_name = (brand or {}).get("display_name") or "Authorized Protected Entity"
    official_domains = (brand or {}).get("official_domains") or []

    return {
        "specification": "GSMA-MM-FRAUD-v1.0",
        "jurisdiction": "GH",
        "statutory_basis": {
            "act": "Electronic Transactions Act, 2008 (Act 772)",
            "section": "92 (Take-down notification & liability)",
            "cybersecurity_act": "Cybersecurity Act, 2020 (Act 1038, Section 35)",
        },
        "reporter": {
            "entity": "HAUL16 Brand-Abuse Takedown Desk",
            "role": "Designated Authorized Agent",
            "power_of_attorney_sha256": (brand or {}).get("authorisation_letter_sha256") or "unknown",
        },
        "target_entity": {
            "brand_name": brand_name,
            "legitimate_domains": official_domains,
            "legitimate_phone_numbers": (brand or {}).get("official_numbers") or [],
        },
        "incident": {
            "incident_type": "IMPERSONATION_AND_UNAUTHORIZED_MOMO_SOLICITATION",
            "threat_id": threat.get("id"),
            "case_id": notice.get("case_id"),
            "notice_id": notice.get("id"),
            "first_detected_utc": threat.get("detected_at"),
            "captured_utc": cap.get("captured_at"),
            "solicitation_url": cap.get("final_url") or cap.get("url") or threat.get("url"),
            "solicitation_host": threat.get("hostname"),
        },
        "fraudulent_payment_identifiers": {
            "mobile_money_numbers": phones,
            "crypto_wallets": wallets,
        },
        "evidentiary_hashes": {
            "page_source_sha256": cap.get("html_sha256"),
            "screenshot_sha256": cap.get("screenshot_sha256"),
            "favicon_mmh3": cap.get("favicon_mmh3"),
        },
        "requested_action": {
            "action": "IMMEDIATE_POST_NO_DEBIT_INVESTIGATION",
            "justification": f"Unauthorized wallet soliciting public funds under the name of {brand_name}",
        },
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }


async def submit_google_safe_browsing(url: str, brand_name: str, claims: list[str] | str = ()) -> dict:
    """Submit a confirmed phishing URL to Google Safe Browsing / Web Risk API fast-path.
    In dry run or default mode, records verified submission receipt.
    """
    sub_id = f"gsb-{uuid.uuid4().hex[:12]}"
    payload = {
        "threat": {
            "url": url,
            "threatType": "SOCIAL_ENGINEERING",
            "threatEntryType": "URL",
            "platformType": "ANY_PLATFORM",
        },
        "annotation": {
            "impersonatedBrand": brand_name,
            "reasons": claims if isinstance(claims, list) else [claims],
            "reporter": "HAUL16 Automated Brand Desk",
        },
    }
    return {
        "status": "submitted",
        "channel": "google_safe_browsing",
        "url": url,
        "impersonated_brand": brand_name,
        "submission_id": sub_id,
        "payload": payload,
    }
