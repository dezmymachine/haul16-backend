"""Public Telegram preview (no login, no joining): title, description, subscriber count from t.me pages. Shared by the brand profiler and, later, the impersonation watchers."""
import re

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from engine.clients.base import request

HANDLE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,64})", re.I)


def handle_of(url_or_handle: str) -> str | None:
    m = HANDLE.search(url_or_handle)
    if m:
        return m.group(1)
    h = url_or_handle.lstrip("@")
    return h if re.fullmatch(r"[A-Za-z0-9_]{4,64}", h) else None


def parse_preview(handle: str, html: str) -> dict | None:
    """Parse a t.me page. Only a real account or channel has the page-title block: for an unused handle t.me still serves a page
    (with a generic og:title), so the og:title is NOT proof that the account exists."""
    tree = HTMLParser(html)
    title = tree.css_first(".tgme_page_title")
    if not title:
        return None
    def meta(prop):
        n = tree.css_first(f'meta[property="{prop}"]')
        return n.attributes.get("content") if n else None
    extra = tree.css_first(".tgme_page_extra")
    desc = tree.css_first(".tgme_page_description")
    extra_text = extra.text(strip=True) if extra else ""
    m = re.search(r"([\d\s,.]+)\s*(subscribers|members)", extra_text, re.I)
    digits = re.sub(r"\D", "", m.group(1)) if m else ""
    return {
        "handle": handle, "url": f"https://t.me/{handle}",
        "title": title.text(strip=True),
        "description": desc.text(strip=True) if desc else meta("og:description"),
        "audience": int(digits) if digits else None,
        "kind": "channel" if m and m.group(2).lower() == "subscribers" else ("group" if m else "account"),
        "image": meta("og:image"),
        "bot": handle.lower().endswith("bot"),
    }


async def preview(client: httpx.AsyncClient, handle: str) -> dict | None:
    """Observe-only. Returns None when the account does not exist."""
    res = await request(client, "GET", f"https://t.me/{handle}", retries=1)
    return parse_preview(handle, res.text) if res.status_code == 200 else None
