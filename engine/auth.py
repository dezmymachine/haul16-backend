"""Simple sign-in for one owner account.

Auth is on whenever AUTH_OWNER_PASSWORD is set. A successful login sets an HMAC-signed, HttpOnly, SameSite=Lax cookie. A pure ASGI
middleware (so the SSE stream is not buffered) answers 401 for every /api route except /api/health and /api/auth/*. The signed-in name is
what gets recorded as the approver, taken from the session and never from the request body.
"""
import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.responses import JSONResponse

from engine.settings import get_settings

COOKIE = "haul16_session"
TTL_S = 7 * 24 * 3600
router = APIRouter(prefix="/api/auth")
_attempts: dict[str, deque] = defaultdict(deque)


def enabled() -> bool:
    return bool(get_settings().auth_owner_password)


def _secret() -> bytes:
    s = get_settings()
    return (s.session_secret or hashlib.sha256(f"haul16-dev:{s.auth_owner_password}".encode()).hexdigest()).encode()


def owner_name() -> str:
    s = get_settings()
    return s.auth_owner_name or s.auth_owner_email.split("@")[0].replace(".", " ").replace("_", " ").title()


def make_token(email: str, ttl: int = TTL_S) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"e": email, "x": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str | None) -> str | None:
    """The signed-in email, or None when the cookie is missing, forged or expired."""
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    return data["e"] if data.get("x", 0) > time.time() else None


def _cookie_from(headers: list[tuple[bytes, bytes]]) -> str | None:
    for k, v in headers:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
    return None


class AuthMiddleware:
    """401 for /api/* without a valid session. Exempt: /api/health, /api/auth/*, CORS preflight."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not enabled():
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not path.startswith("/api/") or path == "/api/health" or path.startswith("/api/auth/") or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        email = read_token(_cookie_from(scope.get("headers", [])))
        if not email:
            return await JSONResponse({"detail": "Sign in to continue"}, status_code=401)(scope, receive, send)
        scope.setdefault("state", {})["user"] = email
        return await self.app(scope, receive, send)


def current_user_name(request: Request) -> str | None:
    """The signed-in person's name, or None when auth is off."""
    if not enabled():
        return None
    return owner_name() if getattr(request.state, "user", None) else None


class Login(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    s = get_settings()
    if not enabled():
        raise HTTPException(409, "Sign-in is not configured on this server")
    ip = request.client.host if request.client else "?"
    q, now = _attempts[ip], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 5:
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    q.append(now)
    ok_email = hmac.compare_digest(body.email.strip().lower().encode(), s.auth_owner_email.lower().encode())
    ok_pw = hmac.compare_digest(body.password.encode(), (s.auth_owner_password or "").encode())
    if not (ok_email and ok_pw):
        raise HTTPException(401, "Wrong email or password")
    q.clear()
    response.set_cookie(COOKIE, make_token(s.auth_owner_email), max_age=TTL_S, httponly=True, samesite="lax", secure=s.is_prod, path="/")
    return {"email": s.auth_owner_email, "name": owner_name()}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    if not enabled():
        return {"email": "local", "name": "Local user", "auth_enabled": False}
    email = read_token(request.cookies.get(COOKIE))
    if not email:
        raise HTTPException(401, "Sign in to continue")
    return {"email": email, "name": owner_name(), "auth_enabled": True}
