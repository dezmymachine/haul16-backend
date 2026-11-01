"""Download a brand's logo and favicon candidates and fingerprint them (sha256, perceptual hash, favicon mmh3)."""
import hashlib
import io

import httpx

from engine.brand.repo import BrandRepo
from engine.clients.base import UpstreamError, request
from engine.capture import favicon_hash

MAX_IMAGE = 1_500_000


def _phash(data: bytes) -> str | None:
    try:
        import imagehash
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return str(imagehash.phash(im.convert("RGBA").convert("RGB")))
    except Exception:  # svg, ico variants or corrupt files: no perceptual hash
        return None


async def collect(client: httpx.AsyncClient, repo: BrandRepo, brand_id: str, images: list[dict], source_url: str, limit: int = 5) -> int:
    """Returns how many assets were stored."""
    # One URL can play several roles (the logo is often also the og:image): keep the most specific label.
    rank = {"logo": 0, "favicon": 1, "og_image": 2}
    best: dict[str, dict] = {}
    for img in images:
        if img["url"] not in best or rank.get(img["kind"], 3) < rank.get(best[img["url"]]["kind"], 3):
            best[img["url"]] = img
    stored = 0
    for url, img in sorted(best.items(), key=lambda kv: rank.get(kv[1]["kind"], 3)):
        if url.startswith("data:") or stored >= limit:
            continue
        try:
            res = await request(client, "GET", url, retries=0)
        except UpstreamError:
            continue
        ctype = res.headers.get("content-type", "")
        if res.status_code != 200 or not (ctype.startswith("image/") or url.lower().endswith((".ico", ".svg", ".png", ".jpg", ".jpeg", ".webp"))):
            continue
        data = res.content[:MAX_IMAGE]
        if len(data) < 64:
            continue
        kind = "favicon" if img["kind"] == "favicon" else ("logo" if img["kind"] == "logo" else "image")
        mmh3 = None
        if kind == "favicon":
            try:
                mmh3 = favicon_hash(data)
            except Exception:
                mmh3 = None
        repo.add_asset(brand_id, kind, url, hashlib.sha256(data).hexdigest(), _phash(data) if "svg" not in ctype else None, mmh3,
                       {"content_type": ctype, "bytes": len(data), "hint": img.get("hint")}, source_url)
        stored += 1
    return stored
