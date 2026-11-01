"""Alerts (stored, deduped, pushed to a webhook and real SMTP), the patrol schedule, and the settings endpoints."""
import asyncio
import http.server
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient

from engine import alerts, api, jobs, patrol
from engine.brand.repo import BrandRepo
from engine.matcher import Brand
from engine.settings import get_settings
from engine.worker import Watch
from tests.conftest import TABLES


@pytest.fixture()
def webhook():
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200); self.end_headers()
        def log_message(self, *a): pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/hook", got
    srv.shutdown()


@pytest.fixture()
def smtp_sink():
    box = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            box.append(envelope)
            return "250 OK"

    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ctl = Controller(Handler(), hostname="127.0.0.1", port=port)
    ctl.start()
    yield port, box
    ctl.stop()


@pytest.fixture()
def cfg(monkeypatch):
    def setk(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(k.upper(), str(v))
        get_settings.cache_clear()
    yield setk
    get_settings.cache_clear()


def test_alert_is_stored_once_and_can_be_read(store):
    a = asyncio.run(alerts.notify(store, "b1", "finding", "High look-alike: x.com", "detail", "/threats/t1", "high", ref="t1"))
    assert a and a["title"].startswith("High") and alerts.unread_count(store, "b1") == 1
    assert asyncio.run(alerts.notify(store, "b1", "finding", "again", ref="t1")) is None            # same finding, same alert
    asyncio.run(alerts.notify(store, "b1", "finding", "Medium: y.com", severity="medium", ref="t2"))
    assert [x["ref"] for x in alerts.list_alerts(store, "b1")] == ["t2", "t1"]
    assert alerts.mark_read(store, "b1", [a["id"]]) == 1 and alerts.unread_count(store, "b1") == 1
    assert alerts.mark_read(store, "b1") == 1 and alerts.unread_count(store, "b1") == 0
    assert alerts.list_alerts(store, "other") == []


def test_webhook_gets_only_alerts_that_are_severe_enough(store, webhook, cfg):
    url, got = webhook
    cfg(alert_webhook_url=url, public_url="https://app.test", alert_min_severity="medium")
    a = asyncio.run(alerts.notify(store, "b1", "scan_done", "Scan done", "info only", "/threats", "info", ref="s1"))
    assert got == [] and not a.get("delivered")                                                    # info stays in the app
    b = asyncio.run(alerts.notify(store, "b1", "finding", "High look-alike: x.com", "reasons", "/cases/c1", "high", ref="t1"))
    assert b["delivered"] == {"webhook": "sent"}
    assert got[0]["severity"] == "high" and "https://app.test/cases/c1" in got[0]["text"] and got[0]["text"].startswith("[HAUL16] High look-alike")


def test_a_dead_webhook_is_recorded_and_never_raises(store, cfg):
    cfg(alert_webhook_url="http://127.0.0.1:9/nope")
    a = asyncio.run(alerts.notify(store, "b1", "finding", "High", severity="high", ref="t1"))
    assert a["delivered"]["webhook"].startswith("failed")


def test_alert_email_is_sent_over_real_smtp(store, smtp_sink, cfg):
    port, box = smtp_sink
    cfg(smtp_host="127.0.0.1", smtp_port=port, alert_email_to="owner@acme.test, ops@acme.test", email_from="haul16@acme.test", public_url="https://app.test")
    a = asyncio.run(alerts.notify(store, "b1", "finding", "High look-alike: x.com", "why", "/threats/t1", "high", ref="t1"))
    assert a["delivered"] == {"email": "sent"} and len(box) == 1
    msg = box[0]
    assert msg.rcpt_tos == ["owner@acme.test", "ops@acme.test"] and msg.mail_from == "haul16@acme.test"
    body = msg.content.decode()
    assert "Subject: [HAUL16] High look-alike: x.com" in body and "https://app.test/threats/t1" in body


@pytest.fixture()
def watch(store):
    repo = BrandRepo(store)
    b = repo.create("Acme Pay", "acmepay.com")
    store.db.execute("UPDATE brands SET status='ready', is_active=TRUE WHERE id=?", (b["id"],))
    return Watch(Brand(brand_id=b["id"], display_name="Acme Pay", seed_terms=["acmepay"], official_domains=["acmepay.com"]), store, workers=1, enrich_hosts=False), b["id"]


def test_patrol_queues_a_scan_when_one_is_due(store, watch):
    w, bid = watch
    assert patrol.tick(store, w) is True and jobs.latest(store, "discovery", bid)["status"] == "queued"      # never scanned: due now
    assert patrol.tick(store, w) is False                                                                    # already queued
    job = jobs.claim(store)
    jobs.complete(store, job["id"], {})
    assert patrol.tick(store, w) is False                                                                    # scanned just now, next one in 6h
    store.db.execute("UPDATE jobs SET updated_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(timespec="seconds"), job["id"]))
    assert patrol.tick(store, w) is True                                                                     # 7 hours later: due again


def test_patrol_can_be_turned_off_per_brand_and_ignores_unprotected_brands(store, watch):
    w, bid = watch
    BrandRepo(store).update(bid, inputs={"patrol_hours": 0})
    assert patrol.tick(store, w) is False and patrol.next_scan(store, BrandRepo(store).get(bid)) is None
    BrandRepo(store).update(bid, inputs={"patrol_hours": 1})
    store.db.execute("UPDATE brands SET is_active=FALSE WHERE id=?", (bid,))
    assert patrol.tick(store, w) is False                                                                    # not protecting: nothing to patrol


@pytest.fixture()
def client(db_url):
    from engine.store import Store
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    s.db.close()
    with TestClient(api.app) as c:
        yield c


def test_alert_endpoints_and_test_alert(client, webhook, cfg):
    url, got = webhook
    cfg(alert_webhook_url=url)
    r = client.post("/api/alerts/test").json()
    assert r["delivered"] == {"webhook": "sent"} and r["channels"] == {"webhook": True, "email": False}
    lst = client.get("/api/alerts").json()
    assert lst["unread"] == 1 and lst["items"][0]["title"] == "Test alert from HAUL16"
    assert client.post("/api/alerts/read", json={}).json() == {"marked": 1, "unread": 0}
    assert client.get("/api/alerts", params={"unread": True}).json()["items"] == []
    assert len(got) == 1


def test_source_settings_endpoints(client):
    src = {s["id"]: s for s in client.get("/api/sources").json()}
    assert set(src) >= {"dns_sweep", "ct_history", "telegram_handles", "urlscan", "web_search", "news_mentions", "meta_ads"}
    assert src["urlscan"]["configured"] is False and src["dns_sweep"]["needs_key"] is None and src["ct_history"]["enabled"] is True
    assert client.put("/api/sources/ct_history", json={"enabled": False}).json()["enabled"] is False
    assert {s["id"]: s["enabled"] for s in client.get("/api/sources").json()}["ct_history"] is False
    assert client.put("/api/sources/bogus", json={"enabled": True}).status_code == 404


def test_patrol_interval_is_a_brand_setting(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()
    assert b["patrol_hours"] == 6
    assert client.patch(f"/api/brands/{b['id']}", json={"patrol_hours": 24}).json()["patrol_hours"] == 24
    assert client.patch(f"/api/brands/{b['id']}", json={"patrol_hours": 0}).json()["patrol_hours"] == 0
    assert client.patch(f"/api/brands/{b['id']}", json={"patrol_hours": 999}).status_code == 422
    BrandRepo(api.S.store).update(b["id"], status="ready")
    client.post(f"/api/brands/{b['id']}/activate")
    s = client.get(f"/api/brands/{b['id']}/setup").json()
    assert s["patrol"]["hours"] == 0 and s["patrol"]["next_at"] is None
    client.patch(f"/api/brands/{b['id']}", json={"patrol_hours": 12})
    assert client.get(f"/api/brands/{b['id']}/setup").json()["patrol"]["next_at"] is not None
