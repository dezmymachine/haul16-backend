"""API integration tests: endpoints, human-approval state machine, and case dossier."""
import os
import pytest
from fastapi.testclient import TestClient

# Ensure CT polling is off during test runs
os.environ["HAUL16_CT"] = "0"

from engine import api
from engine.store import Store, new_id


@pytest.fixture
def client(tmp_path):
    # Use an isolated test database
    test_db = tmp_path / "api_test.db"
    orig_db_path = api.DB_PATH
    api.DB_PATH = test_db
    with TestClient(api.app) as c:
        yield c
    api.DB_PATH = orig_db_path


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["dry_run"] is True


def test_brand(client):
    res = client.get("/api/brand")
    assert res.status_code == 200
    data = res.json()
    assert data["brand_id"] in ("yellowcard_gh", "brand_demo")
    assert data["display_name"] in ("Yellow Card", "Nkwa Exchange")
    assert "authorisation_letter" in data
    assert len(data["authorisation_letter"]["sha256"]) == 64


def test_stats(client):
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    assert "counts" in data
    assert data["dry_run"] is True


def test_notice_approval_state_machine(client):
    store = api.S.store
    brand_id = api.S.watch.brand.brand_id

    # Create test threat and case
    tid = store.add_threat(brand_id, "nkwaex-fraud.com", "https://nkwaex-fraud.com", "manual", None, None)
    case_id, _ = store.assign_case(tid, brand_id, "Test Scam Case")

    # Add a draft notice
    draft = [{
        "channel": "telco_mtn",
        "channel_name": "MTN Ghana MoMo fraud team",
        "method": "email",
        "recipient": "mmfraudteam.GH@mtn.com",
        "recipient_verified": True,
        "subject": "Test MoMo Fraud",
        "body": "Fraud notice body",
        "evidence_refs": {"threat_id": tid, "html_sha256": "abc123sha"},
    }]
    store.add_notices(case_id, draft, tid)
    notices = store.notices(case_id)
    assert len(notices) == 1
    nid = notices[0]["id"]
    assert notices[0]["status"] == "drafted"

    # Invariant: Cannot file an unapproved draft (409 Conflict)
    file_attempt = client.post(f"/api/notices/{nid}/file")
    assert file_attempt.status_code == 409
    assert "approve this notice before it is filed" in file_attempt.json()["detail"]

    # Approver validation: short/empty names rejected (422)
    bad_approve = client.post(f"/api/notices/{nid}/approve", json={"approver": " "})
    assert bad_approve.status_code == 422

    # Legitimate approval
    ok_approve = client.post(f"/api/notices/{nid}/approve", json={"approver": "Kofi Mensah"})
    assert ok_approve.status_code == 200
    assert ok_approve.json()["status"] == "approved"
    assert ok_approve.json()["approved_by"] == "Kofi Mensah"

    # Double approval rejected (409)
    re_approve = client.post(f"/api/notices/{nid}/approve", json={"approver": "Ama Mensah"})
    assert re_approve.status_code == 409

    # File notice (dry run)
    filed = client.post(f"/api/notices/{nid}/file")
    assert filed.status_code == 200
    assert filed.json()["status"] == "filed_dry_run"

    # Log takedown clock event
    clock_res = client.post(f"/api/notices/{nid}/clock", json={"event": "removed", "note": "Wallet suspended"})
    assert clock_res.status_code == 200
    assert len(clock_res.json()) >= 1


def test_case_dossier_endpoint(client):
    store = api.S.store
    brand_id = api.S.watch.brand.brand_id

    tid = store.add_threat(brand_id, "nkwaex-dossier-test.com", "https://nkwaex-dossier-test.com", "manual", None, None)
    case_id, _ = store.assign_case(tid, brand_id, "Regulatory Dossier Test Case")

    # Fetch dossier
    res = client.get(f"/api/cases/{case_id}/dossier")
    assert res.status_code == 200
    dossier = res.json()

    assert dossier["dossier_id"] == f"HAUL16-DOSSIER-{case_id.upper()}"
    assert len(dossier["manifest_sha256"]) == 64
    assert dossier["legal_basis"]["jurisdiction"] == "Ghana"
    assert any("Act 772" in s for s in dossier["legal_basis"]["statutes"])
    assert dossier["case"]["id"] == case_id
    assert len(dossier["threats"]) == 1
    assert dossier["threats"][0]["hostname"] == "nkwaex-dossier-test.com"


def test_report_endpoint(client):
    res = client.get("/api/report")
    assert res.status_code == 200
    data = res.json()
    assert data["dry_run"] is True
    assert "threats_detected" in data
    assert "cases_opened" in data
    assert "notices" in data


def test_evidence_path_traversal_blocked(client):
    # Invalid filename format
    res = client.get("/api/evidence/../../etc/passwd")
    assert res.status_code == 404 or res.status_code == 400


def test_telegram_and_whatsapp_channels(client):
    from engine import notices
    brand = api.S.watch.brand
    brand_cfg = api.S.watch.brand_cfg

    threat = {
        "id": "thr_social_test",
        "hostname": "nkwa-promo-scam.xyz",
        "url": "https://nkwa-promo-scam.xyz",
        "detected_at": "2026-10-30T12:00:00Z",
        "capture": {
            "captured_at": "2026-10-30T12:01:00Z",
            "html_sha256": "def456sha",
            "screenshot_sha256": "789shotsha",
            "identifiers": {
                "phones": ["0240000101"],
                "telegram": ["nkwa_fake_vip", "nkwaex"],
                "whatsapp": ["0240000101"],
            },
        },
        "verdict": {
            "confidence": "high",
            "score": 85,
            "reason_codes": ["brand_named_on_page", "payment_identifier_not_official"],
            "evidence": {"payment_identifier_not_official": {"phones": ["0240000101"]}},
        },
        "enrichment": {},
    }

    drafts = notices.draft(threat, brand, brand_cfg, "case_social_001")
    channels = {d["channel"] for d in drafts}
    assert "telegram_abuse" in channels
    assert "meta_whatsapp" in channels

    tg_notice = next(d for d in drafts if d["channel"] == "telegram_abuse")
    assert tg_notice["recipient"] == "abuse@telegram.org"
    assert "nkwa_fake_vip" in tg_notice["subject"]
    assert "nkwaex" not in tg_notice["subject"]

    wa_notice = next(d for d in drafts if d["channel"] == "meta_whatsapp")
    assert wa_notice["recipient"] == "phishing@support.whatsapp.com"
    assert "0240000101" in wa_notice["subject"]


def test_demo_seed_endpoint(client):
    res = client.post("/api/demo/seed")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["threats"] == 5
    assert data["cases"] == 2

    # Check stats immediately after seed
    stats_res = client.get("/api/stats")
    assert stats_res.status_code == 200
    counts = stats_res.json()["counts"]
    assert counts["threats"] == 5
    assert counts["open_cases"] == 2


