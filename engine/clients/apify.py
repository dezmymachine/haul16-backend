"""Apify actors for public social/LinkedIn data. Actor ids are settings because the marketplace changes; the actor input is passed through."""
import httpx

from engine.clients.base import NotConfigured, UpstreamError, request
from engine.settings import get_settings


class Apify:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.token = get_settings().apify_token
        if not self.token:
            raise NotConfigured("APIFY_TOKEN is not set")

    async def run(self, actor: str | None, actor_input: dict, timeout_s: int = 120) -> list[dict]:
        """Run an actor synchronously and return its dataset items."""
        if not actor:
            raise NotConfigured("no Apify actor id configured for this collector")
        res = await request(self.client, "POST", f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                            params={"token": self.token, "timeout": timeout_s}, json=actor_input, timeout=timeout_s + 15, retries=1)
        if res.status_code >= 400:
            raise UpstreamError(f"apify {res.status_code}: {res.text[:200]}")
        data = res.json()
        return data if isinstance(data, list) else []
