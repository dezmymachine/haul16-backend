"""Shared pieces for every outside-world client: errors, a retrying async HTTP helper, and a text/URL cache key."""
import asyncio
import hashlib

import httpx

UA = "haul16-agent/0.2 (+brand protection; contact via your HAUL16 admin)"


class NotConfigured(Exception):
    """A collector or client was asked to run without its API key. The profiler records this as 'skipped'."""


class UpstreamError(Exception):
    """The vendor answered with an error after retries."""


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()


async def request(client: httpx.AsyncClient, method: str, url: str, *, retries: int = 2, **kw) -> httpx.Response:
    """HTTP with a few retries on 429/5xx and network errors. Raises UpstreamError when it gives up."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            res = await client.request(method, url, **kw)
            if res.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
            return res
        except httpx.HTTPError as e:
            last = e
            if attempt < retries:
                await asyncio.sleep(min(2 ** attempt, 8))
    raise UpstreamError(f"{method} {url}: {last!r}")


ALLOW_PRIVATE = False   # tests and local fixtures only


async def _guard(request: httpx.Request) -> None:
    """Refuse requests to private/internal addresses, on the first hop and on every redirect (SSRF)."""
    if ALLOW_PRIVATE or request.url.host in ("t.me",):
        return
    from engine.capture import _is_private
    if await asyncio.to_thread(_is_private, request.url.host):
        raise httpx.RequestError(f"blocked: {request.url.host} is a private or internal address", request=request)


def new_client(timeout: float = 30, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA},
                             event_hooks={"request": [_guard]}, **kw)
