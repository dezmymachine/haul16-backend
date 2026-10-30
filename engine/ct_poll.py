"""Read Certificate Transparency logs directly (RFC 6962 get-sth / get-entries).

No third-party stream: we follow the head of each public log ourselves.

  uv run python -m engine.ct_poll                      # follow logs forever
  uv run python -m engine.ct_poll --duration 60        # one minute, then report
  uv run python -m engine.ct_poll --seed somebrand     # override seeds (local testing only)

Hits are appended to data/hits.jsonl. Nothing is sent anywhere.
"""
import argparse
import asyncio
import base64
import json
import struct
import time
from pathlib import Path

import httpx
from cryptography import x509

from engine.matcher import Brand, Matcher
from engine.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]

# Usable RFC 6962 logs for certs expiring in late 2026 / 2027 (Google log list v3, 30 Oct 2026).
LOGS = {
    "google_argon2026h2": "https://ct.googleapis.com/logs/us1/argon2026h2/",
    "google_xenon2026h2": "https://ct.googleapis.com/logs/eu1/xenon2026h2/",
    "cloudflare_nimbus2026": "https://ct.cloudflare.com/logs/nimbus2026/",
    "sectigo_elephant2026h2": "https://elephant2026h2.ct.sectigo.com/",
    "sectigo_tiger2026h2": "https://tiger2026h2.ct.sectigo.com/",
}
BATCH = 256          # logs cap batch size anyway; we take what they return
PARALLEL = 8         # concurrent get-entries requests per log


def _u24(b: bytes, off: int) -> tuple[int, int]:
    return int.from_bytes(b[off:off + 3], "big"), off + 3


def cert_domains(entry: dict) -> tuple[list[str], str | None]:
    """Hostnames (SAN dNSNames, else CN) and issuer org from one log entry."""
    leaf = base64.b64decode(entry["leaf_input"])
    entry_type = struct.unpack(">H", leaf[10:12])[0]
    if entry_type == 0:                       # x509_entry: cert is in the leaf
        n, off = _u24(leaf, 12)
        der = leaf[off:off + n]
    else:                                     # precert_entry: full precert is first in extra_data
        extra = base64.b64decode(entry["extra_data"])
        n, off = _u24(extra, 0)
        der = extra[off:off + n]
    cert = x509.load_der_x509_certificate(der)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = san.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = [a.value for a in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)]
    orgs = cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
    return [str(n) for n in names], (str(orgs[0].value) if orgs else None)


async def follow(client: httpx.AsyncClient, name: str, url: str, pipe: Pipeline, stop_at: float | None,
                 lag: dict) -> None:
    sth = (await client.get(url + "ct/v1/get-sth")).json()
    pos = sth["tree_size"]                    # start at the head: we want what is new
    print(f"{name}: following from entry {pos:,}", flush=True)
    while stop_at is None or time.monotonic() < stop_at:
        try:
            head = (await client.get(url + "ct/v1/get-sth")).json()["tree_size"]
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(2)
            continue
        # Google's frontends can briefly serve an older STH; never step backwards.
        lag[name] = max(0, head - pos)
        if head <= pos:
            await asyncio.sleep(2)
            continue
        # Fetch a window of batches in parallel. Logs may return fewer than asked.
        starts = list(range(pos, min(head, pos + BATCH * PARALLEL), BATCH))
        reqs = [client.get(url + "ct/v1/get-entries", params={"start": s, "end": min(s + BATCH, head) - 1})
                for s in starts]
        got = await asyncio.gather(*reqs, return_exceptions=True)
        advanced = pos
        for s, r in zip(starts, got):
            if isinstance(r, Exception) or r.status_code != 200 or s != advanced:
                break                          # keep order: stop at the first gap
            entries = r.json().get("entries", [])
            for i, e in enumerate(entries):
                try:
                    domains, issuer = cert_domains(e)
                except Exception:
                    continue
                pipe.handle(domains, {"source": "certificate_transparency", "ct_log": name,
                                      "ct_index": s + i, "issuer": issuer})
            advanced = s + len(entries)
            if len(entries) < min(BATCH, head - s):
                break
        if advanced == pos:
            await asyncio.sleep(1)
        pos = advanced


async def run(pipe: Pipeline, logs: dict, duration: float | None) -> None:
    stop_at = time.monotonic() + duration if duration else None
    lag: dict[str, int] = {}
    started = time.monotonic()

    async def report():
        while stop_at is None or time.monotonic() < stop_at:
            await asyncio.sleep(10)
            el = time.monotonic() - started
            s = pipe.stats
            print(f"[{el:5.0f}s] certs={s['certs']:,} ({s['certs']/el:.0f}/s) unique_domains="
                  f"{s['unique_domains']:,} hits={s['hits']} backlog={lag}", flush=True)

    limits = httpx.Limits(max_connections=PARALLEL * len(logs))
    async with httpx.AsyncClient(timeout=20, limits=limits, http2=False,
                                 headers={"User-Agent": "haul16-ct-monitor/0.1"}) as client:
        tasks = [follow(client, n, u, pipe, stop_at, lag) for n, u in logs.items()]
        await asyncio.gather(report(), *tasks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default=str(ROOT / "brand.yaml"))
    ap.add_argument("--seed", action="append", help="override seed terms (local testing)")
    ap.add_argument("--duration", type=float, help="seconds to run, then print a summary")
    ap.add_argument("--log", action="append", choices=sorted(LOGS), help="limit to these logs")
    ap.add_argument("--out", default=str(ROOT / "data" / "hits.jsonl"))
    args = ap.parse_args()

    brand = Brand.load(args.brand)
    if args.seed:
        brand.seed_terms = [s.lower() for s in args.seed]
    pipe = Pipeline(Matcher(brand), Path(args.out))
    logs = {k: LOGS[k] for k in (args.log or LOGS)}
    print(f"watching for: {', '.join(brand.seed_terms)}  across {len(logs)} CT logs")

    t0 = time.monotonic()
    try:
        asyncio.run(run(pipe, logs, args.duration))
    except KeyboardInterrupt:
        pass
    print(json.dumps(pipe.summary(time.monotonic() - t0), indent=2))


if __name__ == "__main__":
    main()
