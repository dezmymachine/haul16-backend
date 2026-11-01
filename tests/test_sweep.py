"""Look-alike sweep: candidate generation and a full run with a fake resolver (no network)."""
import asyncio

import pytest

from engine.brand.repo import BrandRepo
from engine.matcher import Brand
from engine.sweep import generate_candidates, run_sweep, split_domain
from engine.worker import Watch


def test_split_domain_handles_multi_label_suffixes():
    assert split_domain("https://www.acmepay.com.gh/x") == ("acmepay", "com.gh")
    assert split_domain("digba-tech.com") == ("digba-tech", "com")


def test_candidates_cover_the_main_techniques_and_exclude_the_brand():
    c = generate_candidates("acmepay.com", ["acmepay"], limit=2000)
    assert "acmepay.com" not in c and len(c) == len(set(c))
    assert "acmepy.com" in c                       # omission
    assert "acmepaay.com" in c                     # repetition
    assert "camepay.com" in c                      # transposition
    assert "acmepay.net" in c and "acmepay.xyz" in c   # other TLDs
    assert "acmepay-login.com" in c and "support-acmepay.com" in c   # lure words
    assert "a-cmepay.com" in c                     # hyphen
    assert "acrnepay.com" in c                     # m -> rn
    assert any(h.startswith("xn--") for h in c)    # unicode homoglyphs are sent as punycode
    assert all("." in h and h == h.lower() for h in c)


def test_candidates_respect_the_cap_and_the_exclusions():
    assert len(generate_candidates("acmepay.com", limit=50)) == 50
    c = generate_candidates("acmepay.com", exclude={"acmepay.net", "https://www.acmepay.xyz/"}, limit=2000)
    assert "acmepay.net" not in c and "acmepay.xyz" not in c


def test_unusable_labels_are_dropped():
    assert all(len(h.split(".")[0]) <= 63 for h in generate_candidates("a" * 60 + ".com", limit=2000))


@pytest.fixture()
def watch(store):
    repo = BrandRepo(store)
    b = repo.create("Acme Pay", "acmepay.com")
    repo.upsert_fact(b["id"], "domain", "acmepay.com", collector="user", origin="user", confirmed=True)
    store.db.execute("UPDATE brands SET status='ready', is_active=TRUE WHERE id=?", (b["id"],))
    brand = Brand(brand_id=b["id"], display_name="Acme Pay", seed_terms=["acmepay"], official_domains=["acmepay.com"])
    return Watch(brand, store, workers=1, enrich_hosts=False), b["id"], store


def test_sweep_queues_only_registered_lookalikes(watch):
    w, bid, store = watch
    registered = {"acmepay-login.com", "acmepay.net", "acmepy.com", "totally-unrelated.com"}

    async def fake_records(host, rtype):
        return ["203.0.113.5"] if host in registered and rtype == "A" else []

    seen = []
    result = asyncio.run(run_sweep(store, w, bid, progress=seen.append, records=fake_records))
    hosts = {r["hostname"] for r in store.threats(bid)}
    assert hosts == {"acmepay-login.com", "acmepay.net", "acmepy.com"}      # the unrelated one was never a candidate
    assert result["phase"] == "done" and result["registered"] == 3 and result["queued"] == 3 and result["checked"] == result["generated"] > 100
    assert seen and seen[-1]["phase"] == "done"
    assert w.queue.qsize() == 3
    sources = {r["source"] for r in store.threats(bid)}
    assert sources == {"lookalike_sweep"}
    again = asyncio.run(run_sweep(store, w, bid, records=fake_records))      # a second sweep does not duplicate known threats
    assert again["queued"] == 0 and again["skipped_known"] == 3


def test_sweep_refuses_a_brand_detection_is_not_watching(watch):
    w, bid, store = watch
    other = BrandRepo(store).create("Other Co", "other.test")["id"]
    with pytest.raises(RuntimeError, match="Start protection"):
        asyncio.run(run_sweep(store, w, other))
