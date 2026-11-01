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
    act = client.post(f"/api/brands/{b['id']}/activate").json()
    assert act["active"] == b["id"] and act["sweep"]["status"] == "running"                   # protection starts a look-alike scan
    assert client.get("/api/brand").json()["display_name"] == "Acme Pay"
    assert api.S.watch.matcher.brand.brand_id == b["id"] and api.S.pipe.matcher is api.S.watch.matcher
    assert [x["id"] for x in client.get("/api/brands").json() if x["is_active"]] == [b["id"]]


def test_demo_routes_are_off_in_production(client, monkeypatch):
    monkeypatch.setattr(api.SETTINGS, "haul16_env", "prod")
    assert client.post("/api/demo/clone", json={"variant": "clone"}).status_code == 404
    assert client.post("/api/demo/reset").status_code == 404


def test_starts_with_no_brand_and_matches_nothing(db_url, monkeypatch):
    from engine.store import Store
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    s.db.close()
    monkeypatch.setattr(api.SETTINGS, "seed_demo_brand", False)
    from engine.settings import get_settings
    monkeypatch.setenv("SEED_DEMO_BRAND", "false")
    get_settings.cache_clear()
    with TestClient(api.app) as c:
        assert c.get("/api/brands").json() == []
        assert c.get("/api/stats").json()["counts"]["threats"] == 0
        assert api.S.watch.matcher.match("nkwaex-support.com") is None          # nothing is watched until a brand is activated
    get_settings.cache_clear()


def test_filing_is_locked_until_ownership_and_letter(client):
    nid, case_id = seed_notice()
    client.post(f"/api/notices/{nid}/approve", json={"approver": "Ama Mensah"})
    repo = BrandRepo(api.S.store)
    repo.update("brand_demo", ownership_status="unverified")
    r = client.post(f"/api/notices/{nid}/file")
    assert r.status_code == 409 and "control the brand's domain" in r.json()["detail"]
    repo.update("brand_demo", ownership_status="verified", authorisation_letter_sha256=None)
    r = client.post(f"/api/notices/{nid}/file")
    assert r.status_code == 409 and "authorisation letter" in r.json()["detail"]
    repo.update("brand_demo", authorisation_letter_sha256="a" * 64)
    assert client.post(f"/api/notices/{nid}/file").json()["status"] == "filed_dry_run"


def test_delete_brand_removes_everything_for_it(client):
    seed_notice()
    b = client.post("/api/brands", json={"display_name": "Temp Co", "website": "temp.test"}).json()
    repo = BrandRepo(api.S.store)
    repo.upsert_fact(b["id"], "email", "a@temp.test", collector="website", origin="official")
    assert client.delete(f"/api/brands/{b['id']}").json() == {"deleted": b["id"]}
    assert client.get(f"/api/brands/{b['id']}").status_code == 404
    assert repo.facts(b["id"]) == []
    assert client.delete(f"/api/brands/{b['id']}").status_code == 404
    assert len(client.get("/api/notices").json()) == 1                      # the other brand's data is untouched


def test_deleting_the_watched_brand_stops_detection(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()
    BrandRepo(api.S.store).update(b["id"], status="ready")
    client.post(f"/api/brands/{b['id']}/activate")
    assert api.S.watch.brand.brand_id == b["id"]
    client.delete(f"/api/brands/{b['id']}")
    assert api.S.watch.brand.brand_id == "__none__" and api.S.watch.matcher.match("acmepay-login.com") is None


def test_setup_state_walks_the_user_through_the_next_step(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()
    bid = b["id"]
    s = client.get(f"/api/brands/{bid}/setup").json()
    assert s["next_action"]["key"] == "wait_profile" and s["step"] == 2 and not s["complete"]
    repo = BrandRepo(api.S.store)
    repo.update(bid, status="ready")
    f = repo.upsert_fact(bid, "phone", "0241112223", collector="website", origin="official")
    s = client.get(f"/api/brands/{bid}/setup").json()
    assert s["next_action"]["key"] == "review_facts" and s["next_action"]["count"] == 1 and s["steps"]["review"] == {"done": False, "count": 1}
    client.post(f"/api/brands/{bid}/facts/{f['id']}/confirm")
    assert client.get(f"/api/brands/{bid}/setup").json()["next_action"]["key"] == "start_protection"
    client.post(f"/api/brands/{bid}/activate")
    s = client.get(f"/api/brands/{bid}/setup").json()
    assert s["complete"] and s["step"] == 3 and s["next_action"]["key"] == "prove_ownership"    # protecting, but filing still locked
    repo.update(bid, ownership_status="verified", authorisation_letter_sha256="b" * 64)
    s = client.get(f"/api/brands/{bid}/setup").json()
    assert s["next_action"]["key"] == "all_clear" and s["step"] == 4 and s["sweep"]["status"] == "running"


def test_sweep_route_needs_protection_first(client):
    bid = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()["id"]
    assert client.post(f"/api/brands/{bid}/sweep").status_code == 409          # profile not built
    BrandRepo(api.S.store).update(bid, status="ready")
    r = client.post(f"/api/brands/{bid}/sweep")
    assert r.status_code == 409 and "Start protection" in r.json()["detail"]
    client.post(f"/api/brands/{bid}/activate")
    assert client.get(f"/api/brands/{bid}/sweep/status").json()["status"] == "running"
    assert client.post(f"/api/brands/{bid}/sweep").json()["status"] == "running"   # no duplicate job


def test_edit_watch_terms_updates_live_detection(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()
    BrandRepo(api.S.store).update(b["id"], status="ready")
    client.post(f"/api/brands/{b['id']}/activate")
    assert api.S.watch.matcher.match("zorbex-login.com") is None
    r = client.patch(f"/api/brands/{b['id']}", json={"seed_terms": ["Zorbex!", "ab", "zorbex"]}).json()
    assert r["seed_terms"] == ["zorbex"]                                       # cleaned, short and duplicate terms dropped
    assert api.S.watch.matcher.match("zorbex-login.com") is not None           # live matcher updated without a restart
    assert client.patch(f"/api/brands/{b['id']}", json={"seed_terms": ["ab"]}).status_code == 422


def test_next_action_puts_waiting_notices_ahead_of_housekeeping_once_protecting(client):
    b = client.post("/api/brands", json={"display_name": "Acme Pay", "website": "acmepay.test"}).json()
    bid = b["id"]
    repo = BrandRepo(api.S.store)
    repo.update(bid, status="ready")
    repo.upsert_fact(bid, "phone", "0241112223", collector="website", origin="official")           # an unconfirmed detail
    assert client.get(f"/api/brands/{bid}/setup").json()["next_action"]["key"] == "review_facts"     # before protection: finish the details
    client.post(f"/api/brands/{bid}/activate")
    assert client.get(f"/api/brands/{bid}/setup").json()["next_action"]["key"] == "review_facts"
    tid = api.S.store.add_threat(bid, "acmepay-login.test", None, "manual", None, {"reasons": []})
    case_id, _ = api.S.store.assign_case(tid, bid, "Suspected clone")
    api.S.store.add_notices(case_id, [{"channel": "csa", "channel_name": "CSA", "method": "web_form", "recipient": "x", "recipient_verified": False,
                                       "subject": "s", "body": "b", "evidence_refs": {}}], tid)
    nxt = client.get(f"/api/brands/{bid}/setup").json()["next_action"]
    assert nxt["key"] == "review_notices" and nxt["count"] == 1                                       # the notice outranks the pending detail
