"""Real filing: by hand and by email, evidence packs, and automatic takedown detection."""
import asyncio
import base64
import http.server
import io
import json
import socket
import threading
import zipfile

import httpx
import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient

from engine import api, liveness
from engine.brand.repo import BrandRepo
from engine.settings import get_settings
from tests.conftest import TABLES
from tests.test_api import seed_notice


@pytest.fixture()
def client(db_url):
    from engine.store import Store
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    s.db.close()
    with TestClient(api.app) as c:
        yield c


@pytest.fixture()
def cfg(monkeypatch):
    def setk(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(k.upper(), str(v))
        get_settings.cache_clear()
    yield setk
    get_settings.cache_clear()


@pytest.fixture()
def smtp_sink():
    box = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            box.append(envelope)
            return "250 OK"

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ctl = Controller(Handler(), hostname="127.0.0.1", port=port)
    ctl.start()
    yield port, box
    ctl.stop()


def approved(client, method="email", verified=True, recipient="abuse@registrar.test"):
    nid, case_id = seed_notice()
    api.S.store.db.execute("UPDATE notices SET method=?, recipient=?, recipient_verified=?, channel='registrar', channel_name='Registrar abuse desk' WHERE id=?",
                           (method, recipient, int(verified), nid))
    client.post(f"/api/notices/{nid}/approve", json={"approver": "Ama Mensah"})
    return nid, case_id


def test_filing_by_hand_is_recorded_as_real_not_simulated(client):
    nid, case_id = approved(client)
    r = client.post(f"/api/notices/{nid}/file", json={"mode": "manual", "reference": "Case 4411 on the registrar's form"}).json()
    assert r["status"] == "filed"
    clock = client.get(f"/api/cases/{case_id}").json()["clock"]
    assert clock[0]["event"] == "filed" and clock[0]["simulated"] == 0 and clock[0]["note"] == "Case 4411 on the registrar's form"
    assert client.get("/api/stats").json()["counts"]["notices_filed"] == 1                       # real filings count like dry-run ones
    assert [n["id"] for n in client.get("/api/notices", params={"status": "filed"}).json()] == [nid]
    after = client.post(f"/api/notices/{nid}/clock", json={"event": "first_response"}).json()
    assert after[-1]["simulated"] == 0                                                            # a person logging a real reply is not simulated
    assert "notice_filed" in [a["action"] for a in client.get("/api/audit").json()]


def test_dry_run_filing_still_simulates(client):
    nid, case_id = approved(client)
    assert client.post(f"/api/notices/{nid}/file").json()["status"] == "filed_dry_run"           # default, no body
    assert client.get(f"/api/cases/{case_id}").json()["clock"][0]["simulated"] == 1


def test_email_filing_is_gated_step_by_step(client, cfg):
    nid, _ = approved(client)
    r = client.post(f"/api/notices/{nid}/file", json={"mode": "email"})
    assert r.status_code == 409 and "dry run" in r.json()["detail"]
    cfg(haul16_dry_run="false")
    assert client.post(f"/api/notices/{nid}/file", json={"mode": "email"}).status_code == 409                      # SMTP not set up
    cfg(haul16_dry_run="false", smtp_host="127.0.0.1")
    web, _ = approved(client, method="web_form", recipient="https://form.test/report")
    assert "web form" in client.post(f"/api/notices/{web}/file", json={"mode": "email"}).json()["detail"]
    unv, _ = approved(client, verified=False)
    assert "not verified" in client.post(f"/api/notices/{unv}/file", json={"mode": "email"}).json()["detail"]
    assert api.S.store.notice(nid)["status"] == "approved"                                                         # nothing was filed by any refused attempt


def test_email_filing_sends_the_notice_with_letter_and_evidence_pack(client, cfg, smtp_sink):
    port, box = smtp_sink
    cfg(haul16_dry_run="false", smtp_host="127.0.0.1", smtp_port=port, email_from="takedowns@haul16.test")
    letter = b"%PDF-1.4 signed authorisation"
    client.post("/api/brands", json={"display_name": "x", "website": "x.test"})                                      # unrelated brand; demo brand is the case's
    from engine.brand.routes import LETTERS
    LETTERS.mkdir(parents=True, exist_ok=True)
    import hashlib
    sha = hashlib.sha256(letter).hexdigest()
    (LETTERS / f"brand_demo-{sha[:12]}-letter.pdf").write_bytes(letter)
    BrandRepo(api.S.store).update("brand_demo", authorisation_letter_ref="letter.pdf", authorisation_letter_sha256=sha)
    nid, case_id = approved(client)
    r = client.post(f"/api/notices/{nid}/file", json={"mode": "email"})
    assert r.status_code == 200 and r.json()["status"] == "filed"
    assert len(box) == 1 and box[0].rcpt_tos == ["abuse@registrar.test"] and box[0].mail_from == "takedowns@haul16.test"
    raw = box[0].content.decode()
    assert "Subject: s" in raw and "authorisation-letter.pdf" in raw and "evidence-pack.zip" in raw
    audit = next(a for a in client.get("/api/audit").json() if a["action"] == "notice_filed")
    assert audit["detail"]["mode"] == "email" and audit["detail"]["message_id"].endswith("@haul16>")
    assert client.get(f"/api/cases/{case_id}").json()["clock"][0]["note"] == "Sent by email by HAUL16."


def test_a_failing_mail_server_does_not_mark_the_notice_filed(client, cfg):
    cfg(haul16_dry_run="false", smtp_host="127.0.0.1", smtp_port=9)                                                   # nothing is listening
    nid, _ = approved(client)
    r = client.post(f"/api/notices/{nid}/file", json={"mode": "email"})
    assert r.status_code == 502 and "could not be sent" in r.json()["detail"]
    assert api.S.store.notice(nid)["status"] == "approved"


def test_evidence_pack_contains_the_notice_hashes_and_files(client):
    from engine.settings import ROOT
    ev = ROOT / "data" / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "test-pack.png").write_bytes(b"\x89PNG fake")
    (ev / "test-pack.html").write_text("<html>captured</html>")
    nid, _ = approved(client)
    n = api.S.store.notice(nid)
    api.S.store.save_result(n["threat_id"], {"final_url": "https://x.test/", "screenshot_ref": "data/evidence/test-pack.png", "html_sha256": "ab" * 32, "title": "Fake"},
                            {"confidence": "high", "score": 90, "reason_codes": ["lure_language_on_page"], "evidence": {}, "rules_version": "t"})
    try:
        r = client.get(f"/api/notices/{nid}/pack")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert {"notice.txt", "evidence.json", "README.txt", "screenshot.png", "page-source.html"} <= set(z.namelist())
        assert "To: abuse@registrar.test" in z.read("notice.txt").decode()
        ej = json.loads(z.read("evidence.json"))
        assert ej["capture"]["html_sha256"] == "ab" * 32 and ej["verdict"]["confidence"] == "high"
    finally:
        (ev / "test-pack.png").unlink(missing_ok=True)
        (ev / "test-pack.html").unlink(missing_ok=True)
    assert client.get("/api/notices/nope/pack").status_code == 404


# ---- liveness ---------------------------------------------------------------------------

@pytest.fixture()
def site():
    state = {"mode": "live"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            m = state["mode"]
            code = {"live": 200, "gone": 404, "suspended": 200, "error": 500}[m]
            body = b"<html>This account has been suspended for violating our terms</html>" if m == "suspended" else b"<html>Welcome</html>"
            self.send_response(code); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


async def has_dns(host, rtype):
    return ["203.0.113.1"] if rtype == "A" else []


async def no_dns(host, rtype):
    return []


def check(url, hostname="x.test", records=has_dns):
    async def go():
        async with httpx.AsyncClient() as c:
            return await liveness.check_site(c, hostname, url, records)
    return asyncio.run(go())


def test_site_status_live_gone_and_unknown(site):
    state, url = site
    assert check(url)["status"] == "live"
    state["mode"] = "gone"
    assert check(url) == {"status": "gone", "reason": "the page returns HTTP 404"}
    state["mode"] = "suspended"
    assert check(url) == {"status": "gone", "reason": "the page shows a suspension notice"}
    state["mode"] = "error"
    assert check(url)["status"] == "unknown"                                    # a 500 is not proof of a takedown
    assert check(url, records=no_dns) == {"status": "gone", "reason": "the domain no longer resolves"}
    assert check("http://127.0.0.1:9/")["status"] == "unknown"                  # unreachable is unknown, not gone


def test_telegram_and_whatsapp_status(monkeypatch):
    from engine.clients import telegram, whatsapp
    async def tg_alive(c, h): return {"handle": h}
    async def tg_dead(c, h): return None
    monkeypatch.setattr(telegram, "preview", tg_alive)
    assert check(None, "t.me/acmepay_help")["status"] == "live"
    monkeypatch.setattr(telegram, "preview", tg_dead)
    assert "no longer exists" in check(None, "t.me/acmepay_help")["reason"]
    monkeypatch.setattr(whatsapp, "preview", tg_dead)
    assert check(None, "chat.whatsapp.com/ABCDEFGHIJKLMNOP")["status"] == "gone"


def test_removal_is_recorded_only_for_really_filed_notices_and_only_once(store, site):
    state, url = site
    tid = store.add_threat("b1", "scam.test", url, "manual", None, {})
    store.save_result(tid, {"final_url": url}, {"confidence": "high", "score": 90, "reason_codes": [], "evidence": {}})
    case_id, _ = store.assign_case(tid, "b1", "Suspected clone")
    store.add_notices(case_id, [{"channel": "registrar", "channel_name": "Registrar", "method": "email", "recipient": "a@b.test", "recipient_verified": True, "subject": "s", "body": "b", "evidence_refs": {}},
                                {"channel": "hosting", "channel_name": "Hosting", "method": "email", "recipient": "c@d.test", "recipient_verified": True, "subject": "s", "body": "b", "evidence_refs": {}}], tid)
    real, dry = store.notices(case_id)
    for n in (real, dry):
        store.db.execute("UPDATE notices SET status='approved', approved_by='A' WHERE id=?", (n["id"],))
    store.file_notice(real["id"], "A", "manual")
    store.file_notice(dry["id"], "A", "dry_run")

    def run():
        async def go():
            async with httpx.AsyncClient() as c:
                return await liveness.check_case(store, case_id, c, has_dns)
        return asyncio.run(go())

    assert run()[0]["status"] == "live" and [e for e in store.clock(case_id) if e["event"] == "removed"] == []
    state["mode"] = "gone"
    assert run()[0]["status"] == "gone"
    removed = [e for e in store.clock(case_id) if e["event"] == "removed"]
    assert len(removed) == 1 and removed[0]["notice_id"] == real["id"] and removed[0]["simulated"] == 0
    assert removed[0]["note"].startswith("Detected automatically: the page returns HTTP 404")        # the dry-run notice got nothing
    run()
    assert len([e for e in store.clock(case_id) if e["event"] == "removed"]) == 1                    # a second check does not duplicate
    a = store.db.execute("SELECT kind, severity, title FROM alerts WHERE kind='takedown_confirmed'").fetchall()
    assert len(a) == 1 and a[0]["title"] == "Taken down: scam.test"
    latest = liveness.latest(store, case_id)
    assert latest[0]["status"] == "gone" and latest[0]["hostname"] == "scam.test"


def test_liveness_endpoints(client):
    nid, case_id = approved(client)
    client.post(f"/api/notices/{nid}/file", json={"mode": "manual"})
    assert client.get(f"/api/cases/{case_id}/liveness").json() == []
    assert client.get("/api/cases/nope/liveness").status_code == 404
    assert client.post("/api/cases/nope/liveness").status_code == 404
