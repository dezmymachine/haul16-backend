"""HTTP API on a real Postgres: approval state machine, notices inbox, brand routes, brand scoping, production switches."""
import pytest
from fastapi.testclient import TestClient

from engine import api
from engine.brand.repo import BrandRepo
from engine.clients import dnsx
from tests.conftest import TABLES


@pytest.fixture()
def client(db_url):
    from engine.store import Store
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    s.db.close()
    with TestClient(api.app) as c:
        yield c


def seed_notice(brand_id="brand_demo", status="drafted"):
    st = api.S.store
    tid = st.add_threat(brand_id, "nkwaex-test.com", None, "manual", None, {"reasons": []})
    case_id, _ = st.assign_case(tid, brand_id, "Suspected clone")
    st.add_notices(case_id, [{"channel": "google_safe_browsing", "channel_name": "Google Safe Browsing", "method": "web_form", "recipient": "https://x",
                              "recipient_verified": True, "subject": "s", "body": "b", "evidence_refs": {}}], tid)
    n = st.notices(case_id)[0]
    return n["id"], case_id


def test_health_and_seeded_demo_brand(client):
    assert client.get("/api/health").json()["dry_run"] is True
    brands = client.get("/api/brands").json()
    assert [b["id"] for b in brands] == ["brand_demo"] and brands[0]["is_active"] and brands[0]["fictional"]
    assert client.get("/api/brand").json()["display_name"] == "Nkwa Exchange"
    assert brands[0]["fact_counts"]["domain"] == 1


def test_approval_state_machine_is_enforced_server_side(client):
    nid, case_id = seed_notice()
    assert client.post(f"/api/notices/{nid}/file").status_code == 409                       # not approved yet
    assert client.post(f"/api/notices/{nid}/clock", json={"event": "removed"}).status_code == 409
    assert client.post(f"/api/notices/{nid}/approve", json={"approver": "A"}).status_code == 422   # a named person
    assert client.post(f"/api/notices/{nid}/approve", json={"approver": "Ama Mensah"}).json()["status"] == "approved"
    assert client.post(f"/api/notices/{nid}/approve", json={"approver": "Ama Mensah"}).status_code == 409
    f = client.post(f"/api/notices/{nid}/file").json()
    assert f["status"] == "filed_dry_run" and f["approved_by"] == "Ama Mensah"
    assert client.post(f"/api/notices/{nid}/reject", json={"approver": "Ama Mensah", "reason": "too late"}).status_code == 409
    clock = client.post(f"/api/notices/{nid}/clock", json={"event": "first_response"}).json()
    assert [e["event"] for e in clock] == ["filed", "first_response"] and all(e["simulated"] for e in clock)
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert {"notice_approved", "notice_filed_dry_run", "clock_first_response"} <= set(actions)


def test_notices_inbox_filters_and_scopes_by_brand(client):
    seed_notice()
    other = client.post("/api/brands", json={"display_name": "Other Co", "website": "other.test"}).json()["id"]
    assert len(client.get("/api/notices").json()) == 1
    assert len(client.get("/api/notices", params={"status": "drafted"}).json()) == 1
    assert client.get("/api/notices", params={"status": "filed_dry_run"}).json() == []
    assert client.get("/api/notices", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/notices", headers={"X-Brand-Id": other}).json() == []           # another brand sees nothing
    assert client.get("/api/stats", headers={"X-Brand-Id": other}).json()["counts"]["threats"] == 0


def test_brand_onboarding_verification_and_review(client, monkeypatch):
    assert client.post("/api/brands", json={"display_name": "No Source"}).status_code == 422
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "https://acme.test", "linkedin_url": "https://linkedin.com/company/acme-pay"}).json()
    v = b["verification"]
    assert v["record_name"] == "_haul16-verify.acme.test" and v["record_value"].startswith("haul16-verify=") and b["ownership_status"] == "unverified"

    j1 = client.post(f"/api/brands/{b['id']}/profile/run").json()
    assert client.get(f"/api/brands/{b['id']}").json()["status"] == "profiling"           # visible to the UI straight away
    assert j1["status"] == "queued" and client.post(f"/api/brands/{b['id']}/profile/run").json()["id"] == j1["id"]   # no duplicate jobs

    async def no_dns(name):
        return []

    async def with_token(name):
        return [v["record_value"]] if name.startswith("_haul16-verify.") else []

    monkeypatch.setattr(dnsx, "txt_values", no_dns)
    r = client.post(f"/api/brands/{b['id']}/verify").json()
    assert r["verified"] is False and "No TXT record" in r["reason"]
    monkeypatch.setattr(dnsx, "txt_values", with_token)
    assert client.post(f"/api/brands/{b['id']}/verify").json()["verified"] is True
    assert client.get(f"/api/brands/{b['id']}").json()["ownership_status"] == "verified"

    repo = BrandRepo(api.S.store)
    f = repo.upsert_fact(b["id"], "phone", "0241112223", collector="website", origin="official", source_url="https://acme.test/contact")
    prof = client.get(f"/api/brands/{b['id']}/profile").json()
    assert f["id"] in prof["needs_review"] and prof["groups"]["phone"][0]["source_url"] == "https://acme.test/contact"
    assert client.post(f"/api/brands/{b['id']}/facts/{f['id']}/confirm").json() == {"ok": True}
    assert client.post(f"/api/brands/{b['id']}/facts/nope/confirm").status_code == 404
    assert client.post(f"/api/brands/{b['id']}/facts/{f['id']}/delete").status_code == 404


def test_letter_upload_stores_hash(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acme.test"}).json()
    import base64, hashlib
    data = b"%PDF-1.4 signed authorisation"
    r = client.post(f"/api/brands/{b['id']}/letter", json={"filename": "letter.pdf", "content_base64": base64.b64encode(data).decode()}).json()
    assert r["sha256"] == hashlib.sha256(data).hexdigest()
    assert client.get(f"/api/brands/{b['id']}").json()["authorisation_letter_sha256"] == r["sha256"]
    assert client.post(f"/api/brands/{b['id']}/letter", json={"filename": "x", "content_base64": "!!!!"}).status_code == 422


def test_activate_switches_the_detection_brand(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acme.test"}).json()
    assert client.post(f"/api/brands/{b['id']}/activate").status_code == 409                # profile not built yet
    BrandRepo(api.S.store).update(b["id"], status="ready")
    assert client.post(f"/api/brands/{b['id']}/activate").json() == {"active": b["id"]}
    assert client.get("/api/brand").json()["display_name"] == "Acme Pay"
    assert api.S.watch.matcher.brand.brand_id == b["id"] and api.S.pipe.matcher is api.S.watch.matcher
    assert [x["id"] for x in client.get("/api/brands").json() if x["is_active"]] == [b["id"]]


def test_demo_routes_are_off_in_production(client, monkeypatch):
    monkeypatch.setattr(api.SETTINGS, "haul16_env", "prod")
    assert client.post("/api/demo/clone", json={"variant": "clone"}).status_code == 404
    assert client.post("/api/demo/reset").status_code == 404
