"""The discovery scan: each source against mocked HTTP and a fake resolver. No network, no keys needed unless a test sets one."""
import asyncio
import json
from datetime import date, timedelta

import httpx
import pytest

from engine.brand.repo import BrandRepo
from engine.discovery import SOURCES, clean_host, run_discovery, source_catalog
from engine.matcher import Brand
from engine.settings import get_settings
from engine.worker import Watch

CHANNEL = '<div class="tgme_page_title"><span>Acme Pay Support</span></div><div class="tgme_page_extra">900 subscribers</div>'
UNUSED = '<meta property="og:title" content="Telegram: Contact"><div class="tgme_page_description">contact right away</div>'
RECENT, OLD = date.today().isoformat(), (date.today() - timedelta(days=500)).isoformat()


@pytest.fixture()
def watch(store):
    repo = BrandRepo(store)
    b = repo.create("Acme Pay", "acmepay.com")
    repo.upsert_fact(b["id"], "domain", "acmepay.com", collector="user", origin="user", confirmed=True)
    store.db.execute("UPDATE brands SET status='ready', is_active=TRUE WHERE id=?", (b["id"],))
    brand = Brand(brand_id=b["id"], display_name="Acme Pay", seed_terms=["acmepay"], official_domains=["acmepay.com"])
    return Watch(brand, store, workers=1, enrich_hosts=False), b["id"], store


def handler_for(routes):
    def handler(req: httpx.Request):
        for key, fn in routes.items():
            if key in str(req.url):
                return fn(req)
        return httpx.Response(404)
    return handler


def crtsh(req):
    rows = [{"name_value": "acmepay-login.net\n*.acmepay-login.net", "not_before": RECENT},
            {"name_value": "acmepay-old.com", "not_before": OLD},                         # too old: not this year
            {"name_value": "www.acmepay.com", "not_before": RECENT},                      # the brand's own domain
            {"name_value": "unrelated-shop.com", "not_before": RECENT}]                   # matches no brand name
    return httpx.Response(200, json=rows)


def telegram(req):
    return httpx.Response(200, text=CHANNEL if req.url.path == "/acmepay_support" else UNUSED)


async def no_dns(host, rtype):
    return ["203.0.113.9"] if host == "acmepay.net" and rtype == "A" else []


def run(watch, routes, **kw):
    w, bid, store = watch
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler_for(routes)))
    seen = []
    return asyncio.run(run_discovery(store, w, bid, progress=seen.append, records=no_dns, http=client, **kw)), seen, store, bid


def test_clean_host():
    assert clean_host("*.Acme-Pay.com.") == "acme-pay.com" and clean_host("not a host") is None and clean_host(None) is None


def test_keyless_sources_run_and_keyed_ones_skip_with_a_reason(watch):
    res, seen, store, bid = run(watch, {"crt.sh": crtsh, "t.me": telegram})
    s = res["sources"]
    assert s["dns_sweep"]["status"] == "ok" and s["dns_sweep"]["found"] == 1 and s["dns_sweep"]["queued"] == 1
    assert s["ct_history"]["status"] == "ok" and s["ct_history"]["found"] == 3 and s["ct_history"]["queued"] == 1     # only the recent, matching, non-own host
    assert s["telegram_handles"]["status"] == "ok" and s["telegram_handles"]["queued"] == 1
    for keyed in ("urlscan", "web_search", "news_mentions", "meta_ads"):
        assert s[keyed]["status"] == "skipped" and "not set" in s[keyed]["reason"], keyed
    threats = {t["hostname"]: t["source"] for t in store.threats(bid)}
    assert threats == {"acmepay.net": "dns_sweep", "acmepay-login.net": "ct_history", "t.me/acmepay_support": "telegram_handles"}
    assert res["queued"] == 3 and res["phase"] == "done" and seen[-1]["phase"] == "done"
    assert res["generated"] > 100 and res["checked"] == res["generated"]
    assert s["telegram_handles"]["checked"] > 30 and s["telegram_handles"]["existing"] == 1
    tg = next(t for t in store.threats(bid) if t["hostname"] == "t.me/acmepay_support")
    assert tg["stage_a"]["reasons"] == ["handle_lookalike"]                                                           # Stage A understood the handle


def test_one_failing_source_does_not_stop_the_others(watch):
    res, *_ = run(watch, {"crt.sh": lambda r: httpx.Response(502, text="bad gateway"), "t.me": telegram})
    assert res["sources"]["ct_history"]["status"] == "error" and "crt.sh" in res["sources"]["ct_history"]["reason"]
    assert res["sources"]["dns_sweep"]["status"] == "ok" and res["sources"]["telegram_handles"]["status"] == "ok"


def test_a_source_switched_off_is_skipped(watch):
    w, bid, store = watch
    store.db.execute("INSERT INTO source_toggles (collector, enabled) VALUES ('ct_history', FALSE)")
    res, *_ = run(watch, {"t.me": telegram})
    assert res["sources"]["ct_history"] == {"label": "Certificate history", "status": "skipped", "reason": "switched off in Settings"}
    cat = {c["id"]: c for c in source_catalog(store)}
    assert cat["ct_history"]["enabled"] is False and cat["dns_sweep"]["enabled"] is True and cat["urlscan"]["configured"] is False


def test_rerun_does_not_duplicate_known_threats(watch):
    run(watch, {"crt.sh": crtsh, "t.me": telegram})
    res, *_ = run(watch, {"crt.sh": crtsh, "t.me": telegram})
    assert res["queued"] == 0 and res["skipped_known"] == 3


def test_keyed_sources_with_mocked_vendors(watch, monkeypatch):
    for k, v in {"URLSCAN_API_KEY": "u", "SEARCH_API_KEY": "s", "META_AD_LIBRARY_TOKEN": "m"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    web = lambda r: httpx.Response(200, json={"web": {"results": [   # noqa: E731
        {"title": "Acme Pay airdrop: claim now", "url": "https://claim-bonus.top/acme", "description": "Acme Pay giveaway, claim your bonus"},
        {"title": "Acme Pay official site", "url": "https://acmepay.com/", "description": "Acme Pay"},
        {"title": "Cooking blog", "url": "https://tasty.example/", "description": "recipes"}]}})
    news = lambda r: httpx.Response(200, json={"results": [   # noqa: E731
        {"title": "Acme Pay warns of fake site acmepay-secure.xyz", "url": "https://news.test/a", "description": "scam warning", "age": "1d", "meta_url": {"hostname": "news.test"}}]})
    routes = {"urlscan.io": lambda r: httpx.Response(200, json={"results": [{"page": {"domain": "acmepay-pay.com", "url": "https://acmepay-pay.com/x"}, "task": {"time": "2026-10-30T10:00:00Z"}}]}),
              "news/search": news, "web/search": web,
              "ads_archive": lambda r: httpx.Response(200, json={"data": [{"page_name": "Acme Giveaways", "ad_creative_link_captions": ["fastcash.club"]}]}),
              "crt.sh": crtsh, "t.me": telegram}
    try:
        res, _, store, bid = run(watch, routes)
    finally:
        get_settings.cache_clear()
    s = res["sources"]
    assert {k: s[k]["status"] for k in ("urlscan", "web_search", "news_mentions", "meta_ads")} == {"urlscan": "ok", "web_search": "ok", "news_mentions": "ok", "meta_ads": "ok"}
    hosts = {t["hostname"]: t["source"] for t in store.threats(bid)}
    assert hosts["acmepay-pay.com"] == "urlscan" and hosts["claim-bonus.top"] == "web_search" and hosts["fastcash.club"] == "meta_ads"
    assert hosts["acmepay-secure.xyz"] == "news_mentions"                                  # a domain the article named
    assert "acmepay.com" not in hosts and "tasty.example" not in hosts                      # own site and unrelated results are not threats
    sig = store.db.execute("SELECT kind, source FROM signals WHERE brand_id=?", (bid,)).fetchall()
    assert [(r["kind"], r["source"]) for r in sig] == [("press_warning", "news_mentions")]  # the article itself is kept as a signal


def test_refuses_a_brand_detection_is_not_watching(watch):
    w, _, store = watch
    other = BrandRepo(store).create("Other", "other.test")["id"]
    with pytest.raises(RuntimeError, match="Start protection"):
        asyncio.run(run_discovery(store, w, other))
