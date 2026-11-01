"""Is the reported site still up? Detects takedowns without anyone clicking "removed".

A site counts as gone when its domain no longer resolves, the page returns 404/410/451, it shows a suspension notice, or (for Telegram and
WhatsApp) the public page no longer shows the account or group. Anything unclear is `unknown`, never `gone`. A removal event is recorded only
for notices that were really filed (by hand or by email): a dry-run notice sent nothing, so a site that went down is not our takedown.
"""
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from engine import alerts
from engine.clients import dnsx, telegram, whatsapp
from engine.clients.base import UpstreamError, new_client, request
from engine.social_hosts import is_social

SUSPENDED = re.compile(r"(account|site|domain|page|website) (has been|is|was) (suspended|disabled|removed|terminated|deactivated)|suspended for|"
                       r"removed for violat|this (site|page) (can.t|cannot) be reached|deceptive site ahead|phishing (site )?(warning|detected)", re.I)


@asynccontextmanager
async def _client(http):
    """Use the caller's client as is, or open (and close) our own SSRF-guarded one."""
    if http is not None:
        yield http
    else:
        async with new_client(timeout=20) as c:
            yield c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def check_site(http: httpx.AsyncClient, hostname: str, url: str | None, records=dnsx.records) -> dict:
    """{status: live | gone | unknown, reason}"""
    try:
        if hostname.startswith("t.me/"):
            pv = await telegram.preview(http, hostname.split("/", 1)[1])
            return {"status": "live", "reason": "account still public"} if pv else {"status": "gone", "reason": "the Telegram account or channel no longer exists"}
        if hostname.startswith("chat.whatsapp.com/"):
            g = await whatsapp.preview(http, hostname.split("/", 1)[1])
            return {"status": "live", "reason": "invite link still works"} if g else {"status": "gone", "reason": "the WhatsApp invite link no longer works"}
        if not (await records(hostname, "A") or await records(hostname, "AAAA")):
            return {"status": "gone", "reason": "the domain no longer resolves"}
        res = await request(http, "GET", url or f"https://{hostname}/", retries=0)
        if res.status_code in (404, 410, 451):
            return {"status": "gone", "reason": f"the page returns HTTP {res.status_code}"}
        if res.status_code < 400 and SUSPENDED.search(res.text[:40_000]):
            return {"status": "gone", "reason": "the page shows a suspension notice"}
        if res.status_code >= 400:
            return {"status": "unknown", "reason": f"HTTP {res.status_code} (could be a block or an outage, not proof of removal)"}
        return {"status": "live", "reason": f"HTTP {res.status_code}"}
    except UpstreamError as e:
        return {"status": "unknown", "reason": f"could not reach it: {str(e)[:100]}"}


def latest(store, case_id: str) -> list[dict]:
    rows = store.db.execute(
        """SELECT DISTINCT ON (s.ref) s.ref AS threat_id, t.hostname, s.data, s.created_at FROM signals s JOIN threats t ON t.id = s.ref
           WHERE s.kind='liveness' AND t.case_id=? ORDER BY s.ref, s.created_at DESC, s.id DESC""", (case_id,))
    return [{"threat_id": r["threat_id"], "hostname": r["hostname"], "checked_at": r["created_at"], **(r["data"] or {})} for r in rows]


async def check_case(store, case_id: str, http: httpx.AsyncClient | None = None, records=dnsx.records) -> list[dict]:
    """Re-check every site in a case that has a filed notice. Records what it saw, and a removal for really-filed notices of a gone site."""
    case = store.db.execute("SELECT brand_id FROM cases WHERE id=?", (case_id,)).fetchone()
    notices = [n for n in store.notices(case_id) if n["status"] in ("filed", "filed_dry_run")]
    results = []
    async with _client(http) as client:
        for tid in dict.fromkeys(n["threat_id"] for n in notices):
            t = store.threat(tid)
            r = await check_site(client, t["hostname"], (t.get("capture") or {}).get("final_url") or t.get("url"), records)
            store.db.execute("INSERT INTO signals (brand_id, kind, source, ref, data, created_at) VALUES (?,?,?,?,?,?)",
                             (case["brand_id"], "liveness", "liveness", tid, json.dumps(r), _now()))
            results.append({"threat_id": tid, "hostname": t["hostname"], **r})
            if r["status"] != "gone":
                continue
            for n in (x for x in notices if x["threat_id"] == tid and x["status"] == "filed"):
                if store.db.execute("SELECT 1 FROM clock_events WHERE notice_id=? AND event='removed'", (n["id"],)).fetchone():
                    continue
                store.clock_event(case_id, n["id"], n["channel"], "removed", f"Detected automatically: {r['reason']}", simulated=False)
                await alerts.notify(store, case["brand_id"], "takedown_confirmed", f"Taken down: {t['hostname']}", f"{r['reason'].capitalize()}. Detected automatically after your {n['channel_name']} notice.",
                                    f"/cases/{case_id}?tab=clock", "medium", ref=f"removed-{n['id']}")
    return results


async def check_brand(store, brand_id: str) -> int:
    """Re-check every case of a brand that has a filed notice. Returns how many cases were checked."""
    ids = [r["case_id"] for r in store.db.execute(
        "SELECT DISTINCT n.case_id FROM notices n JOIN cases c ON c.id=n.case_id WHERE c.brand_id=? AND n.status IN ('filed','filed_dry_run')", (brand_id,))]
    for cid in ids:
        await check_case(store, cid)
    return len(ids)
