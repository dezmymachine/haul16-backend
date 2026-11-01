"""Web and news search (Brave Search API). The provider is a setting so Serper or Tavily can be added behind the same two methods."""
import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings


class Search:
    def __init__(self, client: httpx.AsyncClient):
        s = get_settings()
        self.client, self.provider, self.key = client, s.search_provider, s.search_api_key
        if not self.key:
            raise NotConfigured("SEARCH_API_KEY is not set")
        if self.provider != "brave":
            raise NotConfigured(f"search provider '{self.provider}' is not implemented yet (only 'brave')")

    async def _get(self, path: str, q: str, count: int) -> dict:
        res = await request(self.client, "GET", f"https://api.search.brave.com/res/v1/{path}",
                            headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
                            params={"q": q, "count": count})
        if res.status_code >= 400:
            raise UpstreamError(f"search {res.status_code}: {res.text[:200]}")
        return res.json()

    async def web(self, q: str, count: int = 10) -> list[dict]:
        data = await self._get("web/search", q, count)
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"), "age": r.get("age")}
                for r in (data.get("web") or {}).get("results", [])]

    async def news(self, q: str, count: int = 10) -> list[dict]:
        data = await self._get("news/search", q, count)
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description"), "age": r.get("age"),
                 "source": (r.get("meta_url") or {}).get("hostname")} for r in data.get("results", [])]
