"""Sign-in: cookie session, 401 on the API without one, approver taken from the session, production safety."""
import pytest
from fastapi.testclient import TestClient

from engine import api
from engine.settings import get_settings
from tests.conftest import TABLES

CREDS = {"email": "owner@haul16.local", "password": "correct horse"}


@pytest.fixture()
def secured(db_url, monkeypatch):
    from engine import auth
    from engine.store import Store
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    s.db.close()
    monkeypatch.setenv("AUTH_OWNER_PASSWORD", CREDS["password"])
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    get_settings.cache_clear()
    auth._attempts.clear()
    with TestClient(api.app) as c:
        yield c
    get_settings.cache_clear()


def test_api_is_closed_until_you_sign_in_but_health_is_open(secured):
    assert secured.get("/api/health").status_code == 200
    for path in ("/api/brands", "/api/stats", "/api/threats", "/api/notices", "/api/auth/me"):
        assert secured.get(path).status_code == 401, path
    assert secured.post("/api/notices/x/approve", json={}).status_code == 401


def test_login_logout_and_me(secured):
    assert secured.post("/api/auth/login", json={**CREDS, "password": "nope"}).status_code == 401
    assert secured.post("/api/auth/login", json={**CREDS, "email": "someone@else.test"}).status_code == 401
    r = secured.post("/api/auth/login", json=CREDS)
    assert r.status_code == 200 and r.json()["name"] == "Owner"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert secured.get("/api/auth/me").json()["email"] == CREDS["email"]
    assert secured.get("/api/brands").status_code == 200
    secured.post("/api/auth/logout")
    assert secured.get("/api/brands").status_code == 401


def test_forged_or_expired_cookie_is_rejected(secured):
    from engine import auth
    secured.cookies.set(auth.COOKIE, "eyJlIjoib3duZXIiLCJ4Ijo5OTk5OTk5OTk5fQ.deadbeef")
    assert secured.get("/api/brands").status_code == 401
    secured.cookies.set(auth.COOKIE, auth.make_token(CREDS["email"], ttl=-10))
    assert secured.get("/api/brands").status_code == 401


def test_login_is_rate_limited(secured):
    for _ in range(5):
        assert secured.post("/api/auth/login", json={**CREDS, "password": "bad"}).status_code == 401
    assert secured.post("/api/auth/login", json=CREDS).status_code == 429


def test_approver_comes_from_the_session_not_the_request(secured):
    from tests.test_api import seed_notice
    secured.post("/api/auth/login", json=CREDS)
    nid, _ = seed_notice()
    r = secured.post(f"/api/notices/{nid}/approve", json={"approver": "Mallory Pretender"}).json()
    assert r["approved_by"] == "Owner"                      # the body name is ignored
    assert secured.post(f"/api/notices/{nid}/approve", json={}).status_code == 409   # no name needed any more


def test_production_refuses_to_start_without_sign_in(db_url, monkeypatch):
    monkeypatch.setenv("HAUL16_ENV", "prod")
    monkeypatch.setenv("AUTH_OWNER_PASSWORD", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="AUTH_OWNER_PASSWORD"):
            with TestClient(api.app):
                pass
    finally:
        get_settings.cache_clear()
