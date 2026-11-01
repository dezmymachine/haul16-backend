"""The full watch loop on fixtures: inject URLs, check threats, cases and campaign links."""
import asyncio
from pathlib import Path

from engine.matcher import Brand
from engine.worker import Watch

from tests.test_capture_score import server  # noqa: F401  (reuse the fixture web server)

ROOT = Path(__file__).resolve().parents[1]


def test_watch_links_campaign_and_skips_decoy(server, store):  # noqa: F811
    watch = Watch(Brand.load(ROOT / "brand.yaml"), store, allow_local=True, workers=1, enrich_hosts=False)

    async def go():
        a = watch.inject(f"{server}/clone/", "nkwaex-support.com")
        b = watch.inject(f"{server}/clone2/", "nkwaex-airdrop.top")
        c = watch.inject(f"{server}/decoy/", "nkwafarms.com")
        assert watch.inject(f"{server}/clone/", "nkwaex-support.com") is None   # dedupe
        results = {}
        while not watch.queue.empty():
            item = watch.queue.get_nowait()
            results[item[0]] = await watch.process(*item)
        return a, b, c, results

    a, b, c, r = asyncio.run(go())
    assert r[a]["confidence"] == "high"
    assert r[b]["confidence"] == "high"
    assert r[b]["case_id"] == r[a]["case_id"]                 # same operator, one campaign
    assert "phone:0240000101" in r[b]["linked_via"]
    assert r[c]["confidence"] == "low" and "case_id" not in r[c]

    actions = [row["action"] for row in store.db.execute("SELECT action FROM audit_log")]
    assert actions.count("case_opened") == 1
    assert "threat_linked_to_case" in actions

    # Notices are drafted per channel from evidence, never filed automatically.
    case = store.case(r[a]["case_id"])
    channels = {n["channel"] for n in case["notices"]}
    assert {"google_safe_browsing", "telco_mtn", "csa"} <= channels
    assert all(n["status"] == "drafted" for n in case["notices"])
    mtn = next(n for n in case["notices"] if n["channel"] == "telco_mtn")
    assert "0240000101" in mtn["body"] and mtn["recipient"] == "mmfraudteam.GH@mtn.com"
    assert "{" not in mtn["body"]                                 # every placeholder filled


def test_unreachable_is_retried(store):
    watch = Watch(Brand.load(ROOT / "brand.yaml"), store, workers=1, enrich_hosts=False)
    watch.retry_after = (0.05,)

    async def go():
        tid = watch.inject("http://nkwaex-not-up-yet.invalid/", "nkwaex-not-up-yet.invalid")
        first = await watch.process(*watch.queue.get_nowait())
        await asyncio.sleep(0.2)
        assert watch.queue.qsize() == 1                       # re-queued once
        second = await watch.process(*watch.queue.get_nowait())
        await asyncio.sleep(0.2)
        return first, second, watch.queue.qsize()

    first, second, left = asyncio.run(go())
    assert first["confidence"] == "unknown" and first["retry_in_s"] == 0.05
    assert "retry_in_s" not in second and left == 0           # gives up after the schedule
