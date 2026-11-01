"""The HAUL16 watch loop: CT hits -> capture -> enrich -> score -> threat, case, draft notices.

  uv run python -m engine.worker                       # follow live CT logs for brand.yaml
  uv run python -m engine.worker --no-ct --inject http://127.0.0.1:8765/clone/ \
      --as-host nkwaex-support.com --allow-local       # push one URL through (fixtures, replay)

Nothing is filed or sent anywhere. Notices are drafts until a human approves them,
and "filing" is a dry run.
"""
import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from engine import ct_poll, notices
from engine.capture import capture
from engine.extract import Identifiers, unofficial
from engine.matcher import Brand, Candidate, Matcher
from engine.social_hosts import capture_urls, is_social, social_host
from engine.pipeline import Pipeline
from engine.rdap import enrich
from engine.score import score
from engine.settings import get_settings
from engine.store import Store

ROOT = Path(__file__).resolve().parents[1]
OPEN_CASE_AT = {"medium", "high"}          # low and unknown stay in the feed only
# Scammers often get the cert minutes before the page is up: retry unreachable hosts.
RETRY_AFTER_S = (120, 600, 1800)


class Watch:
    def __init__(self, brand: Brand, store: Store, allow_local: bool = False, workers: int = 2,
                 brand_cfg: dict | None = None, enrich_hosts: bool = True):
        self.brand = brand
        self.brand_cfg = brand_cfg or {}
        self.matcher = Matcher(brand)
        self.store = store
        self.allow_local = allow_local
        self.workers = workers
        self.enrich_hosts = enrich_hosts
        self.queue: asyncio.Queue = asyncio.Queue()
        self.attempts: dict[str, int] = {}
        self.retry_after = RETRY_AFTER_S
        self.subscribers: set[asyncio.Queue] = set()

    def set_brand(self, brand: Brand, cfg: dict) -> None:
        """Switch the brand this watcher detects for (the dossier changed or another brand was activated)."""
        self.brand, self.brand_cfg, self.matcher = brand, cfg, Matcher(brand)

    # ---- live events for the UI -------------------------------------------

    def publish(self, event: dict) -> None:
        for q in list(self.subscribers):
            if q.qsize() < 1000:
                q.put_nowait(event)

    # ---- intake ------------------------------------------------------------

    def on_hit(self, record: dict) -> None:
        """Called from the CT pipeline for every Stage A hit."""
        host = record["display"]
        tid = self.store.add_threat(self.brand.brand_id, host, None, record["source"],
                                    f"{record.get('ct_log')}#{record.get('ct_index')}", record)
        if tid:
            self.publish({"type": "hit", "threat_id": tid, "host": host, "priority": record["priority"],
                          "reasons": record["reasons"], "source": record["source"], "ct_log": record.get("ct_log")})
            self.queue.put_nowait((tid, host, None, self.allow_local))

    def inject(self, url: str, as_host: str | None = None, source: str = "manual",
               allow_local: bool | None = None) -> str | None:
        host = as_host or social_host(url) or urlparse(url).hostname or url
        cand = self.matcher.match(host) or self.social_candidate(host)
        tid = self.store.add_threat(self.brand.brand_id, host, url, source, None, asdict(cand) if cand else None)
        if tid:
            self.publish({"type": "hit", "threat_id": tid, "host": host, "priority": cand.priority if cand else 0,
                          "reasons": cand.reasons if cand else [], "source": source})
            self.queue.put_nowait((tid, host, url, self.allow_local if allow_local is None else allow_local))
        return tid

    def social_candidate(self, host: str) -> Candidate | None:
        """Stage A for a Telegram or WhatsApp link: does the handle contain one of the brand's names?"""
        if not is_social(host):
            return None
        words = [w for w in self.brand.seed_terms if len(w) >= 4]
        slug = host.split("/", 1)[1].lower().replace("_", "")
        hit = next((w for w in words if w in slug), None)
        if hit:
            return Candidate(hostname=host, display=host, seed=hit, reasons=["handle_lookalike"], priority=35)
        return Candidate(hostname=host, display=host, seed="", reasons=["social_link_submitted"], priority=10)

    async def assess_social(self, host: str, cap, enr: dict) -> dict:
        """Public-page facts about the account or group, observed only (never joined): Telegram preview, and any WhatsApp invite links the page shows."""
        from engine.clients import telegram, whatsapp
        from engine.clients.base import new_client
        try:
            async with new_client(timeout=20) as http:
                if host.startswith("t.me/"):
                    pv = await telegram.preview(http, host.split("/", 1)[1])
                    if pv:
                        enr = {**enr, "telegram": pv}
                codes = [c for c in (cap.identifiers or {}).get("whatsapp", []) if whatsapp.INVITE.search(f"chat.whatsapp.com/{c}")] if cap else []
                if host.startswith("chat.whatsapp.com/"):
                    codes.append(host.split("/", 1)[1])
                groups = []
                for code in list(dict.fromkeys(codes))[:5]:
                    g = await whatsapp.preview(http, code)
                    if g:
                        groups.append(g)
                if groups:
                    enr = {**enr, "whatsapp_groups": groups}
        except Exception:  # noqa: BLE001  assessment is extra evidence, never a reason to fail the capture
            pass
        return enr

    def recover(self) -> int:
        """Put threats left in 'queued' (a restart mid-pipeline) back on the capture queue. Makes the in-process queue durable."""
        rows = self.store.db.execute("SELECT id, hostname, url FROM threats WHERE brand_id=? AND status='queued'",
                                     (self.brand.brand_id,)).fetchall()
        for r in rows:
            self.queue.put_nowait((r["id"], r["hostname"], r["url"], self.allow_local))
        return len(rows)

    # ---- processing --------------------------------------------------------

    async def process(self, tid: str, host: str, url: str | None, allow_local: bool | None = None) -> dict:
        allow_local = self.allow_local if allow_local is None else allow_local
        social = is_social(host)
        urls = capture_urls(host) if social else ([url] if url else [f"https://{host}/", f"http://{host}/"])
        cap = None
        for u in urls:
            cap = await capture(u, allow_local=allow_local)
            if cap.html_sha256:
                break
        enr: dict = {"host": host, "platform": "telegram" if host.startswith("t.me/") else "whatsapp"} if social else {}
        if self.enrich_hosts and not social:
            try:
                enr = await enrich(host)
            except Exception as e:                       # enrichment is optional evidence
                enr = {"host": host, "error": repr(e)[:200]}
        if self.enrich_hosts:
            enr = await self.assess_social(host, cap, enr)
            self.store.save_enrichment(tid, enr)
        cand = self.matcher.match(host) or self.social_candidate(host)
        verdict = score(self.brand, cand, cap)
        self.store.save_result(tid, asdict(cap), asdict(verdict))

        result = {"threat_id": tid, "host": host, "confidence": verdict.confidence,
                  "score": verdict.score, "reasons": verdict.reason_codes}
        if verdict.confidence in OPEN_CASE_AT:
            ids = Identifiers(**cap.identifiers) if cap.identifiers else Identifiers()
            self.store.link_identifiers(tid, unofficial(ids, self.brand.official_numbers,
                                                        self.brand.official_wallets), ids.telegram)
            case_id, links = self.store.assign_case(tid, self.brand.brand_id,
                                                    f"Suspected clone of {self.brand.display_name}")
            drafts = notices.draft(self.store.threat(tid), self.brand, self.brand_cfg, case_id)
            # A fraud desk needs one report per number, not one per site that shows it.
            existing = {(n["channel"], n["subject"]) for n in self.store.notices(case_id)}
            drafts = [d for d in drafts if not (d["channel"].startswith("telco_")
                                                and (d["channel"], d["subject"]) in existing)]
            n = self.store.add_notices(case_id, drafts, tid)
            result.update(case_id=case_id, linked_via=[f"{l['kind']}:{l['value']}" for l in links],
                          notices_drafted=n)
        elif verdict.confidence == "unknown":
            n = self.attempts.get(tid, 0)
            if n < len(self.retry_after):
                self.attempts[tid] = n + 1
                result["retry_in_s"] = self.retry_after[n]
                asyncio.get_running_loop().call_later(
                    self.retry_after[n], self.queue.put_nowait, (tid, host, url, allow_local))
        print("SCORED " + json.dumps(result), flush=True)
        self.publish({"type": "scored", **result})
        if verdict.confidence in ("high", "medium"):
            from engine import alerts
            links = f" Joined case via {', '.join(result['linked_via'])}." if result.get("linked_via") else (" Opened a case." if result.get("case_id") else "")
            await alerts.notify(
                self.store, self.brand.brand_id, "finding", f"{verdict.confidence.title()}-confidence look-alike: {host}",
                f"Score {verdict.score}. {', '.join(verdict.reason_codes)}.{links} {result.get('notices_drafted', 0)} notice(s) drafted for approval.",
                f"/cases/{result['case_id']}" if result.get("case_id") else f"/threats/{tid}", verdict.confidence, ref=tid)
        return result

    async def capture_worker(self) -> None:
        while True:
            tid, host, url, allow_local = await self.queue.get()
            try:
                self.publish({"type": "capturing", "threat_id": tid, "host": host})
                await self.process(tid, host, url, allow_local)
            except Exception as e:                       # one bad page never stops the watch
                self.store.audit("system", "capture_failed", tid, {"error": repr(e)[:300]})
                print(f"ERROR {host}: {e!r}", flush=True)
            finally:
                self.queue.task_done()


def load_brand(path: str | Path) -> tuple[Brand, dict]:
    import yaml
    return Brand.load(path), yaml.safe_load(Path(path).read_text())


async def main_async(args) -> None:
    brand, cfg = load_brand(args.brand)
    watch = Watch(brand, Store(args.db), allow_local=args.allow_local, workers=args.workers, brand_cfg=cfg)
    for url in args.inject or []:
        watch.inject(url, args.as_host)
    workers = [asyncio.create_task(watch.capture_worker()) for _ in range(watch.workers)]

    if args.no_ct:
        await watch.queue.join()
    else:
        pipe = Pipeline(watch.matcher, ROOT / "data" / "hits.jsonl", on_hit=watch.on_hit)
        print(f"watching CT logs for: {', '.join(brand.seed_terms)}", flush=True)
        await ct_poll.run(pipe, ct_poll.LOGS, args.duration)
        await watch.queue.join()
    for w in workers:
        w.cancel()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    ap.add_argument("--db", default=get_settings().database_url, help="Postgres URL")
    ap.add_argument("--inject", action="append", help="URL to push through capture and scoring")
    ap.add_argument("--as-host", help="hostname to score the injected URL as (fixtures, replay)")
    ap.add_argument("--allow-local", action="store_true", help="permit localhost captures (fixtures only)")
    ap.add_argument("--no-ct", action="store_true", help="do not follow CT logs")
    ap.add_argument("--duration", type=float, help="seconds to follow CT logs")
    ap.add_argument("--workers", type=int, default=2)
    try:
        asyncio.run(main_async(ap.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
