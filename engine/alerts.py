"""Alerts: something the person should know about. Always stored (the bell in the app); optionally pushed to email and a webhook.

An alert is unique per (brand, kind, ref), so the same finding never alerts twice however many times a scan sees it.
Delivery failures are recorded on the alert and never raise: an unreachable webhook must not break detection.
"""
import json
from datetime import datetime, timezone

import httpx

from engine import senders
from engine.clients.base import NotConfigured
from engine.settings import get_settings

RANK = {"info": 0, "medium": 1, "high": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def notify(store, brand_id: str, kind: str, title: str, detail: str = "", href: str = "/home", severity: str = "info", ref: str | None = None) -> dict | None:
    """Store the alert and push it outside the app if it is severe enough. Returns the alert, or None if this one was already raised."""
    row = store.db.execute(
        "INSERT INTO alerts (brand_id, kind, severity, title, detail, href, ref, created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING *",
        (brand_id, kind, severity, title, detail, href, ref or title, _now())).fetchone()
    if not row:
        return None
    alert = dict(row)
    delivered = await deliver(alert, store)
    if delivered:
        store.db.execute("UPDATE alerts SET delivered=? WHERE id=?", (json.dumps(delivered), alert["id"]))
        alert["delivered"] = delivered
    return alert


async def deliver(alert: dict, store=None, force: bool = False) -> dict:
    """Push to the webhook and email, when configured and the alert is at least ALERT_MIN_SEVERITY. Returns {channel: 'sent' | error}."""
    s = get_settings()
    if not force and RANK.get(alert["severity"], 0) < RANK.get(s.alert_min_severity, 1):
        return {}
    link = s.public_url.rstrip("/") + (alert.get("href") or "/home")
    text = f"[HAUL16] {alert['title']}\n{alert.get('detail') or ''}\n{link}".strip()
    out: dict = {}
    if s.alert_webhook_url:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(s.alert_webhook_url, json={"text": text, "title": alert["title"], "detail": alert.get("detail"), "severity": alert["severity"], "url": link})
            out["webhook"] = "sent" if r.status_code < 300 else f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            out["webhook"] = f"failed: {e!r}"[:160]
    if s.alert_email_to and senders.smtp_ready():
        try:
            await senders.send_email([x.strip() for x in s.alert_email_to.split(",") if x.strip()], f"[HAUL16] {alert['title']}", text)
            out["email"] = "sent"
        except NotConfigured as e:
            out["email"] = str(e)
        except Exception as e:  # noqa: BLE001
            out["email"] = f"failed: {e!r}"[:160]
    return out


def list_alerts(store, brand_id: str, unread: bool = False, limit: int = 50) -> list[dict]:
    q = "SELECT * FROM alerts WHERE brand_id=?" + (" AND read_at IS NULL" if unread else "") + " ORDER BY created_at DESC, id DESC LIMIT ?"
    return [dict(r) for r in store.db.execute(q, (brand_id, limit))]


def unread_count(store, brand_id: str) -> int:
    return store.db.execute("SELECT COUNT(*) AS n FROM alerts WHERE brand_id=? AND read_at IS NULL", (brand_id,)).fetchone()["n"]


def mark_read(store, brand_id: str, ids: list[int] | None = None) -> int:
    if ids:
        r = store.db.execute("UPDATE alerts SET read_at=? WHERE brand_id=? AND read_at IS NULL AND id = ANY(?)", (_now(), brand_id, ids))
    else:
        r = store.db.execute("UPDATE alerts SET read_at=? WHERE brand_id=? AND read_at IS NULL", (_now(), brand_id))
    return r.rowcount
