"""HTTP routes for brands, profiling, verification and fact review."""
import base64
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine import jobs, patrol
from engine.brand import verify
from engine.brand.loader import NO_BRAND, brand_from_db
from engine.brand.repo import BrandRepo
from engine.settings import ROOT

router = APIRouter(prefix="/api/brands")
STATE = None   # bound by engine.api (holds .store and .watch)
LETTERS = ROOT / "data" / "letters"


def bind(state) -> None:
    global STATE
    STATE = state


def repo() -> BrandRepo:
    return BrandRepo(STATE.store)


def _need(brand_id: str) -> dict:
    b = repo().get(brand_id)
    if not b:
        raise HTTPException(404, "No such brand")
    return b


def _public(b: dict) -> dict:
    return {"patrol_hours": patrol.interval_hours(b), **{k: b[k] for k in ("id", "display_name", "website", "linkedin_url", "status", "ownership_status", "verified_at", "fictional",
                              "authorisation_letter_ref", "authorisation_letter_sha256", "seed_terms", "active_version", "is_active", "created_at", "updated_at")}}


def _job(brand_id: str) -> dict | None:
    j = jobs.latest(STATE.store, "brand_profile", brand_id)
    if not j:
        return None
    return {"id": j["id"], "status": j["status"], "attempts": j["attempts"], "error": j["last_error"], "updated_at": j["updated_at"],
            "steps": (j["result"] or {}).get("steps", {}), "version": (j["result"] or {}).get("version")}


class BrandIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    website: str | None = Field(default=None, max_length=300)
    linkedin_url: str | None = Field(default=None, max_length=300)
    socials: list[str] = Field(default_factory=list, max_length=12)
    extra_domains: list[str] = Field(default_factory=list, max_length=10)


@router.get("")
def list_brands():
    out = []
    for b in repo().list_all():
        counts = {r["kind"]: r["n"] for r in STATE.store.db.execute("SELECT kind, COUNT(*) AS n FROM brand_facts WHERE brand_id=? GROUP BY kind", (b["id"],))}
        out.append({**_public(b), "fact_counts": counts})
    return out


@router.post("")
def create_brand(body: BrandIn):
    r = repo()
    if not (body.website or body.linkedin_url):
        raise HTTPException(422, "Enter a website or a LinkedIn page so there is something to build the profile from")
    b = r.create(body.display_name, body.website, body.linkedin_url, {"socials": body.socials, "extra_domains": body.extra_domains})
    return {**_public(b), "verification": verify.instructions(b)}


@router.get("/{brand_id}")
def get_brand(brand_id: str):
    b = _need(brand_id)
    return {**_public(b), "verification": verify.instructions(b), "job": _job(brand_id)}


@router.post("/{brand_id}/profile/run")
def run_profile(brand_id: str):
    _need(brand_id)
    cur = _job(brand_id)
    if cur and cur["status"] in ("queued", "running"):
        return cur
    jobs.enqueue(STATE.store, "brand_profile", brand_id, {}, max_attempts=2)
    repo().update(brand_id, status="profiling")
    return _job(brand_id)


@router.get("/{brand_id}/profile/status")
def profile_status(brand_id: str):
    _need(brand_id)
    return _job(brand_id) or {"status": "none", "steps": {}}


@router.get("/{brand_id}/profile")
def profile(brand_id: str):
    b = _need(brand_id)
    r = repo()
    facts = r.facts(brand_id)
    groups: dict[str, list] = {}
    for f in facts:
        groups.setdefault(f["kind"], []).append({k: f[k] for k in ("id", "kind", "value", "label", "data", "source_url", "collector", "confidence", "verification",
                                                                    "review_status", "needs_confirmation", "fetched_at")})
    versions = r.versions(brand_id)
    return {"brand": {**_public(b), "verification": verify.instructions(b)}, "job": _job(brand_id), "groups": groups, "people": r.people(brand_id),
            "assets": r.assets(brand_id), "summary": versions[0]["summary"] if versions else None,
            "conflicts": [f for f in facts if f["verification"] == "conflicting"] and [f["id"] for f in facts if f["verification"] == "conflicting"],
            "needs_review": [f["id"] for f in facts if f["needs_confirmation"] and f["review_status"] == "pending"]}


@router.post("/{brand_id}/verify")
async def verify_brand(brand_id: str):
    _need(brand_id)
    return await verify.check(repo(), brand_id)


@router.post("/{brand_id}/facts/{fact_id}/{action}")
def review_fact(brand_id: str, fact_id: str, action: str):
    _need(brand_id)
    if action not in ("confirm", "reject"):
        raise HTTPException(404, "Unknown action")
    if not repo().set_review(brand_id, fact_id, "confirmed" if action == "confirm" else "rejected"):
        raise HTTPException(404, "No such fact")
    return {"ok": True}


class LetterIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=4, max_length=8_000_000)


@router.post("/{brand_id}/letter")
def upload_letter(brand_id: str, body: LetterIn):
    _need(brand_id)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "content_base64 is not valid base64") from None
    sha = hashlib.sha256(data).hexdigest()
    LETTERS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in Path(body.filename).name if c.isalnum() or c in "._-") or "letter"
    (LETTERS / f"{brand_id}-{sha[:12]}-{safe}").write_bytes(data)
    repo().update(brand_id, authorisation_letter_ref=safe, authorisation_letter_sha256=sha)
    STATE.store.audit("operator", "authorisation_letter_uploaded", brand_id, {"sha256": sha, "filename": safe})
    return {"ref": safe, "sha256": sha, "bytes": len(data)}


def _sweep(brand_id: str) -> dict:
    j = jobs.latest(STATE.store, "discovery", brand_id)
    if not j:
        return {"status": "none"}
    res = j["result"] or {}
    return {"job_status": j["status"], "status": "done" if j["status"] == "done" else ("failed" if j["status"] == "failed" else "running"),
            "error": j["last_error"], "updated_at": j["updated_at"], "phase": res.get("phase", "queued"), "generated": res.get("generated", 0),
            "checked": res.get("checked", 0), "registered": res.get("registered", 0), "queued": res.get("queued", 0),
            "skipped_known": res.get("skipped_known", 0), "sources": res.get("sources", {})}


@router.post("/{brand_id}/sweep")
def run_sweep_route(brand_id: str):
    b = _need(brand_id)
    if b["status"] != "ready":
        raise HTTPException(409, "Build the brand's profile first")
    if not b["is_active"]:
        raise HTTPException(409, "Start protection first. Detection must be watching this brand.")
    cur = _sweep(brand_id)
    if cur["status"] == "running":
        return cur
    jobs.enqueue(STATE.store, "discovery", brand_id, {}, max_attempts=1)
    return _sweep(brand_id)


@router.get("/{brand_id}/sweep/status")
def sweep_status(brand_id: str):
    _need(brand_id)
    return _sweep(brand_id)


@router.get("/{brand_id}/setup")
def setup_state(brand_id: str):
    """Where this brand is in setup, and the one thing to do next. The wizard and Home both read this so they always agree."""
    b = _need(brand_id)
    r = repo()
    facts = r.facts(brand_id)
    undecided = [f for f in facts if f["review_status"] == "pending" and (f["needs_confirmation"] or f["verification"] == "conflicting")]
    ready, running = b["status"] == "ready", b["status"] == "profiling"
    verified, letter, active = b["ownership_status"] == "verified", bool(b["authorisation_letter_sha256"]), bool(b["is_active"])
    awaiting = len(STATE.store.all_notices(brand_id, "drafted"))
    approved = len(STATE.store.all_notices(brand_id, "approved"))
    steps = {"brand": {"done": True}, "profile": {"done": ready, "running": running},
             "review": {"done": not undecided, "count": len(undecided)}, "ownership": {"done": verified}, "letter": {"done": letter},
             "protection": {"done": active}}
    wizard_step = 2 if not (ready and not undecided) else 3 if not (verified and letter) else 4
    n_u = len(undecided)
    review = {"key": "review_facts", "title": f"Confirm {n_u} detail{'s' if n_u != 1 else ''} we found", "detail": "Phone numbers, accounts and wallets are only used once you confirm them.", "href": "/setup?step=2", "count": n_u}
    prove = {"key": "prove_ownership", "title": "Prove it's your brand", "detail": "Required before any notice can be filed: verify your domain and add the signed authorisation letter.", "href": "/setup?step=3"}
    if not ready:
        nxt = {"key": "wait_profile", "title": "Reading your brand's public footprint", "detail": "This takes about a minute. You can leave this page.", "href": "/setup?step=2"}
    elif not active:                      # still setting up: finish the details, then switch protection on
        nxt = review if undecided else {"key": "start_protection", "title": "Start protecting your brand", "detail": "HAUL16 will watch for look-alike sites and scan for ones that already exist.", "href": "/setup?step=4"}
    elif awaiting:                        # protecting: whatever is waiting on a person comes before housekeeping
        nxt = {"key": "review_notices", "title": f"Approve {awaiting} takedown notice{'s' if awaiting != 1 else ''}", "detail": "Each one needs a person to approve it before it can be filed.", "href": "/approvals", "count": awaiting}
    elif approved and verified and letter:
        nxt = {"key": "file_notices", "title": f"File {approved} approved notice{'s' if approved != 1 else ''}", "detail": "Filing is a dry run for now: nothing is sent, and the takedown clock starts.", "href": "/approvals?status=approved", "count": approved}
    elif approved:
        nxt = prove                       # something is ready to file but filing is locked
    elif undecided:
        nxt = review
    elif not (verified and letter):
        nxt = prove
    else:
        nxt = {"key": "all_clear", "title": "All clear", "detail": "Nothing needs you right now. HAUL16 keeps watching.", "href": "/home"}
    return {"brand_id": brand_id, "step": wizard_step, "complete": active, "steps": steps, "next_action": nxt, "sweep": _sweep(brand_id),
            "patrol": {"hours": patrol.interval_hours(b), "next_at": (n.isoformat(timespec="seconds") if (n := patrol.next_scan(STATE.store, b)) else None)},
            "counts": {"threats": len(STATE.store.threats(brand_id, limit=100000)), "awaiting": awaiting, "approved": approved}}


class BrandPatch(BaseModel):
    seed_terms: list[str] | None = Field(default=None, max_length=20)
    patrol_hours: int | None = Field(default=None, ge=0, le=168)         # 0 turns scheduled scans off


@router.patch("/{brand_id}")
def patch_brand(brand_id: str, body: BrandPatch):
    """Edit the names detection looks for. Lowercase letters and digits only, at least 4 characters (shorter flood the queue)."""
    b = _need(brand_id)
    if body.seed_terms is not None:
        terms = list(dict.fromkeys(t for t in ("".join(c for c in x.lower() if c.isalnum()) for x in body.seed_terms) if len(t) >= 4))
        if not terms:
            raise HTTPException(422, "Add at least one name of 4 or more letters or digits")
        repo().update(brand_id, seed_terms=terms)
        STATE.store.audit("operator", "seed_terms_updated", brand_id, {"seed_terms": terms})
        if STATE.watch.brand.brand_id == brand_id:        # live detection picks the change up straight away
            brand, cfg = brand_from_db(repo(), brand_id)
            STATE.watch.set_brand(brand, cfg)
            STATE.pipe.matcher = STATE.watch.matcher
    if body.patrol_hours is not None:
        cur = repo().get(brand_id)
        repo().update(brand_id, inputs={**(cur["inputs"] or {}), "patrol_hours": body.patrol_hours})
        STATE.store.audit("operator", "patrol_interval_set", brand_id, {"hours": body.patrol_hours})
    return _public(repo().get(brand_id) or b)


@router.delete("/{brand_id}")
def delete_brand(brand_id: str):
    _need(brand_id)
    was_active = STATE.watch.brand.brand_id == brand_id
    repo().delete(brand_id)
    if was_active:
        STATE.watch.set_brand(NO_BRAND, {})
        STATE.pipe.matcher = STATE.watch.matcher
    return {"deleted": brand_id}


@router.post("/{brand_id}/activate")
def activate(brand_id: str):
    """Point the live detection pipeline (CT matcher and capture workers) at this brand."""
    b = _need(brand_id)
    if b["status"] != "ready":
        raise HTTPException(409, "Build the brand's profile first")
    brand, cfg = brand_from_db(repo(), brand_id)
    STATE.watch.set_brand(brand, cfg)
    STATE.pipe.matcher = STATE.watch.matcher
    STATE.store.db.execute("UPDATE brands SET is_active=(id=?)", (brand_id,))
    STATE.store.audit("operator", "brand_activated", brand_id)
    if _sweep(brand_id)["status"] != "running":        # protection starts with a look-alike scan so there is something to review
        jobs.enqueue(STATE.store, "discovery", brand_id, {}, max_attempts=1)
    return {"active": brand_id, "sweep": _sweep(brand_id)}
