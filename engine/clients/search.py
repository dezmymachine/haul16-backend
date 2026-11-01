"""Web and news search behind two methods, `web()` and `news()`. Providers: Firecrawl (default, uses FIRECRAWL_API_KEY) or Brave (SEARCH_API_KEY).

Both return the same shape: [{title, url, snippet, age, source?}].
"""
from urllib.parse import urlparse

import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings

FIRECRAWL_SEARCH = "https://api.firecrawl.dev/v2/search"


class Search:
    def __init__(self, client: httpx.AsyncClient):
        s = get_settings()
        self.client, self.provider = client, s.search_provider
        if self.provider == "firecrawl":
            self.key = s.firecrawl_api_key
            if not self.key:
                raise NotConfigured("FIRECRAWL_API_KEY is not set (search provider is 'firecrawl')")
        elif self.provider == "brave":
            self.key = s.search_api_key
            if not self.key:
                raise NotConfigured("SEARCH_API_KEY is not set (search provider is 'brave')")
        else:
            raise NotConfigured(f"search provider '{self.provider}' is not implemented (use 'firecrawl' or 'brave')")

    async def web(self, q: str, count: int = 10) -> list[dict]:
        return await (self._firecrawl("web", q, count) if self.provider == "firecrawl" else self._brave_web(q, count))

    async def news(self, q: str, count: int = 10) -> list[dict]:
        return await (self._firecrawl("news", q, count) if self.provider == "firecrawl" else self._brave_news(q, count))

    # ---- Firecrawl: 2 credits per 10 results, no page scraping unless scrapeOptions is sent (it is not) ----
    async def _firecrawl(self, source: str, q: str, count: int) -> list[dict]:
        res = await request(self.client, "POST", FIRECRAWL_SEARCH, timeout=60,
                            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                            json={"query": q[:500], "limit": max(1, min(count, 100)), "sources": [source], "highlights": False, "timeout": 30000})
        if res.status_code >= 400:
            raise UpstreamError(f"firecrawl search {res.status_code}: {res.text[:200]}")
        data = (res.json() or {}).get("data") or {}
        if isinstance(data, list):                 # an older flat reply: treat it as web results
            data = {"web": data}
        out = []
        for r in data.get(source) or []:
            url = r.get("url")
            if not url:
                continue
            out.append({"title": r.get("title"), "url": url, "snippet": r.get("snippet") or r.get("description"), "age": r.get("date"),
                        **({"source": urlparse(url).hostname} if source == "news" else {})})
        return out

    # ---- Brave ----
    async def _brave(self, path: str, q: str, count: int) -> dict:
        res = await request(self.client, "GET", f"https://api.search.brave.com/res/v1/{path}",
                            headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                            params={"q": q, "count": count})
        if res.status_code >= 400:
            raise UpstreamError(f"search {res.status_code}: {res.text[:200]}")
        return res.json()

    async def _brave_web(self, q: str, count: int) -> list[dict]:
        data = await self._brave("web/search", q, count)
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"), "age": r.get("age")}
                for r in (data.get("web") or {}).get("results", [])]

    async def _brave_news(self, q: str, count: int) -> list[dict]:
        data = await self._brave("news/search", q, count)
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"), "age": r.get("age"),
                 "source": (r.get("meta_url") or {}).get("hostname")} for r in data.get("results", [])]
