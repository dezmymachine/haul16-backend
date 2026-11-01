"""Firecrawl: turn a URL into clean markdown plus its links. Used for the brand's own site and for press pages."""
import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings

API = "https://api.firecrawl.dev/v1"


class Firecrawl:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None):
        self.client = client
        self.key = api_key or get_settings().firecrawl_api_key
        if not self.key:
            raise NotConfigured("FIRECRAWL_API_KEY is not set")

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    async def scrape(self, url: str) -> dict:
        """Returns {url, title, description, markdown, links, metadata}."""
        res = await request(self.client, "POST", f"{API}/scrape", headers=self._h(),
                            json={"url": url, "formats": ["markdown", "links"], "onlyMainContent": False, "timeout": 30000})
        if res.status_code >= 400:
            raise UpstreamError(f"firecrawl scrape {res.status_code}: {res.text[:200]}")
        data = (res.json() or {}).get("data") or {}
        meta = data.get("metadata") or {}
        return {"url": meta.get("sourceURL") or url, "title": meta.get("title"), "description": meta.get("description"),
                "markdown": data.get("markdown") or "", "links": data.get("links") or [], "metadata": meta}

    async def map(self, url: str, limit: int = 60) -> list[str]:
        res = await request(self.client, "POST", f"{API}/map", headers=self._h(), json={"url": url, "limit": limit})
        if res.status_code >= 400:
            raise UpstreamError(f"firecrawl map {res.status_code}: {res.text[:200]}")
        links = (res.json() or {}).get("links") or []
        return [l["url"] if isinstance(l, dict) else l for l in links]
