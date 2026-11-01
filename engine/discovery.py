"""Discovery: every place HAUL16 looks for things that imitate a brand, run as one scan.

Each source is isolated. One that needs a key it does not have is `skipped` with the reason; one that fails is `error`; neither stops the others.
Everything a source finds goes through the same matcher, capture and scoring pipeline as certificate-log hits (`Watch.inject`).

  dns_sweep         typo, homoglyph, lure-word and TLD variants of the brand's domain that exist in DNS     no key
  ct_history        certificates issued in the last year whose names contain the brand's terms (crt.sh)     no key
  telegram_handles  Telegram accounts and channels with look-alike handles that name the brand             no key
  urlscan           recent urlscan.io scans of domains containing the brand's terms                          URLSCAN_API_KEY
  web_search        look-alike sites and "official" pages found by web search                                SEARCH_API_KEY (Brave)
  news_mentions     press that names the brand with scam/fake/impersonation wording, and domains it cites    SEARCH_API_KEY (Brave)
  meta_ads          Meta Ad Library ads that use the brand's name from pages that are not the brand's        META_AD_LIBRARY_TOKEN
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from engine import social, sweep
from engine.brand.repo import BrandRepo, domain_of
from engine.clients import dnsx
from engine.clients.base import NotConfigured, UpstreamError, new_client, request
from engine.clients.search import Search
from engine.settings import get_settings
from engine.social_hosts import social_host

HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
DOMAIN_IN_TEXT = re.compile(r"\b((?:[a-z0-9-]{2,63}\.)+(?:com|net|org|co|io|xyz|top|site|online|app|info|biz|club|gh|africa|me|cc|tk|ml|ga|cf|gq|ng|ke|za))\b", re.I)
LURE = re.compile(r"\b(airdrop|giveaway|bonus|double|guaranteed|support|helpdesk|login|verify|claim|official|investment|profit|wallet)\b", re.I)
SCAM = re.compile(r"\b(scam|fake|phishing|impersonat\w*|fraud\w*|beware|warning|clone|spoof\w*|counterfeit)\b", re.I)


@dataclass
class Found:
    host: str
    source: str
    url: str | None = None
    trusted: bool = False          # evidence beyond the name (a search snippet naming the brand with lure words): skip the matcher requirement
    note: str | None = None


@dataclass
class Ctx:
    store: object
    watch: object
    repo: BrandRepo
    brand: dict
    inputs: dict
    http: httpx.AsyncClient
    records: object = dnsx.records
    exclude: set = field(default_factory=set)
    progress: object = None
    stats: dict = field(default_factory=dict)       # per-source counters that run() fills in (dns generated/checked, telegram checked)

    @property
    def terms(self) -> list[str]:
        return self.brand.get("seed_terms") or []


def clean_host(h: str | None) -> str | None:
    if not h:
        return None
    h = h.strip().lower().lstrip("*.").rstrip(".")
    return h if HOST.match(h) else None


# ---- sources -------------------------------------------------------------------------

async def src_dns(ctx: Ctx) -> list[Found]:
    domains = ctx.inputs["official_domains"] or ([domain_of(ctx.brand["website"])] if ctx.brand.get("website") else [])
    if not domains:
        raise NotConfigured("the brand has no domain")
    def prog(st):
        ctx.stats["dns_sweep"] = dict(st)
        if ctx.progress:
            ctx.progress()
    hosts, st = await sweep.dns_lookalike_hosts(domains, ctx.terms, ctx.exclude, ctx.records, progress=prog)
    ctx.stats["dns_sweep"] = st
    return [Found(h, "dns_sweep") for h in hosts]


async def src_ct_history(ctx: Ctx) -> list[Found]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
    seen: dict[str, str] = {}
    errors = 0
    for term in ctx.terms[:4]:
        try:
            res = await request(ctx.http, "GET", "https://crt.sh/", params={"q": f"%{term}%", "output": "json"}, timeout=30, retries=0)
            rows = res.json() if res.status_code == 200 and res.text.strip().startswith("[") else None
        except (UpstreamError, ValueError):
            rows = None
        if rows is None:
            errors += 1
            break                                  # crt.sh is down or busy: do not make the scan wait for every term
        for r in rows[:3000]:
            issued = (r.get("not_before") or "")[:10]
            if issued < cutoff:
                continue
            for name in (r.get("name_value") or "").split("\n"):
                if (h := clean_host(name)):
                    seen.setdefault(h, issued)
    if errors and not seen:
        raise UpstreamError("crt.sh did not answer (it is often slow or busy). Try again later.")
    return [Found(h, "ct_history", note=f"certificate issued {d}") for h, d in seen.items()]


async def src_telegram(ctx: Ctx) -> list[Found]:
    def prog(st):
        ctx.stats["telegram_handles"] = {"checked": st["checked"], "existing": st["existing"]}
        if ctx.progress:
            ctx.progress()
    st = await social.telegram_lookalikes(ctx.http, ctx.repo, ctx.brand["id"], progress=prog)
    ctx.stats["telegram_handles"] = {"checked": st["checked"], "existing": st["existing"]}
    return [Found(f"t.me/{p['handle'].lower()}", "telegram_handles", url=f"https://t.me/{p['handle']}", trusted=True,
                  note=f"{p['kind']} “{p['title']}”" + (f", {p['audience']:,} {'subscribers' if p['kind'] == 'channel' else 'members'}" if p.get("audience") else ""))
            for p in st["found"]]


async def src_urlscan(ctx: Ctx) -> list[Found]:
    key = get_settings().urlscan_api_key
    if not key:
        raise NotConfigured("URLSCAN_API_KEY is not set (urlscan.io does not allow anonymous wildcard searches)")
    found: dict[str, Found] = {}
    for term in ctx.terms[:3]:
        res = await request(ctx.http, "GET", "https://urlscan.io/api/v1/search/", headers={"API-Key": key},
                            params={"q": f"page.domain:*{term}* AND date:>now-30d", "size": 100}, retries=1)
        if res.status_code == 429:
            raise UpstreamError("urlscan rate limit reached")
        if res.status_code >= 400:
            raise UpstreamError(f"urlscan {res.status_code}: {res.text[:120]}")
        for r in res.json().get("results", []):
            page = r.get("page") or {}
            if (h := clean_host(page.get("domain"))):
                found.setdefault(h, Found(h, "urlscan", url=page.get("url"), note=f"scanned {str(r.get('task', {}).get('time', ''))[:10]}"))
    return list(found.values())


async def src_web(ctx: Ctx) -> list[Found]:
    search = Search(ctx.http)
    name = ctx.brand["display_name"]
    out: dict[str, Found] = {}
    for q in (f'"{name}" airdrop OR giveaway OR bonus OR "customer support"', f'"{name}" official login'):
        for r in await search.web(q, 15):
            h = clean_host(urlparse(r.get("url") or "").hostname)
            blob = f"{r.get('title') or ''} {r.get('snippet') or ''}"
            if h and name.lower() in blob.lower() and LURE.search(blob):
                out.setdefault(h, Found(h, "web_search", url=r["url"], trusted=True, note=(r.get("title") or "")[:100]))
    return list(out.values())


async def src_news(ctx: Ctx) -> list[Found]:
    search = Search(ctx.http)
    name, n = ctx.brand["display_name"], 0
    out: dict[str, Found] = {}
    for r in await search.news(f'"{name}" scam OR fake OR phishing OR impersonation OR warning', 15):
        blob = f"{r.get('title') or ''} {r.get('snippet') or ''}"
        if name.lower() not in blob.lower() or not SCAM.search(blob):
            continue
        n += 1
        ctx.store.db.execute("INSERT INTO signals (brand_id, kind, source, ref, data, created_at) VALUES (?,?,?,?,?,?)",
                             (ctx.brand["id"], "press_warning", "news_mentions", r.get("url"), json.dumps(r), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        for d in DOMAIN_IN_TEXT.findall(blob):
            if (h := clean_host(d)):
                out.setdefault(h, Found(h, "news_mentions", note=f"named in: {(r.get('title') or '')[:80]}"))
    ctx.stats["news_mentions"] = {"articles": n}
    return list(out.values())


async def src_ads(ctx: Ctx) -> list[Found]:
    token = get_settings().meta_ad_library_token
    if not token:
        raise NotConfigured("META_AD_LIBRARY_TOKEN is not set")
    res = await request(ctx.http, "GET", "https://graph.facebook.com/v19.0/ads_archive", retries=1, params={
        "search_terms": ctx.brand["display_name"], "ad_reached_countries": '["GH"]', "ad_active_status": "ACTIVE", "limit": 50, "access_token": token,
        "fields": "page_name,ad_creative_link_captions,ad_creative_link_titles,ad_snapshot_url"})
    if res.status_code >= 400:
        raise UpstreamError(f"Meta Ad Library {res.status_code}: {res.text[:160]}")
    out: dict[str, Found] = {}
    own = {d.lower() for d in ctx.inputs["official_domains"]}
    for ad in res.json().get("data", []):
        for cap in ad.get("ad_creative_link_captions") or []:
            h = clean_host(cap)
            if h and h not in own:
                out.setdefault(h, Found(h, "meta_ads", trusted=True, note=f"ad by page “{ad.get('page_name')}”"))
    return list(out.values())


SOURCES = {
    "dns_sweep": ("Look-alike domains", "Variants of your domain that exist in DNS", None, src_dns),
    "ct_history": ("Certificate history", "Certificates issued this year containing your names (crt.sh)", None, src_ct_history),
    "telegram_handles": ("Telegram accounts", "Look-alike channels and accounts on Telegram's public pages", None, src_telegram),
    "urlscan": ("urlscan.io", "Recent public scans of domains containing your names", "URLSCAN_API_KEY", src_urlscan),
    "web_search": ("Web search", "Fake login and giveaway pages found by search", "SEARCH_API_KEY", src_web),
    "news_mentions": ("Press warnings", "News naming your brand with scam wording, and domains it cites", "SEARCH_API_KEY", src_news),
    "meta_ads": ("Meta ads", "Ads using your name from pages that are not yours", "META_AD_LIBRARY_TOKEN", src_ads),
}   # name: (label, description, key it needs, function). A Found is injected if it is `trusted` or the matcher flags it.


def source_catalog(store) -> list[dict]:
    """For the settings page: every source, whether its key is set, and whether an admin switched it off."""
    s = get_settings()
    keys = {"URLSCAN_API_KEY": bool(s.urlscan_api_key), "SEARCH_API_KEY": bool(s.search_api_key), "META_AD_LIBRARY_TOKEN": bool(s.meta_ad_library_token)}
    off = {r["collector"] for r in store.db.execute("SELECT collector FROM source_toggles WHERE enabled=FALSE")}
    return [{"id": k, "label": v[0], "description": v[1], "needs_key": v[2], "configured": (keys[v[2]] if v[2] else True), "enabled": k not in off}
            for k, v in SOURCES.items()]


# ---- orchestration -------------------------------------------------------------------

async def run_discovery(store, watch, brand_id: str, progress=None, records=dnsx.records, only: list[str] | None = None,
                        max_per_source: int = 30, max_total: int = 80, http: httpx.AsyncClient | None = None) -> dict:
    """Run every enabled source, inject what they find into the pipeline, and return the per-source results."""
    repo = BrandRepo(store)
    d = repo.detection_inputs(brand_id)
    brand = d["brand"]
    if watch.brand.brand_id != brand_id:
        raise RuntimeError("Detection is not watching this brand. Start protection first.")
    domains = d["official_domains"]
    exclude = set(domains) | set(d["allowlist_domains"])
    disabled = {r["collector"] for r in store.db.execute("SELECT collector FROM source_toggles WHERE enabled=FALSE")}
    names = [n for n in SOURCES if not only or n in only]
    state = {"phase": "running", "sources": {n: {"label": SOURCES[n][0], "status": "pending"} for n in names},
             "generated": 0, "checked": 0, "registered": 0, "queued": 0, "skipped_known": 0}

    def report():
        dns = ctx.stats.get("dns_sweep") or {}
        state.update(generated=dns.get("generated", 0), checked=dns.get("checked", 0), registered=dns.get("registered", 0))
        for n, st in ctx.stats.items():
            state["sources"].get(n, {}).update(st)
        if progress:
            progress(json.loads(json.dumps(state)))

    own = http or new_client(timeout=30)
    async with own as client:
        ctx = Ctx(store, watch, repo, brand, d, client, records, exclude, report)
        total = 0
        for name in names:
            label, _desc, _key, fn = SOURCES[name][:4]
            entry = state["sources"][name]
            if name in disabled:
                entry.update(status="skipped", reason="switched off in Settings")
                report()
                continue
            entry["status"] = "running"
            report()
            try:
                found = await fn(ctx)
            except NotConfigured as e:
                entry.update(status="skipped", reason=str(e))
            except Exception as e:  # noqa: BLE001  one source never stops the scan
                entry.update(status="error", reason=str(e)[:200])
            else:
                queued = known = 0
                for f in sorted(found, key=lambda f: -(watch.matcher.match(f.host).priority if watch.matcher.match(f.host) else (50 if f.trusted else 0))):
                    if total >= max_total or queued >= max_per_source:
                        break
                    h = f.host
                    if not social_host(f"https://{h}") and any(h == x or h.endswith("." + x) for x in exclude):
                        continue                                  # the brand's own domains and allow-listed partners
                    if not f.trusted and not (watch.matcher.match(h) or watch.social_candidate(h)):
                        continue                                  # no evidence beyond a similar name, and the matcher does not agree
                    tid = watch.inject(f.url or f"https://{h}/", h, source=f.source)
                    if tid is None:
                        known += 1
                    else:
                        queued += 1
                        total += 1
                entry.update(status="ok", found=len(found), queued=queued, already_tracked=known)
                state["queued"] += queued
                state["skipped_known"] += known
            report()
    state["phase"] = "done"
    report()
    store.audit("system", "discovery_run", brand_id, {k: state[k] for k in ("queued", "skipped_known", "generated", "registered")})
    return json.loads(json.dumps(state))
