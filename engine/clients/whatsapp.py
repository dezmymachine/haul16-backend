"""Public WhatsApp group-invite preview (no login, never joins). A real invite page carries the group name; an invalid or revoked
code serves the same page with an empty og:title."""
import re

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from engine.clients.base import request

INVITE = re.compile(r"chat\.whatsapp\.com/(?:invite/)?([A-Za-z0-9]{16,32})")


def invite_code(url: str) -> str | None:
    m = INVITE.search(url)
    return m.group(1) if m else None


def parse_invite(code: str, html: str) -> dict | None:
    tree = HTMLParser(html)
    def meta(prop):
        n = tree.css_first(f'meta[property="{prop}"]')
        return (n.attributes.get("content") or "").strip() if n else ""
    name = meta("og:title")
    if not name:
        return None
    return {"code": code, "url": f"https://chat.whatsapp.com/{code}", "name": name, "image": meta("og:image") or None}


async def preview(client: httpx.AsyncClient, code: str) -> dict | None:
    res = await request(client, "GET", f"https://chat.whatsapp.com/{code}", retries=1)
    return parse_invite(code, res.text) if res.status_code == 200 else None
