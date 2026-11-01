"""HAUL16 API: the watch loop, the case desk and the report behind one FastAPI app.

  uv run uvicorn engine.api:app --port 8000
  HAUL16_CT=0 uv run uvicorn engine.api:app --port 8000     # without live CT polling

DRY RUN: nothing is ever sent to a registrar, host, platform, telco or regulator.
"""
import asyncio
import json
import os
import re
import statistics
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import auth, ct_poll, jobs, notices
from engine.brand import routes as brand_routes
from engine.brand.import_yaml import import_yaml
from engine.brand.loader import NO_BRAND, brand_from_db
from engine.brand.repo import BrandRepo
from engine.worker_main import handlers as job_handlers
from engine.pipeline import Pipeline
from engine.settings import get_settings
from engine.store import Store
from engine.worker import Watch

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data" / "evidence"
SETTINGS = get_settings()
PORT = SETTINGS.haul16_port
CT_ENABLED = SETTINGS.haul16_ct
DEMO_HOSTS = {"clone": "nkwaex-support.com", "clone2": "nkwaex-airdrop.top", "decoy": "nkwafarms.com"}


class State:
    watch: Watch
    store: Store
    pipe: Pipeline
    lag: dict
    started: float
    ct_status: str = "off"


S = State()


async def ct_loop() -> None:
    """Follow every CT log; restart a log's follower if it dies."""
    limits = httpx.Limits(max_connections=ct_poll.PARALLEL * len(ct_poll.LOGS))
    async with httpx.AsyncClient(timeout=20, limits=limits,
                                 headers={"User-Agent": "haul16-ct-monitor/0.1"}) as client:
        async def keep(name, url):
            while True:
                try:
                    S.ct_status = "live"
                    await ct_poll.follow(client, name, url, S.pipe, None, S.lag)
                except Exception as e:
                    print(f"ct {name} restarting after {e!r}", flush=True)
                    await asyncio.sleep(5)
        await asyncio.gather(*(keep(n, u) for n, u in ct_poll.LOGS.items()))


def ct_snapshot() -> dict:
    s = S.pipe.stats
    el = max(time.monotonic() - S.started, 1e-9)
    return {"status": S.ct_status, "logs": len(ct_poll.LOGS), "certs": s["certs"],
            "unique_domains": s["unique_domains"], "hits": s["hits"],
            "certs_per_s": round(s["certs"] / el, 1), "uptime_s": round(el), "backlog": dict(S.lag)}


async def stats_ticker() -> None:
    last = 0
    while True:
        await asyncio.sleep(2)
        snap = ct_snapshot()
        snap["rate_now"] = round((snap["certs"] - last) / 2, 1)
        last = snap["certs"]
        S.watch.publish({"type": "ct_stats", **snap})


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.is_prod and not (s.auth_owner_password and s.session_secret):
        raise RuntimeError("Production needs AUTH_OWNER_PASSWORD and SESSION_SECRET (sign-in must be on)")
    S.store = Store()
    repo = BrandRepo(S.store)
    if s.seed_demo_brand:
        import_yaml(S.store)                       # the fictional demo brand, development only (idempotent)
    row = S.store.db.execute("SELECT id FROM brands WHERE is_active LIMIT 1").fetchone()
    brand, cfg = brand_from_db(repo, row["id"]) if row else (NO_BRAND, {})   # no brand yet: nothing matches until one is activated
    S.watch = Watch(brand, S.store, brand_cfg=cfg, workers=2)
    S.pipe = Pipeline(S.watch.matcher, ROOT / "data" / "hits.jsonl", on_hit=S.watch.on_hit)
    S.lag, S.started = {}, time.monotonic()
    S.watch.recover()          # threats left 'queued' by a restart go back on the capture queue
    brand_routes.bind(S)
    tasks = [asyncio.create_task(S.watch.capture_worker()) for _ in range(S.watch.workers)]
    tasks.append(asyncio.create_task(stats_ticker()))
    if SETTINGS.embedded_worker:
        tasks.append(asyncio.create_task(jobs.run_worker(S.store, job_handlers(S.store, S.watch))))
    if CT_ENABLED:
        tasks.append(asyncio.create_task(ct_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="HAUL16", lifespan=lifespan)
app.add_middleware(auth.AuthMiddleware)       # added first so CORS (added next) wraps it and 401s still carry CORS headers
app.add_middleware(CORSMiddleware, allow_origins=SETTINGS.origins,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(brand_routes.router)
if not SETTINGS.is_prod:                          # demo fixtures and demo routes are development-only
    app.mount("/demo-sites", StaticFiles(directory=ROOT / "fixtures" / "sites", html=True), name="demo-sites")


def bid(brand_id: str | None = None, x_brand_id: str | None = Header(default=None)) -> str:
    """The brand a request is about: ?brand_id=, else the X-Brand-Id header, else the brand the pipeline is watching."""
    return brand_id or x_brand_id or S.watch.brand.brand_id


# ---- brand, stats, feed -----------------------------------------------------

@app.get("/api/health")
def health():
    """Liveness plus what is configured. Reports which collectors have keys, never the keys."""
    try:
        S.store.db.execute("SELECT 1")
        db_ok = True
        queued = S.store.db.execute("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')").fetchone()["n"]
    except Exception:  # noqa: BLE001
        db_ok, queued = False, None
    return {"ok": db_ok, "dry_run": SETTINGS.haul16_dry_run, "ct_enabled": CT_ENABLED, "env": SETTINGS.haul16_env, "db": db_ok,
            "jobs_pending": queued, "embedded_worker": SETTINGS.embedded_worker,
            "collectors": {"llm": bool(SETTINGS.openai_api_key if SETTINGS.llm_provider == "openai" else SETTINGS.anthropic_api_key),
                           "firecrawl": bool(SETTINGS.firecrawl_api_key), "apify": bool(SETTINGS.apify_token and SETTINGS.apify_linkedin_actor),
                           "search": bool(SETTINGS.search_api_key), "urlscan": bool(SETTINGS.urlscan_api_key)}}


@app.get("/api/brand")
def brand(b: str = Depends(bid)):
    cfg = dict(S.watch.brand_cfg) if b == S.watch.brand.brand_id else brand_from_db(BrandRepo(S.store), b)[1]
    ref, sha = notices.letter(cfg)
    cfg["authorisation_letter"] = {"ref": ref, "sha256": sha}
    return cfg


@app.get("/api/stats")
def stats(b: str = Depends(bid)):
    return {"counts": S.store.counts(b), "ct": ct_snapshot(),
            "queue": S.watch.queue.qsize(), "dry_run": True}


@app.get("/api/threats")
def threats(confidence: str | None = None, limit: int = 200, b: str = Depends(bid)):
    rows = S.store.threats(b, limit=limit, confidence=confidence)
    for r in rows:                                   # keep the list light
        cap = r.pop("capture", None) or {}
        r["screenshot_ref"] = cap.get("screenshot_ref")
        r["title"] = cap.get("title")
        r.pop("enrichment", None)
    return rows


@app.get("/api/threats/{tid}")
def threat(tid: str):
    t = S.store.threat(tid)
    if not t:
        raise HTTPException(404, "No such threat")
    return t


@app.get("/api/cases")
def cases(b: str = Depends(bid)):
    return S.store.cases(b)


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    c = S.store.case(case_id)
    if not c:
        raise HTTPException(404, "No such case")
    return c


@app.post("/api/cases/{case_id}/redraft")
def redraft(case_id: str):
    c = S.store.case(case_id)
    if not c:
        raise HTTPException(404, "No such case")
    n = 0
    for t in c["threats"]:
        n += S.store.add_notices(case_id, notices.draft(t, S.watch.brand, S.watch.brand_cfg, case_id), t["id"])
    return {"drafted": n}


@app.get("/api/audit")
def audit(limit: int = 100):
    return [S.store._row(r) for r in S.store.db.execute(
        "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,))]


# ---- notices: human approval is enforced here ---------------------------------

@app.get("/api/notices")
def list_notices(status: str | None = None, b: str = Depends(bid)):
    if status and status not in ("drafted", "approved", "filed_dry_run", "rejected"):
        raise HTTPException(422, "Unknown notice status")
    return S.store.all_notices(b, status)


class Approve(BaseModel):
    approver: str | None = Field(default=None, max_length=80)     # ignored when signed in: the session name is used


class Reject(Approve):
    reason: str = Field(min_length=3, max_length=500)


class ClockIn(BaseModel):
    event: str = Field(pattern="^(first_response|removed)$")
    note: str | None = Field(default=None, max_length=500)


def _notice(nid: str) -> dict:
    n = S.store.notice(nid)
    if not n:
        raise HTTPException(404, "No such notice")
    return n


def _who(request: Request, body: Approve) -> str:
    """The approver is the signed-in person. Only when sign-in is off does the request name them."""
    who = auth.current_user_name(request) or (body.approver or "").strip()
    if len(who) < 2:
        raise HTTPException(422, "Enter your name so the decision can be recorded against you")
    return who


@app.post("/api/notices/{nid}/approve")
def approve(nid: str, body: Approve, request: Request):
    n = _notice(nid)
    who = _who(request, body)
    if n["status"] != "drafted":
        raise HTTPException(409, f"Notice is {n['status']}, only drafts can be approved")
    S.store.approve_notice(nid, who)
    return S.store.notice(nid)


@app.post("/api/notices/{nid}/reject")
def reject(nid: str, body: Reject, request: Request):
    n = _notice(nid)
    who = _who(request, body)
    if n["status"] not in ("drafted", "approved"):
        raise HTTPException(409, f"Notice is {n['status']}")
    S.store.reject_notice(nid, who, body.reason)
    return S.store.notice(nid)


@app.post("/api/notices/{nid}/file")
def file_notice(nid: str):
    n = _notice(nid)
    if n["status"] != "approved":
        raise HTTPException(409, "A person must approve this notice before it is filed")
    brand = BrandRepo(S.store).get(S.store.db.execute("SELECT brand_id FROM cases WHERE id=?", (n["case_id"],)).fetchone()["brand_id"])
    if not brand or brand["ownership_status"] != "verified":
        raise HTTPException(409, "Prove you control the brand's domain before filing (Brand, then Verify and letter)")
    if not brand["authorisation_letter_sha256"]:
        raise HTTPException(409, "Upload the signed authorisation letter before filing (Brand, then Verify and letter)")
    S.store.file_notice(nid, n["approved_by"])
    S.watch.publish({"type": "filed", "notice_id": nid, "case_id": n["case_id"], "channel": n["channel"]})
    return S.store.notice(nid)


@app.post("/api/notices/{nid}/clock")
def clock(nid: str, body: ClockIn):
    n = _notice(nid)
    if n["status"] != "filed_dry_run":
        raise HTTPException(409, "File the notice before logging a response")
    S.store.clock_event(n["case_id"], nid, n["channel"], body.event, body.note, simulated=True)
    return S.store.clock(n["case_id"])


# ---- intake -----------------------------------------------------------------

class InjectIn(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=2000)
    as_host: str | None = Field(default=None, max_length=253)


@app.post("/api/inject")
def inject(body: InjectIn):
    tid = S.watch.inject(body.url, body.as_host, source="manual", allow_local=False)
    return {"threat_id": tid, "duplicate": tid is None}


class DemoIn(BaseModel):
    variant: str = Field(pattern="^(clone|clone2|decoy)$")


@app.post("/api/demo/clone")
def demo_clone(body: DemoIn):
    if SETTINGS.is_prod:
        raise HTTPException(404, "Demo routes are disabled")
    """Push a fictional fixture site through the real pipeline (served by this API)."""
    host = DEMO_HOSTS[body.variant]
    url = f"http://127.0.0.1:{PORT}/demo-sites/{body.variant}/"
    tid = S.watch.inject(url, host, source="demo_fixture", allow_local=True)
    if tid is None:
        existing = S.store.db.execute("SELECT id FROM threats WHERE hostname=?", (host,)).fetchone()
        return {"threat_id": existing[0] if existing else None, "duplicate": True, "host": host}
    return {"threat_id": tid, "duplicate": False, "host": host}


@app.post("/api/demo/reset")
def demo_reset():
    if SETTINGS.is_prod:
        raise HTTPException(404, "Demo routes are disabled")
    """Wipe cases, threats and notices (demo rehearsal only)."""
    for t in ("threats", "cases", "identifiers", "notices", "clock_events", "audit_log"):
        S.store.db.execute(f"DELETE FROM {t}")
    S.store.db.commit()
    S.watch.attempts.clear()
    S.store.audit("operator", "demo_reset")
    return {"ok": True}


# ---- evidence ---------------------------------------------------------------

@app.get("/api/evidence/{name}")
def evidence(name: str):
    if not re.fullmatch(r"[0-9A-Za-z+_.-]{10,120}\.(png|html)", name):
        raise HTTPException(400, "Bad evidence name")
    p = EVIDENCE / name
    if not p.exists():
        raise HTTPException(404, "No such evidence file")
    media = "image/png" if name.endswith(".png") else "text/plain; charset=utf-8"
    return FileResponse(p, media_type=media)


# ---- report -----------------------------------------------------------------

def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


@app.get("/api/report")
def report(month: str | None = None, b: str = Depends(bid)):
    month = month or datetime.now().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month must be YYYY-MM")
    brand_pk = b
    in_month = lambda ts: bool(ts) and ts.startswith(month)  # noqa: E731
    threats_ = [t for t in S.store.threats(brand_pk, limit=100000) if in_month(t["detected_at"])]
    cases_ = [c for c in S.store.cases(brand_pk) if in_month(c["opened_at"])]
    events = [e for e in S.store.all_clock(brand_pk) if in_month(e["ts"])]

    filed_at = {e["notice_id"]: e["ts"] for e in events if e["event"] == "filed"}
    per_channel: dict[str, dict] = {}
    for e in events:
        ch = per_channel.setdefault(e["channel"] or "case", {"filed": 0, "first_response": 0, "removed": 0,
                                                              "detect_to_removed_min": [], "file_to_removed_min": []})
        ch[e["event"]] = ch.get(e["event"], 0) + 1
        if e["event"] == "removed":
            ch["detect_to_removed_min"].append((_ts(e["ts"]) - _ts(e["detected_at"])).total_seconds() / 60)
            if e["notice_id"] in filed_at:
                ch["file_to_removed_min"].append((_ts(e["ts"]) - _ts(filed_at[e["notice_id"]])).total_seconds() / 60)
    channels = []
    for name, ch in sorted(per_channel.items()):
        d2r, f2r = ch.pop("detect_to_removed_min"), ch.pop("file_to_removed_min")
        channels.append({"channel": name, **ch,
                         "median_detect_to_removed_min": round(statistics.median(d2r), 1) if d2r else None,
                         "median_file_to_removed_min": round(statistics.median(f2r), 1) if f2r else None})

    by_conf: dict[str, int] = {}
    for t in threats_:
        by_conf[t["confidence"] or "pending"] = by_conf.get(t["confidence"] or "pending", 0) + 1
    return {
        "brand": S.watch.brand.display_name, "month": month, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": True,
        "limitations": ["Dry run: no notice was sent to any recipient.",
                        "Responses and removals are logged by an operator and marked simulated.",
                        "Detection covers Certificate Transparency logs and manual submissions only."],
        "threats_detected": len(threats_), "threats_by_confidence": by_conf,
        "cases_opened": len(cases_),
        "notices": {s: sum(1 for c in cases_ for n in S.store.notices(c["id"]) if n["status"] == s)
                    for s in ("drafted", "approved", "filed_dry_run", "rejected")},
        "channels": channels,
        "cases": [{"id": c["id"], "opened_at": c["opened_at"], "threats": c["threat_count"],
                   "filed": c["filed_count"], "removed": c["removed_count"], "status": c["status"]} for c in cases_],
    }


# ---- live events (SSE) -------------------------------------------------------

@app.get("/api/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    S.watch.subscribers.add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'ct_stats', **ct_snapshot()})}\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            S.watch.subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
