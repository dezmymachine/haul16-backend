"""crt.name: hostnames under a domain that appear in public certificate logs (no key). One hostname per line.

These are names the domain's owner (or someone controlling its DNS) got a certificate for, so they are the brand's own estate
plus anything forgotten or hijacked under it. The reply is data from the internet: every line is validated before use.
"""
import re

import httpx

from engine.clients.base import UpstreamError, request

API = "https://crt.name/v1/search"
HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
MAX_HOSTS = 2000


async def subdomains(client: httpx.AsyncClient, apex: str) -> list[str]:
    """Distinct, sorted hostnames strictly below `apex` (the apex itself, wildcards' `*.` prefix and anything off-domain are dropped)."""
    apex = apex.strip().lower().rstrip(".")
    res = await request(client, "GET", API, params={"apex": apex}, timeout=30, retries=1)
    if res.status_code >= 400:
        raise UpstreamError(f"crt.name {res.status_code}: {res.text[:160]}")
    seen: set[str] = set()
    for line in res.text.splitlines()[:50_000]:
        h = line.strip().lower().lstrip("*.").rstrip(".")
        if h.endswith("." + apex) and HOST.match(h):
            seen.add(h)
            if len(seen) >= MAX_HOSTS:
                break
    return sorted(seen)
