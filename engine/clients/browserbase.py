"""Browserbase: a remote, managed Chromium that Playwright drives over CDP. Used by engine/capture.py for suspect pages.

No keys -> NotConfigured and the capture falls back to a local Chromium.
Docs: https://docs.browserbase.com/reference/api/create-a-session
"""
import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings

API = "https://api.browserbase.com/v1/sessions"


def configured() -> bool:
    s = get_settings()
    return bool(s.browserbase_api_key)          # the project id is optional: Browserbase infers it from the key


async def create_session(client: httpx.AsyncClient) -> tuple[str, str]:
    """Start a remote browser session. Returns (session_id, connect_url); connect_url is a websocket for connect_over_cdp."""
    s = get_settings()
    if not configured():
        raise NotConfigured("BROWSERBASE_API_KEY is not set")
    body: dict = {"timeout": 180, "browserSettings": {"ignoreCertificateErrors": True}}   # scam sites often have broken certificates
    if s.browserbase_project_id:
        body["projectId"] = s.browserbase_project_id
    if s.browserbase_proxy:
        body["proxies"] = [{"type": "browserbase", "geolocation": {"country": s.browserbase_country}}]
    res = await request(client, "POST", API, headers={"X-BB-API-Key": s.browserbase_api_key}, json=body, timeout=30, retries=1)
    if res.status_code >= 400:
        raise UpstreamError(f"browserbase {res.status_code}: {res.text[:200]}")
    data = res.json()
    if not data.get("id") or not data.get("connectUrl"):
        raise UpstreamError("browserbase: session response had no id or connectUrl")
    return data["id"], data["connectUrl"]


def replay_url(session_id: str) -> str:
    return f"https://browserbase.com/sessions/{session_id}"
