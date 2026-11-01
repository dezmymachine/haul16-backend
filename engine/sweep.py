"""Look-alike domain sweep: guess the domains a scammer would register, check which exist, and push those through the pipeline.

generate_candidates() builds typo, homoglyph, lure-word, hyphen and TLD variants of the brand's domain. run_sweep() resolves them over
DNS (no API key), keeps the ones that exist and that the matcher flags, and injects the strongest into the same capture and scoring
pipeline that Certificate Transparency hits use, so cases and drafted notices work unchanged.
"""
import asyncio
import re
from itertools import zip_longest

import idna

from engine.brand.repo import BrandRepo, domain_of
from engine.clients import dnsx
from engine.normalize import ASCII_LOOKALIKES, CONFUSABLES, MULTI_SUFFIXES

TLDS = ["com", "net", "org", "co", "io", "xyz", "top", "site", "online", "app", "info", "biz", "club", "com.gh", "gh", "africa"]
SWEEP_LURES = ["login", "support", "help", "wallet", "secure", "verify", "official", "app", "pay", "airdrop", "bonus", "claim", "ghana", "gh", "online", "portal", "customer", "care"]
KEYBOARD = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh", "u": "yij", "i": "uok", "o": "ipl", "p": "ol",
    "a": "qsz", "s": "awdx", "d": "sefc", "f": "drgv", "g": "fthb", "h": "gyjn", "j": "hukm", "k": "jil", "l": "ko",
    "z": "ax", "x": "zsc", "c": "xdv", "v": "cfb", "b": "vgn", "n": "bhm", "m": "nj",
}
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def split_domain(domain: str) -> tuple[str, str]:
    """('acmepay', 'com') or ('acmepay', 'com.gh')."""
    d = domain_of(domain)
    for suf in sorted(MULTI_SUFFIXES, key=len, reverse=True):
        if d.endswith("." + suf):
            return d[: -len(suf) - 1].split(".")[-1], suf
    label, _, tld = d.partition(".")
    return label, tld.split(".", 1)[-1] if "." in tld else tld


def _as_host(label: str, tld: str) -> str | None:
    """A DNS-safe hostname for a label, using punycode when it has non-ASCII characters. None when it is not valid."""
    try:
        ascii_label = idna.encode(label, uts46=False).decode() if not label.isascii() else label
    except idna.IDNAError:
        return None
    return f"{ascii_label}.{tld}" if LABEL.match(ascii_label) or ascii_label.startswith("xn--") else None


def label_variants(label: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for k in ("omission", "repetition", "transposition", "replacement", "homoglyph", "hyphen", "lure")}
    for i in range(len(label)):
        out["omission"].append(label[:i] + label[i + 1:])
        out["repetition"].append(label[:i] + label[i] + label[i:])
        for k in KEYBOARD.get(label[i], ""):
            out["replacement"].append(label[:i] + k + label[i + 1:])
        if i < len(label) - 1 and label[i] != label[i + 1]:
            out["transposition"].append(label[:i] + label[i + 1] + label[i] + label[i + 2:])
        if 0 < i < len(label):
            out["hyphen"].append(label[:i] + "-" + label[i:])
    reverse: dict[str, list[str]] = {}
    for uni, asc in CONFUSABLES.items():
        reverse.setdefault(asc, []).append(uni)
    for seq, rep in ASCII_LOOKALIKES:
        reverse.setdefault(rep, []).append(seq)         # skeleton rule "rn" -> "m" gives the look-alike "m" -> "rn"
    for i, ch in enumerate(label):
        for sub in reverse.get(ch, []):
            out["homoglyph"].append(label[:i] + sub + label[i + 1:])
    for w in SWEEP_LURES:
        out["lure"] += [f"{label}-{w}", f"{w}-{label}", f"{label}{w}"]
    return out


def generate_candidates(domain: str, seed_terms: list[str] = (), exclude: set[str] = frozenset(), limit: int = 600) -> list[str]:
    """Look-alike hostnames for a brand domain, interleaved across techniques so a cap keeps variety. Never includes `exclude`.

    Only variants of the domain itself. `seed_terms` is accepted but not swept as names: `<first word>.com` is usually a different,
    legitimate business with the same word, and flagging it risks a wrongful takedown."""
    label, suffix = split_domain(domain)
    own = {domain_of(domain)} | {domain_of(e) for e in exclude}
    groups: list[list[str]] = []
    for kind, labels in label_variants(label).items():
        hosts = []
        for lab in dict.fromkeys(labels):
            if lab and lab != label:
                for tld in dict.fromkeys([suffix, "com", "net", "xyz", "top"] if kind == "lure" else [suffix]):
                    h = _as_host(lab, tld)
                    if h:
                        hosts.append(h)
        groups.append(hosts)
    groups.append([f"{label}.{t}" for t in TLDS if t != suffix])                                   # same name, other TLD
    seen, out = set(own), []
    for batch in zip_longest(*groups):
        for h in batch:
            if h and h not in seen:
                seen.add(h)
                out.append(h)
                if len(out) >= limit:
                    return out
    return out


async def resolves(host: str, records=dnsx.records) -> bool:
    """Registered, as far as DNS can tell: it has an address or nameservers."""
    return bool(await records(host, "A") or await records(host, "AAAA") or await records(host, "NS"))


async def dns_lookalike_hosts(domains: list[str], seed_terms: list[str], exclude: set[str], records=dnsx.records, concurrency: int = 25, progress=None) -> tuple[list[str], dict]:
    """Generate variants of every domain and return the ones that resolve, plus counters {generated, checked, registered}."""
    candidates: list[str] = []
    for dom in domains:
        for h in generate_candidates(dom, seed_terms, exclude, limit=600):
            if h not in candidates:
                candidates.append(h)
    state = {"generated": len(candidates), "checked": 0, "registered": 0}
    if progress:
        progress(dict(state))
    sem = asyncio.Semaphore(concurrency)
    live: list[str] = []

    async def check(h: str):
        async with sem:
            try:
                ok = await resolves(h, records)
            except Exception:  # noqa: BLE001  a resolver hiccup is "unknown", not a failure of the sweep
                ok = False
        state["checked"] += 1
        if ok:
            live.append(h)
            state["registered"] = len(live)
        if progress and state["checked"] % 20 == 0:
            progress(dict(state))

    await asyncio.gather(*(check(h) for h in candidates))
    if progress:
        progress(dict(state))
    return live, state


async def run_sweep(store, watch, brand_id: str, progress=None, records=dnsx.records, max_inject: int = 40, concurrency: int = 25) -> dict:
    """Generate, resolve, and inject. `records` is injectable so tests need no network."""
    repo = BrandRepo(store)
    d = repo.detection_inputs(brand_id)
    brand = d["brand"]
    if watch.brand.brand_id != brand_id:
        raise RuntimeError("Detection is not watching this brand. Start protection first.")
    domains = d["official_domains"] or ([domain_of(brand["website"])] if brand.get("website") else [])
    if not domains:
        raise RuntimeError("The brand has no domain to look for look-alikes of.")
    exclude = set(domains) | set(d["allowlist_domains"])
    state = {"phase": "generating", "generated": 0, "checked": 0, "registered": 0, "queued": 0, "skipped_known": 0}

    def report():
        if progress:
            progress(dict(state))

    candidates: list[str] = []
    for dom in domains:
        for h in generate_candidates(dom, brand.get("seed_terms") or [], exclude, limit=600):
            if h not in candidates:
                candidates.append(h)
    state.update(phase="checking", generated=len(candidates))
    report()

    sem = asyncio.Semaphore(concurrency)
    live: list[str] = []

    async def check(h: str):
        async with sem:
            try:
                ok = await resolves(h, records)
            except Exception:  # noqa: BLE001  a resolver hiccup is "unknown", not a failure of the sweep
                ok = False
        state["checked"] += 1
        if ok:
            live.append(h)
            state["registered"] = len(live)
        if state["checked"] % 20 == 0:
            report()

    await asyncio.gather(*(check(h) for h in candidates))

    flagged = []
    for h in live:
        cand = watch.matcher.match(h)
        if cand:
            flagged.append((cand.priority, h))
    flagged.sort(reverse=True)
    for _, h in flagged[:max_inject]:
        if watch.inject(f"https://{h}/", h, source="lookalike_sweep") is None:
            state["skipped_known"] += 1                 # already tracked
        else:
            state["queued"] += 1
    state["phase"] = "done"
    report()
    store.audit("system", "lookalike_sweep", brand_id, dict(state))
    return dict(state)
