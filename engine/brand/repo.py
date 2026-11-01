"""Persistence for brands, versioned profiles and sourced facts.

Every fact keeps where it came from. Verification level is computed from who reported it:
  verified       the brand's own site (origin 'official') AND an independent source agree, or a person entered it
  corroborated   only the brand's own site reports it
  single_source  only a third party reports it
  conflicting    two different values for something that can only have one (registration number, legal name)
"""
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from engine.store import Store

SINGULAR = {"legal_name", "registration_no", "incorporated", "hq_address"}
CONFIRM_KINDS = {"phone", "wallet", "bank_account", "social", "telegram", "whatsapp"}   # used for matching only once a person confirms
TYPE_ORDER = ["domain", "legal_name", "trading_name", "registration_no", "licence", "hq_address", "phone", "email", "social",
              "telegram", "whatsapp", "app", "wallet", "bank_account", "regulator", "news", "warning", "dns", "domain_registration", "summary"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def domain_of(url_or_host: str) -> str:
    h = urlparse(url_or_host if "//" in url_or_host else f"https://{url_or_host}").hostname or url_or_host
    return h.lower().removeprefix("www.")


def derive_seed_terms(name: str, domain: str | None = None, extra: list[str] = ()) -> list[str]:
    """Lowercase letters/digits strings a scammer would put in a hostname: the name squashed, the name's words, the domain label."""
    terms: list[str] = []
    squash = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(squash) >= 4:
        terms.append(squash)
    words = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in name.split()]
    if len(words) > 1 and len(words[0]) >= 4:
        terms.append(words[0])
    if domain:
        label = domain_of(domain).split(".")[0]
        if len(label) >= 4:
            terms.append(re.sub(r"[^a-z0-9]", "", label))
    for e in extra:
        e = re.sub(r"[^a-z0-9]", "", e.lower())
        if len(e) >= 4:
            terms.append(e)
    return list(dict.fromkeys(terms))


class BrandRepo:
    def __init__(self, store: Store):
        self.db = store.db
        self.store = store

    # ---- brands --------------------------------------------------------------

    def create(self, display_name: str, website: str | None = None, linkedin_url: str | None = None,
               inputs: dict | None = None, fictional: bool = False, brand_id: str | None = None) -> dict:
        bid = brand_id or new_id("brand")
        seeds = derive_seed_terms(display_name, website)
        t = now()
        self.db.execute(
            "INSERT INTO brands (id, display_name, website, linkedin_url, status, verify_token, fictional, seed_terms, inputs, created_at, updated_at)"
            " VALUES (?,?,?,?, 'draft', ?, ?, ?, ?, ?, ?)",
            (bid, display_name.strip(), website, linkedin_url, f"haul16-verify={secrets.token_hex(12)}", fictional,
             json.dumps(seeds), json.dumps(inputs or {}), t, t))
        self.store.audit("operator", "brand_created", bid, {"display_name": display_name})
        return self.get(bid)

    def get(self, brand_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        return dict(r) if r else None

    def list_all(self) -> "list[dict]":
        return [dict(r) for r in self.db.execute("SELECT * FROM brands ORDER BY created_at")]

    def update(self, brand_id: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if k in ("seed_terms", "inputs") else v for k, v in fields.items()]
        self.db.execute(f"UPDATE brands SET {cols}, updated_at=? WHERE id=?", (*vals, now(), brand_id))

    def delete(self, brand_id: str) -> None:
        """Remove a brand and everything collected or detected for it (used to clear test brands). The audit log is kept."""
        q = self.db.execute
        case_ids = [r["id"] for r in q("SELECT id FROM cases WHERE brand_id=?", (brand_id,))]
        thr_ids = [r["id"] for r in q("SELECT id FROM threats WHERE brand_id=?", (brand_id,))]
        for cid in case_ids:
            q("DELETE FROM notices WHERE case_id=?", (cid,))
            q("DELETE FROM clock_events WHERE case_id=?", (cid,))
        for tid in thr_ids:
            q("DELETE FROM identifiers WHERE threat_id=?", (tid,))
        for t in ("threats", "cases", "brand_facts", "brand_assets", "brand_people", "brand_profile_versions", "signals", "jobs", "llm_calls"):
            q(f"DELETE FROM {t} WHERE brand_id=?", (brand_id,))
        q("DELETE FROM brands WHERE id=?", (brand_id,))
        self.store.audit("operator", "brand_deleted", brand_id)

    # ---- facts ---------------------------------------------------------------

    def upsert_fact(self, brand_id: str, kind: str, value: str, *, collector: str, origin: str, source_url: str | None = None,
                    label: str | None = None, data: dict | None = None, content_hash: str | None = None,
                    confidence: float = 0.6, profile_version: int | None = None, confirmed: bool = False) -> dict:
        value = value.strip()
        existing = self.db.execute("SELECT * FROM brand_facts WHERE brand_id=? AND kind=? AND value=?", (brand_id, kind, value)).fetchone()
        origins = {origin}
        merged = dict(data or {})
        if existing:
            old = existing["data"] or {}
            origins |= set(old.get("origins", []))
            merged = {**old, **merged}
            confidence = max(confidence, existing["confidence"])
            confirmed = confirmed or existing["review_status"] == "confirmed"
            source_url = existing["source_url"] or source_url
        merged["origins"] = sorted(origins)
        if "user" in origins or ("official" in origins and "third_party" in origins):
            verification = "verified"
        elif "official" in origins:
            verification = "corroborated"
        else:
            verification = "single_source"
        review = "confirmed" if (confirmed or "user" in origins) else (existing["review_status"] if existing else "pending")
        fid = existing["id"] if existing else new_id("fact")
        self.db.execute(
            """INSERT INTO brand_facts (id, brand_id, kind, value, label, data, source_url, collector, content_hash, confidence,
                   verification, review_status, needs_confirmation, fetched_at, profile_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (brand_id, kind, value) DO UPDATE SET label=EXCLUDED.label, data=EXCLUDED.data, source_url=EXCLUDED.source_url,
                   content_hash=EXCLUDED.content_hash, confidence=EXCLUDED.confidence, verification=EXCLUDED.verification,
                   review_status=EXCLUDED.review_status, fetched_at=EXCLUDED.fetched_at, profile_version=EXCLUDED.profile_version""",
            (fid, brand_id, kind, value, label, json.dumps(merged), source_url, collector, content_hash, confidence, verification,
             review, kind in CONFIRM_KINDS, now(), profile_version))
        return {"id": fid, "verification": verification}

    def facts(self, brand_id: str, kind: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM brand_facts WHERE brand_id=?", [brand_id]
        if kind:
            q, a = q + " AND kind=?", a + [kind]
        rows = [dict(r) for r in self.db.execute(q + " ORDER BY confidence DESC, value", a)]
        rows.sort(key=lambda r: TYPE_ORDER.index(r["kind"]) if r["kind"] in TYPE_ORDER else 99)
        return rows

    def set_review(self, brand_id: str, fact_id: str, status: str) -> bool:
        r = self.db.execute("UPDATE brand_facts SET review_status=? WHERE id=? AND brand_id=?", (status, fact_id, brand_id))
        if r.rowcount:
            self.store.audit("operator", f"fact_{status}", fact_id, {"brand_id": brand_id})
        return bool(r.rowcount)

    def mark_conflicts(self, brand_id: str) -> int:
        """Singular facts (registration number, legal name...) with more than one distinct value are flagged for review."""
        n = 0
        for kind in SINGULAR:
            rows = self.facts(brand_id, kind)
            if len({r["value"].lower() for r in rows}) > 1:
                self.db.execute("UPDATE brand_facts SET verification='conflicting' WHERE brand_id=? AND kind=?", (brand_id, kind))
                n += len(rows)
        return n

    # ---- assets and people ---------------------------------------------------

    def add_asset(self, brand_id: str, kind: str, url: str, sha256: str | None, phash: str | None, mmh3: int | None,
                  meta: dict | None = None, source_url: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO brand_assets (id, brand_id, kind, url, sha256, phash, mmh3, meta, source_url, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (brand_id, kind, url) DO UPDATE SET sha256=EXCLUDED.sha256, phash=EXCLUDED.phash, mmh3=EXCLUDED.mmh3, meta=EXCLUDED.meta",
            (new_id("asset"), brand_id, kind, url, sha256, phash, mmh3, json.dumps(meta or {}), source_url, now()))

    def assets(self, brand_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM brand_assets WHERE brand_id=? ORDER BY kind, created_at", (brand_id,))]

    def add_person(self, brand_id: str, name: str, title: str | None, *, collector: str, source_url: str | None = None,
                   profile_url: str | None = None, photo_url: str | None = None, bio: str | None = None, verification: str = "single_source") -> None:
        self.db.execute(
            "INSERT INTO brand_people (id, brand_id, name, title, profile_url, photo_url, bio, source_url, collector, verification, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (brand_id, name, title) DO UPDATE SET profile_url=COALESCE(EXCLUDED.profile_url, brand_people.profile_url),"
            " photo_url=COALESCE(EXCLUDED.photo_url, brand_people.photo_url), bio=COALESCE(EXCLUDED.bio, brand_people.bio), fetched_at=EXCLUDED.fetched_at",
            (new_id("person"), brand_id, name.strip(), (title or "").strip(), profile_url, photo_url, bio, source_url, collector, verification, now()))

    def people(self, brand_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM brand_people WHERE brand_id=? ORDER BY name", (brand_id,))]

    # ---- versions ------------------------------------------------------------

    def save_version(self, brand_id: str, summary: dict) -> int:
        v = (self.db.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM brand_profile_versions WHERE brand_id=?", (brand_id,)).fetchone()["v"])
        self.db.execute("INSERT INTO brand_profile_versions (brand_id, version, created_at, summary) VALUES (?,?,?,?)",
                        (brand_id, v, now(), json.dumps(summary)))
        self.update(brand_id, active_version=v)
        return v

    def versions(self, brand_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM brand_profile_versions WHERE brand_id=? ORDER BY version DESC", (brand_id,))]

    # ---- bridge to the detection engine --------------------------------------

    def detection_inputs(self, brand_id: str) -> dict:
        """What the matcher and scorer need, built only from facts a person confirmed or the brand's own site backs."""
        b = self.get(brand_id)
        ok = lambda f: f["review_status"] != "rejected" and f["verification"] in ("verified", "corroborated") or f["review_status"] == "confirmed"  # noqa: E731
        confirmed = lambda f: f["review_status"] == "confirmed"  # noqa: E731
        facts = self.facts(brand_id)
        by = lambda k: [f for f in facts if f["kind"] == k]  # noqa: E731
        handles = {}
        for f in by("social"):
            if confirmed(f):
                handles[(f["data"] or {}).get("platform", "social")] = f["value"]
        return {
            "brand": b,
            "official_domains": sorted({f["value"] for f in by("domain") if ok(f)}),
            "official_numbers": sorted({f["value"] for f in by("phone") if confirmed(f)}),
            "official_wallets": sorted({f["value"] for f in by("wallet") if confirmed(f)}),
            "official_handles": handles,
            "allowlist_domains": sorted({f["value"] for f in by("partner") if confirmed(f)}),
            "favicon_mmh3": sorted({a["mmh3"] for a in self.assets(brand_id) if a["kind"] == "favicon" and a["mmh3"] is not None}),
        }
