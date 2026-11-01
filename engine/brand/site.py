"""Fetch and parse a brand's own website: text, links, social accounts, contacts, structured data and logo candidates.

This is the no-key path (plain HTTP + HTML parsing). With FIRECRAWL_API_KEY the profiler uses Firecrawl for the page text and
links instead, and the same extractors below run on the result.
"""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from engine.clients.base import UpstreamError, request, sha256_text
from engine.extract import extract as extract_ids

MAX_BYTES = 2_000_000
KEY_WORDS = ("about", "contact", "team", "leadership", "management", "company", "who-we-are", "our-story", "support", "help", "terms", "press", "licen", "regulat", "legal")

PLATFORMS = [
    ("linkedin", re.compile(r"linkedin\.com/(company|school|showcase)/([^/?#]+)", re.I)),
    ("x", re.compile(r"(?:twitter|x)\.com/(?!intent|share|home|search|i/)([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I)),
    ("facebook", re.compile(r"facebook\.com/(?!sharer|share|dialog|tr\?|plugins)([A-Za-z0-9.\-_]{3,})(?:[/?#]|$)", re.I)),
    ("instagram", re.compile(r"instagram\.com/(?!p/|reel|explore)([A-Za-z0-9._]{2,30})(?:[/?#]|$)", re.I)),
    ("tiktok", re.compile(r"tiktok\.com/@([A-Za-z0-9._]{2,30})", re.I)),
    ("youtube", re.compile(r"youtube\.com/(@[A-Za-z0-9._\-]+|channel/[A-Za-z0-9_\-]+|c/[A-Za-z0-9_\-]+|user/[A-Za-z0-9_\-]+)", re.I)),
    ("telegram", re.compile(r"(?:t\.me|telegram\.me)/(?!share|joinchat|\+)([A-Za-z0-9_]{4,64})", re.I)),
    ("whatsapp", re.compile(r"(?:wa\.me/(\d{6,15})|whatsapp\.com/channel/([A-Za-z0-9_\-]+)|chat\.whatsapp\.com/([A-Za-z0-9]+))", re.I)),
    ("github", re.compile(r"github\.com/([A-Za-z0-9\-]{1,39})(?:[/?#]|$)", re.I)),
]
APP_STORES = [
    ("android", re.compile(r"play\.google\.com/store/apps/details\?id=([A-Za-z0-9._]+)", re.I)),
    ("ios", re.compile(r"apps\.apple\.com/[^\s\"']*?/id(\d+)", re.I)),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
INTL_PHONE = re.compile(r"(?<![\d+])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}(?!\d)")


@dataclass
class Page:
    url: str
    title: str | None = None
    description: str | None = None
    text: str = ""
    links: list[str] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)       # {url, kind, hint}
    jsonld: list[dict] = field(default_factory=list)
    sha256: str = ""


def _in_chrome(node) -> bool:
    """Is this element inside the page header or navigation (where a company's own logo lives)?"""
    p = node.parent
    while p is not None:
        if p.tag in ("header", "nav"):
            return True
        p = p.parent
    return False


def parse_html(url: str, html: str, brand_hints: list[str] = ()) -> Page:
    tree = HTMLParser(html)
    for n in tree.css("script, style, noscript, svg"):
        if n.attributes.get("type") != "application/ld+json":
            n.decompose()
    jsonld: list[dict] = []
    raw = HTMLParser(html)
    for n in raw.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(n.text() or "null")
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict):
                jsonld.extend(item.get("@graph") if isinstance(item.get("@graph"), list) else [item])
    meta = lambda sel, attr="content": (lambda n: n.attributes.get(attr) if n else None)(raw.css_first(sel))  # noqa: E731
    title = (raw.css_first("title").text(strip=True) if raw.css_first("title") else None) or meta('meta[property="og:title"]')
    desc = meta('meta[name="description"]') or meta('meta[property="og:description"]')
    links = []
    for a in raw.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("javascript:", "#")):
            links.append(urljoin(url, href))
    images: list[dict] = []
    for sel, kind in (('link[rel~="icon"]', "favicon"), ('link[rel="apple-touch-icon"]', "favicon"), ('link[rel="shortcut icon"]', "favicon")):
        for n in raw.css(sel):
            if n.attributes.get("href"):
                images.append({"url": urljoin(url, n.attributes["href"]), "kind": kind, "hint": sel})
    og = meta('meta[property="og:image"]')
    if og:
        images.append({"url": urljoin(url, og), "kind": "og_image", "hint": "og:image"})
    for n in raw.css("img"):
        src = n.attributes.get("src") or n.attributes.get("data-src") or ""
        blob = " ".join([src, n.attributes.get("alt") or "", n.attributes.get("class") or "", n.attributes.get("id") or ""]).lower()
        if src and "logo" in blob:
            # Only the company's own logo: named after the brand, or in the header/nav. Footer and partner-strip logos are other people's marks.
            if any(h in blob for h in brand_hints) or _in_chrome(n):
                images.append({"url": urljoin(url, src), "kind": "logo", "hint": "img[logo]"})
    for item in jsonld:
        logo = item.get("logo")
        logo = logo.get("url") if isinstance(logo, dict) else logo
        if isinstance(logo, str):
            images.append({"url": urljoin(url, logo), "kind": "logo", "hint": "jsonld"})
    text = re.sub(r"\s+", " ", tree.body.text(separator=" ") if tree.body else "").strip()
    return Page(url=url, title=title, description=desc, text=text[:60_000], links=list(dict.fromkeys(links)),
                images=images, jsonld=jsonld, sha256=sha256_text(html))


async def fetch_page(client: httpx.AsyncClient, url: str, brand_hints: list[str] = ()) -> Page | None:
    try:
        res = await request(client, "GET", url, retries=1)
    except UpstreamError:
        return None
    if res.status_code >= 400 or "html" not in res.headers.get("content-type", "html").lower():
        return None
    return parse_html(str(res.url), res.text[:MAX_BYTES], brand_hints)


def key_links(base_url: str, links: list[str], limit: int = 6) -> list[str]:
    host = (urlparse(base_url).hostname or "").removeprefix("www.")
    out: list[str] = []
    for l in links:
        u = urlparse(l)
        if (u.hostname or "").removeprefix("www.") != host or u.fragment:
            continue
        path = u.path.lower().rstrip("/")
        if path and any(k in path for k in KEY_WORDS) and l.split("#")[0] not in out and not path.endswith((".jpg", ".png", ".pdf")):
            out.append(l.split("#")[0])
    return out[:limit]


def socials(links: list[str], text: str = "") -> list[dict]:
    seen, out = set(), []
    for l in list(links) + re.findall(r"https?://[^\s\"'<>)]+", text):
        for platform, rx in PLATFORMS:
            m = rx.search(l)
            if not m:
                continue
            handle = next((g for g in reversed(m.groups()) if g), None)
            if platform == "linkedin":
                handle = m.group(2)
            key = (platform, (handle or "").lower())
            if key in seen or not handle:
                continue
            seen.add(key)
            out.append({"platform": platform, "handle": handle, "url": l.split("?")[0] if platform != "youtube" else l})
    return out


def apps(links: list[str], text: str = "") -> list[dict]:
    out, seen = [], set()
    for l in list(links) + re.findall(r"https?://[^\s\"'<>)]+", text):
        for store, rx in APP_STORES:
            m = rx.search(l)
            if m and (store, m.group(1)) not in seen:
                seen.add((store, m.group(1)))
                out.append({"store": store, "id": m.group(1), "url": l})
    return out


def contacts(text: str, links: list[str]) -> dict:
    ids = extract_ids(text, links)
    emails = {e.lower() for e in EMAIL.findall(text)} | {l.split(":", 1)[1].split("?")[0].lower() for l in links if l.lower().startswith("mailto:")}
    emails = {e for e in emails if not e.endswith((".png", ".jpg", ".svg")) and "sentry" not in e and "example." not in e}
    intl = {re.sub(r"[\s.\-()]", "", p) for p in INTL_PHONE.findall(text)}
    for l in links:
        if l.lower().startswith("tel:"):
            intl.add(re.sub(r"[\s.\-()]", "", l[4:]))
    return {"phones_gh": ids.phones, "phones_intl": sorted(p for p in intl if 8 <= len(p) <= 16), "emails": sorted(emails)[:12], "wallets": ids.wallets}


def org_from_jsonld(items: list[dict]) -> dict:
    """Name, sameAs, address and phone from schema.org Organization markup, when the site has it."""
    for it in items:
        t = it.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("Organization", "Corporation", "LocalBusiness", "FinancialService", "OnlineBusiness") for x in types if isinstance(x, str)):
            addr = it.get("address")
            if isinstance(addr, dict):
                addr = ", ".join(str(addr.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion", "addressCountry") if addr.get(k))
            same = it.get("sameAs")
            return {"name": it.get("name"), "legal_name": it.get("legalName"), "phone": it.get("telephone"), "address": addr if isinstance(addr, str) else None,
                    "same_as": same if isinstance(same, list) else ([same] if isinstance(same, str) else []),
                    "founders": [f.get("name") for f in (it.get("founder") if isinstance(it.get("founder"), list) else [it.get("founder")]) if isinstance(f, dict) and f.get("name")]}
    return {}
