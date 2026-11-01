"""Account look-alikes on Telegram: guess handles a scammer would use for the brand, check which exist (public pages only),
and judge whether they present themselves as the brand. Never joins, messages or logs in."""
import asyncio
import re

from engine.brand.repo import BrandRepo
from engine.clients import telegram
from engine.sweep import KEYBOARD

POLITE_DELAY = 0.15          # seconds between t.me requests per worker; tests set it to 0
SUFFIXES = ["official", "_official", "support", "_support", "help", "_help", "admin", "_admin", "ghana", "_gh", "gh", "group", "_group", "signals", "airdrop", "_airdrop",
            "care", "_care", "team", "_team", "news", "vip", "invest", "wallet", "_ceo"]
PREFIXES = ["the", "real", "official", "mr", "join"]


def handle_candidates(terms: list[str], official: set[str] = frozenset(), limit: int = 80) -> list[str]:
    """Telegram handles are 5 to 32 characters of letters, digits and underscores."""
    official = {o.lower().lstrip("@") for o in official}
    groups: list[list[str]] = []
    for t in terms:
        t = re.sub(r"[^a-z0-9_]", "", t.lower())
        if len(t) < 4:
            continue
        g = [t + s for s in SUFFIXES] + [p + t for p in PREFIXES] + [p + "_" + t for p in PREFIXES]
        g += [t[:i] + t[i + 1:] for i in range(len(t))]                                   # omission
        g += [t[:i] + t[i + 1] + t[i] + t[i + 2:] for i in range(len(t) - 1)]             # transposition
        g += [t[:i] + k + t[i + 1:] for i in range(len(t)) for k in KEYBOARD.get(t[i], "")[:1]]
        g += [t[:i] + "_" + t[i:] for i in range(2, len(t) - 1)][:3]
        g.append(t)
        groups.append(g)
    out, seen = [], set(official)
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g):
                h = g[i]
                if 5 <= len(h) <= 32 and h not in seen and re.fullmatch(r"[a-z][a-z0-9_]*", h) and not h.endswith("_"):
                    seen.add(h)
                    out.append(h)
    return out[:limit]


def brand_words(brand: dict, terms: list[str]) -> list[str]:
    ws = {re.sub(r"[^a-z0-9]", "", w.lower()) for w in brand["display_name"].split()} | set(terms)
    return sorted(w for w in ws if len(w) >= 4)


def claims_brand(preview: dict, words: list[str]) -> bool:
    blob = re.sub(r"[^a-z0-9]", "", f"{preview.get('title','')} {preview.get('description') or ''} {preview['handle']}".lower())
    return any(w in blob for w in words)


async def telegram_lookalikes(client, repo: BrandRepo, brand_id: str, preview_fn=telegram.preview, concurrency: int = 4, progress=None) -> dict:
    """Existing Telegram accounts that look like the brand. Returns {checked, existing, found:[preview...]}; found = those that name the brand."""
    d = repo.detection_inputs(brand_id)
    brand = d["brand"]
    official = {f["data"].get("handle", "") for f in repo.facts(brand_id, "social") if f["data"].get("platform") == "telegram"} | {
        f["data"].get("handle", "") for f in repo.facts(brand_id, "telegram")}
    official |= {telegram.handle_of(f["value"]) or "" for f in repo.facts(brand_id, "telegram")}
    terms = brand.get("seed_terms") or []
    words = brand_words(brand, terms)
    cands = handle_candidates(terms, official)
    state = {"checked": 0, "existing": 0, "found": []}
    sem = asyncio.Semaphore(concurrency)

    async def one(h: str):
        async with sem:
            try:
                pv = await preview_fn(client, h)
            except Exception:  # noqa: BLE001  one unreachable page is not a failed sweep
                pv = None
            await asyncio.sleep(POLITE_DELAY)            # be polite to t.me
        state["checked"] += 1
        if pv:
            state["existing"] += 1
            if claims_brand(pv, words):
                state["found"].append(pv)
        if progress and state["checked"] % 10 == 0:
            progress(dict(state, found=len(state["found"])))

    await asyncio.gather(*(one(h) for h in cands))
    return state
