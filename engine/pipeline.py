"""Shared hit handling for every hostname source (CT poller, CertStream, NRD lists)."""
import json
import time
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from engine.matcher import Matcher


class Seen:
    """Bounded set: the same hostname shows up in many certs and many logs."""

    def __init__(self, size: int = 500_000):
        self.size = size
        self.items: OrderedDict[str, None] = OrderedDict()

    def add(self, key: str) -> bool:
        if key in self.items:
            return False
        self.items[key] = None
        if len(self.items) > self.size:
            self.items.popitem(last=False)
        return True


class Pipeline:
    def __init__(self, matcher: Matcher, out: Path, on_hit=None):
        self.matcher = matcher
        self.on_hit = on_hit
        self.seen = Seen()
        self.stats = {"certs": 0, "domains": 0, "unique_domains": 0, "hits": 0, "match_seconds": 0.0}
        out.parent.mkdir(parents=True, exist_ok=True)
        self.sink = out.open("a")

    def handle(self, domains: list[str], meta: dict) -> None:
        self.stats["certs"] += 1
        self.stats["domains"] += len(domains)
        t0 = time.perf_counter()
        for host in domains:
            # *.site, www.site and site are one finding.
            key = host.lower().removeprefix("*.").removeprefix("www.")
            if not self.seen.add(key):
                continue
            self.stats["unique_domains"] += 1
            cand = self.matcher.match(host)
            if cand is None:
                continue
            self.stats["hits"] += 1
            record = {
                "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "brand_id": self.matcher.brand.brand_id,
                **meta,
                **asdict(cand),
            }
            self.sink.write(json.dumps(record) + "\n")
            self.sink.flush()
            if self.on_hit:
                self.on_hit(record)
            print(f"HIT p={cand.priority:3d} {cand.display:50s} {','.join(cand.reasons)}", flush=True)
        self.stats["match_seconds"] += time.perf_counter() - t0

    def summary(self, elapsed: float) -> dict:
        s = self.stats
        return {**s, "elapsed_s": round(elapsed, 1),
                "certs_per_s": round(s["certs"] / max(elapsed, 1e-9), 1),
                "match_us_per_domain": round(1e6 * s["match_seconds"] / max(s["unique_domains"], 1), 1)}
