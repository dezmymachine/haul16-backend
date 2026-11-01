"""HTTP routes for brands, profiling, verification and fact review."""
import base64
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine import jobs
from engine.brand import verify
from engine.brand.loader import brand_from_db
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
    return {k: b[k] for k in ("id", "display_name", "website", "linkedin_url", "status", "ownership_status", "verified_at", "fictional",
                              "authorisation_letter_ref", "authorisation_letter_sha256", "seed_terms", "active_version", "is_active", "created_at", "updated_at")}


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
    return {"active": brand_id}
