"""Firecrawl: turn a URL into clean markdown plus its links. Used for the brand's own site and for press pages."""
import re

import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings

API = "https://api.firecrawl.dev/v1"
V2 = "https://api.firecrawl.dev/v2"
ENRICHMENT_PAGE = "https://www.firecrawl.dev/app/alexandria?enrichment=true"
LINKEDIN_COMPANY = re.compile(r"linkedin\.com/company/([A-Za-z0-9_%.\-]+)", re.I)
LINKEDIN_SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"}, "tagline": {"type": "string"}, "industry": {"type": "string"}, "website": {"type": "string"},
    "employee_count": {"type": "string"}, "followers": {"type": "string"}, "headquarters": {"type": "string"}, "description": {"type": "string"}}}


def linkedin_slug(url: str | None) -> str | None:
    """The company slug of a linkedin.com/company/<slug> link (any country subdomain, any trailing path or tracking query), lower-cased."""
    m = LINKEDIN_COMPANY.search(url or "")
    return m.group(1).lower() if m else None


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

    async def linkedin_company(self, url: str) -> dict:
        """A LinkedIn company record. Firecrawl does not scrape LinkedIn itself: an org admin has to switch on third-party enrichment
        (5-30 credits a company). Returns {slug, fields, markdown, provider}; raises NotConfigured while enrichment is off."""
        slug = linkedin_slug(url)
        if not slug:
            raise UpstreamError("not a LinkedIn company page link")
        res = await request(self.client, "POST", f"{V2}/scrape", headers=self._h(), timeout=90, retries=1,
                            json={"url": f"https://www.linkedin.com/company/{slug}/about", "formats": ["markdown", {"type": "json", "schema": LINKEDIN_SCHEMA}]})
        if res.status_code >= 400:
            try:
                body = res.json() or {}
            except ValueError:
                body = {}
            code = str(body.get("code") or "")
            if code == "THIRD_PARTY_DATA_ENRICHMENT_NOT_ENABLED":
                raise NotConfigured(f"Firecrawl LinkedIn enrichment is not switched on. An admin of the Firecrawl account can enable it at {ENRICHMENT_PAGE}")
            if code == "THIRD_PARTY_DATA_TERMS_REQUIRED":
                raise NotConfigured(f"A Firecrawl admin has to accept the enrichment terms: {(body.get('requiresAction') or {}).get('url') or ENRICHMENT_PAGE}")
            if res.status_code == 402:
                raise UpstreamError("Firecrawl credits are used up (HTTP 402)")
            raise UpstreamError(f"firecrawl linkedin {res.status_code}: {res.text[:200]}")
        data = (res.json() or {}).get("data") or {}
        fields = data.get("json") if isinstance(data.get("json"), dict) else {}
        return {"slug": slug, "fields": {k: v for k, v in fields.items() if v not in (None, "", [])}, "markdown": data.get("markdown") or "",
                "provider": (data.get("metadata") or {}).get("provider")}
