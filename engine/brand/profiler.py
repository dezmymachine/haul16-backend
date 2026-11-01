"""Brand Profiler: build a sourced, versioned identity dossier for one brand.

Collectors run one after another; each is isolated, so a missing key or a vendor outage skips that collector and never fails the profile.
Every fact keeps its provenance. Progress is written to the job row so any process (UI polling) can show it.
"""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from engine.brand import dossier, logos, site
from engine.brand.repo import BrandRepo, derive_seed_terms, domain_of, now
from engine.clients import dnsx, telegram
from engine.clients.apify import Apify
from engine.clients.base import NotConfigured, UpstreamError, new_client, sha256_text
from engine.clients.firecrawl import Firecrawl
from engine.clients.search import Search
from engine.llm import LLM
from engine.rdap import enrich as rdap_enrich
from engine.settings import get_settings
from engine.store import Store

SCAM_WORDS = re.compile(r"\b(scam|fake|phishing|impersonat\w*|fraud\w*|warning|beware|clone|spoof\w*|counterfeit)\b", re.I)
COLLECTORS = ["inputs", "dns", "rdap", "website", "assets", "linkedin", "socials", "news", "dossier"]


@dataclass
class Ctx:
    store: Store
    repo: BrandRepo
    brand: dict
    http: httpx.AsyncClient
    version: int
    pages: list[dict] = field(default_factory=list)          # {url, text, links}
    images: list[dict] = field(default_factory=list)
    social_urls: list[dict] = field(default_factory=list)
    domain: str | None = None

    def fact(self, kind: str, value: str, **kw):
        return self.repo.upsert_fact(self.brand["id"], kind, value, profile_version=self.version, **kw)


def _normalise_url(u: str) -> str:
    return u if u.startswith(("http://", "https://")) else f"https://{u}"


# ---- collectors ---------------------------------------------------------------------

async def c_inputs(ctx: Ctx) -> dict:
    b, n = ctx.brand, 0
    if b.get("website"):
        ctx.domain = domain_of(b["website"])
        ctx.fact("domain", ctx.domain, collector="user", origin="user", source_url=_normalise_url(b["website"]), confidence=1.0, confirmed=True, label="Primary domain")
        n += 1
    ctx.fact("trading_name", b["display_name"], collector="user", origin="user", confidence=1.0, confirmed=True, label="Brand name as entered")
    inputs = b.get("inputs") or {}
    urls = ([b["linkedin_url"]] if b.get("linkedin_url") else []) + list(inputs.get("socials", []))
    for u in urls:
        for s in site.socials([u]):
            ctx.fact("social", s["url"], collector="user", origin="user", source_url=s["url"], confidence=1.0, confirmed=True,
                     label=f"{s['platform']} @{s['handle']}", data={"platform": s["platform"], "handle": s["handle"]})
            ctx.social_urls.append(s)
            n += 1
    for d in inputs.get("extra_domains", []):
        ctx.fact("domain", domain_of(d), collector="user", origin="user", confidence=1.0, confirmed=True, label="Additional domain")
        n += 1
    return {"facts": n}


async def c_dns(ctx: Ctx) -> dict:
    if not ctx.domain:
        raise NotConfigured("no website entered")
    d = ctx.domain
    a, ns, mx, txt = await dnsx.records(d, "A"), await dnsx.records(d, "NS"), await dnsx.records(d, "MX"), await dnsx.txt_values(d)
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), None)
    mail = "Google Workspace" if any("google" in m.lower() for m in mx) else "Microsoft 365" if any("outlook" in m.lower() or "protection.outlook" in m.lower() for m in mx) else (mx[0].split()[-1] if mx else None)
    ctx.fact("dns", d, collector="dns", origin="third_party", confidence=0.9, label="DNS records",
             data={"a": a, "ns": ns, "mx": mx, "spf": spf, "email_provider": mail, "txt_count": len(txt)})
    return {"records": len(a) + len(ns) + len(mx) + len(txt)}


async def c_rdap(ctx: Ctx) -> dict:
    if not ctx.domain:
        raise NotConfigured("no website entered")
    info = await rdap_enrich(ctx.domain)
    reg, host = info.get("registration") or {}, info.get("hosting") or {}
    if not (reg or host):
        return {"records": 0}
    ctx.fact("domain_registration", ctx.domain, collector="rdap", origin="third_party", confidence=0.9, label="Registrar and hosting",
             data={"registrar": reg.get("registrar"), "registrar_abuse_email": reg.get("registrar_abuse_email"), "created": reg.get("created"),
                   "hosting_network": host.get("network"), "hosting_country": host.get("country"), "host_abuse_email": host.get("host_abuse_email"), "ips": info.get("ips")})
    return {"records": 1}


def _record_page(ctx: Ctx, page: site.Page | dict, collector: str) -> None:
    url = page.url if isinstance(page, site.Page) else page["url"]
    text = page.text if isinstance(page, site.Page) else page.get("text", "")
    links = page.links if isinstance(page, site.Page) else page.get("links", [])
    ctx.pages.append({"url": url, "text": text, "links": links})
    ctx.fact("source", url, collector=collector, origin="official", source_url=url, content_hash=sha256_text(text), confidence=1.0,
             label=(page.title if isinstance(page, site.Page) else page.get("title")) or url, data={"chars": len(text)})
    for s in site.socials(links, text):
        ctx.fact("social", s["url"], collector=collector, origin="official", source_url=url, confidence=0.85, label=f"{s['platform']} @{s['handle']}",
                 data={"platform": s["platform"], "handle": s["handle"]})
        if s not in ctx.social_urls:
            ctx.social_urls.append(s)
    for a in site.apps(links, text):
        ctx.fact("app", a["url"], collector=collector, origin="official", source_url=url, confidence=0.9, label=f"{a['store']} app {a['id']}", data=a)
    c = site.contacts(text, links)
    for p in c["phones_gh"] + c["phones_intl"]:
        ctx.fact("phone", p, collector=collector, origin="official", source_url=url, confidence=0.7, label="Phone number on the site")
    for e in c["emails"]:
        ctx.fact("email", e, collector=collector, origin="official", source_url=url, confidence=0.7, label="Email on the site")
    for kind, ws in c["wallets"].items():
        for w in ws:
            ctx.fact("wallet", w, collector=collector, origin="official", source_url=url, confidence=0.6, label=f"{kind} wallet on the site")


async def c_website(ctx: Ctx) -> dict:
    if not ctx.brand.get("website"):
        raise NotConfigured("no website entered")
    home_url = _normalise_url(ctx.brand["website"])
    hints = [h for h in derive_seed_terms(ctx.brand["display_name"], ctx.brand.get("website")) if len(h) >= 4]
    home = await site.fetch_page(ctx.http, home_url, hints)
    if not home:
        raise UpstreamError(f"could not load {home_url}")
    ctx.images += home.images
    _record_page(ctx, home, "website")
    org = site.org_from_jsonld(home.jsonld)
    if org.get("legal_name") or org.get("name"):
        ctx.fact("legal_name", org.get("legal_name") or org["name"], collector="website", origin="official", source_url=home.url, confidence=0.8, label="From the site's structured data")
    if org.get("address"):
        ctx.fact("hq_address", org["address"], collector="website", origin="official", source_url=home.url, confidence=0.7, label="Address in structured data")
    for u in org.get("same_as", []):
        for s in site.socials([u]):
            ctx.fact("social", s["url"], collector="website", origin="official", source_url=home.url, confidence=0.9, label=f"{s['platform']} @{s['handle']}", data={"platform": s["platform"], "handle": s["handle"]})
    for name in org.get("founders", []):
        ctx.repo.add_person(ctx.brand["id"], name, "Founder", collector="website", source_url=home.url, verification="corroborated")

    firecrawl = None
    try:
        firecrawl = Firecrawl(ctx.http)
    except NotConfigured:
        pass
    targets = site.key_links(home.url, home.links, limit=6)
    if firecrawl:
        try:
            mapped = await firecrawl.map(home.url, limit=80)
            targets = list(dict.fromkeys(site.key_links(home.url, mapped, limit=8) + targets))[:8]
        except UpstreamError:
            pass
    fetched = 1
    for u in targets:
        try:
            if firecrawl:
                r = await firecrawl.scrape(u)
                _record_page(ctx, {"url": r["url"], "text": r["markdown"], "links": r["links"], "title": r.get("title")}, "firecrawl")
            else:
                pg = await site.fetch_page(ctx.http, u, hints)
                if not pg:
                    continue
                ctx.images += pg.images
                _record_page(ctx, pg, "website")
            fetched += 1
        except (UpstreamError, NotConfigured):
            continue
    return {"pages": fetched, "firecrawl": bool(firecrawl)}


async def c_assets(ctx: Ctx) -> dict:
    if not ctx.images:
        raise NotConfigured("no images found on the site")
    n = await logos.collect(ctx.http, ctx.repo, ctx.brand["id"], ctx.images, _normalise_url(ctx.brand["website"]))
    return {"assets": n}


async def c_linkedin(ctx: Ctx) -> dict:
    url = ctx.brand.get("linkedin_url") or next((s["url"] for s in ctx.social_urls if s["platform"] == "linkedin"), None)
    if not url:
        raise NotConfigured("no LinkedIn page known")
    apify = Apify(ctx.http)
    s = get_settings()
    actor_input = json.loads(s.apify_linkedin_input.replace("{url}", url))
    items = await apify.run(s.apify_linkedin_actor, actor_input)
    if not items:
        return {"profiles": 0}
    it = items[0]
    pick = lambda *keys: next((it[k] for k in keys if it.get(k) not in (None, "", [])), None)  # noqa: E731
    data = {"name": pick("name", "companyName", "title"), "followers": pick("followers", "followerCount", "followersCount"),
            "employees": pick("employeeCount", "staffCount", "employees_count"), "tagline": pick("tagline", "headline"),
            "industry": pick("industry", "industries"), "website": pick("website", "websiteUrl"), "description": pick("description", "about"),
            "headquarters": pick("headquarter", "headquarters", "location")}
    ctx.fact("linkedin_profile", url, collector="apify:linkedin", origin="third_party", source_url=url, confidence=0.8, label=str(data["name"] or "LinkedIn page"), data=data)
    if data.get("website") and ctx.domain and domain_of(str(data["website"])) == ctx.domain:
        ctx.fact("social", url, collector="apify:linkedin", origin="third_party", source_url=url, confidence=0.9, label="linkedin company page",
                 data={"platform": "linkedin", "handle": url.rstrip("/").split("/")[-1]})  # the page points back at the official site
    n_people = 0
    for emp in (it.get("employees") or it.get("leaders") or it.get("keyPeople") or [])[:15]:
        if isinstance(emp, dict) and emp.get("name"):
            ctx.repo.add_person(ctx.brand["id"], emp["name"], emp.get("title") or emp.get("position"), collector="apify:linkedin", source_url=url,
                                profile_url=emp.get("url") or emp.get("profileUrl"), photo_url=emp.get("photo") or emp.get("image"))
            n_people += 1
    return {"profiles": 1, "people": n_people}


async def c_socials(ctx: Ctx) -> dict:
    n = 0
    for s in ctx.social_urls:
        if s["platform"] == "telegram":
            pv = await telegram.preview(ctx.http, s["handle"])
            if pv:
                ctx.fact("telegram", pv["url"], collector="telegram_public", origin="third_party", source_url=pv["url"], confidence=0.85,
                         label=f"{pv['title']} ({pv['kind']})", data={**pv, "platform": "telegram"})
                n += 1
    s = get_settings()
    if s.apify_social_actor and s.apify_token:
        apify = Apify(ctx.http)
        others = [x["url"] for x in ctx.social_urls if x["platform"] in ("x", "facebook", "instagram", "tiktok", "youtube")][:6]
        if others:
            for it in await apify.run(s.apify_social_actor, {"startUrls": [{"url": u} for u in others]}):
                url = it.get("url") or it.get("profileUrl")
                if url:
                    ctx.fact("social_profile", url, collector="apify:social", origin="third_party", source_url=url, confidence=0.7,
                             label=str(it.get("name") or it.get("fullName") or url), data={k: it.get(k) for k in ("followers", "followersCount", "verified", "biography", "description", "created") if it.get(k) is not None})
                    n += 1
    elif n == 0:
        raise NotConfigured("no public Telegram handle found and no APIFY_SOCIAL_ACTOR configured")
    return {"profiles": n}


async def c_news(ctx: Ctx) -> dict:
    search = Search(ctx.http)
    name = ctx.brand["display_name"]
    own = ctx.domain or ""
    n = w = 0
    queries = [(f'"{name}"', "news"), (f'"{name}" scam OR fake OR phishing OR impersonation OR warning', "news"), (f'"{name}" fake OR scam OR impersonating', "web")]
    for q, mode in queries:
        results = await (search.news(q, 8) if mode == "news" else search.web(q, 8))
        for r in results:
            url = r.get("url")
            blob = f"{r.get('title') or ''} {r.get('snippet') or ''}"
            if not url or name.lower() not in blob.lower() or (own and domain_of(url) == own):
                continue
            scam = bool(SCAM_WORDS.search(blob))
            ctx.fact("warning" if scam else "news", url, collector="search", origin="third_party", source_url=url, confidence=0.6,
                     label=r.get("title") or url, data={"snippet": r.get("snippet"), "age": r.get("age"), "source": r.get("source") or domain_of(url)})
            n, w = n + (not scam), w + scam
    return {"news": n, "warnings": w}


async def c_dossier(ctx: Ctx) -> dict:
    texts = [p for p in ctx.pages if p.get("text")]
    if not texts:
        raise NotConfigured("no page text collected")
    llm = LLM(ctx.http, ctx.store)
    ex = await dossier.extract(llm, ctx.brand["id"], texts)
    n = 0
    mapping = [("legal_names", "legal_name", 0.8), ("trading_names", "trading_name", 0.7), ("registration_numbers", "registration_no", 0.8),
               ("licences", "licence", 0.8), ("regulators", "regulator", 0.7), ("incorporated", "incorporated", 0.7), ("hq_addresses", "hq_address", 0.7),
               ("official_phones", "phone", 0.7), ("official_emails", "email", 0.7), ("payment_instructions", "payment_instruction", 0.7)]
    for attr, kind, conf in mapping:
        for it in getattr(ex, attr):
            ctx.fact(kind, it.value, collector="llm_extraction", origin="official", source_url=it.source_url, confidence=conf, label="Extracted from the site, cited")
            n += 1
    for p in ex.leadership:
        ctx.repo.add_person(ctx.brand["id"], p.name, p.title, collector="llm_extraction", source_url=p.source_url, verification="corroborated")
        n += 1
    if ex.summary:
        ctx.fact("summary", ex.summary[:600], collector="llm_extraction", origin="official", confidence=0.6, label="Neutral summary")
    return {"items": n}


FUNCS = {"inputs": c_inputs, "dns": c_dns, "rdap": c_rdap, "website": c_website, "assets": c_assets, "linkedin": c_linkedin,
         "socials": c_socials, "news": c_news, "dossier": c_dossier}


# ---- orchestration --------------------------------------------------------------------

async def run_profile(store: Store, brand_id: str, progress=None) -> dict:
    repo = BrandRepo(store)
    brand = repo.get(brand_id)
    if not brand:
        raise ValueError(f"no such brand {brand_id}")
    repo.update(brand_id, status="profiling")
    version = repo.db.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM brand_profile_versions WHERE brand_id=?", (brand_id,)).fetchone()["v"]
    disabled = {r["collector"] for r in store.db.execute("SELECT collector FROM source_toggles WHERE enabled=FALSE")}
    steps = {name: {"status": "pending"} for name in COLLECTORS}

    def report():
        if progress:
            progress({"version": version, "steps": steps})

    report()
    async with new_client(timeout=30) as http:
        ctx = Ctx(store, repo, brand, http, version)
        for name in COLLECTORS:
            steps[name] = {"status": "running", "started_at": now()}
            report()
            if name in disabled:
                steps[name] = {"status": "skipped", "reason": "disabled by an admin"}
            else:
                try:
                    steps[name] = {"status": "ok", **(await FUNCS[name](ctx))}
                except NotConfigured as e:
                    steps[name] = {"status": "skipped", "reason": str(e)}
                except Exception as e:  # noqa: BLE001  one collector never fails the profile
                    steps[name] = {"status": "error", "error": repr(e)[:300]}
            report()
    conflicts = repo.mark_conflicts(brand_id)
    extra = [f["value"] for f in repo.facts(brand_id, "trading_name")] + [f["value"] for f in repo.facts(brand_id, "legal_name")]
    seeds = list(dict.fromkeys((brand.get("seed_terms") or []) + derive_seed_terms(brand["display_name"], brand.get("website"), extra)))
    facts = repo.facts(brand_id)
    counts: dict[str, int] = {}
    for f in facts:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    summary = {"steps": steps, "counts": counts, "conflicts": conflicts, "needs_review": sum(1 for f in facts if f["needs_confirmation"] and f["review_status"] == "pending")}
    v = repo.save_version(brand_id, summary)
    repo.update(brand_id, status="ready", seed_terms=seeds)
    store.audit("system", "brand_profiled", brand_id, {"version": v, "counts": counts})
    return {"version": v, **summary}
