"""Stage B: capture evidence from a suspect URL in a locked-down headless browser.

The page is attacker-controlled. The browser only looks: no clicks, no form
submission, no downloads, no permissions, private and internal addresses blocked
(SSRF), hard timeout. Page text is treated as untrusted data, never instructions.

  uv run python -m engine.capture https://suspect.example
  uv run python -m engine.capture http://127.0.0.1:8765/ --allow-local   # local fixtures only
"""
import argparse
import asyncio
import base64
import codecs
import hashlib
import ipaddress
import json
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import mmh3
import os
from playwright.async_api import async_playwright

from engine.extract import extract

CAPTURE_TOOL = "haul16-capture/0.1 (playwright-chromium)"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "data" / "evidence"
TIMEOUT_MS = 25_000
MAX_TEXT = 200_000


def _load_env_if_present() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env_if_present()


@dataclass
class Capture:
    url: str
    final_url: str | None = None
    status: int | None = None
    captured_at: str = ""
    capture_tool: str = CAPTURE_TOOL
    title: str | None = None
    html_sha256: str | None = None
    screenshot_ref: str | None = None
    screenshot_sha256: str | None = None
    favicon_url: str | None = None
    favicon_mmh3: int | None = None
    favicon_sha256: str | None = None
    server_headers: dict = field(default_factory=dict)
    text_excerpt: str = ""                       # untrusted page text, for scoring and triage
    identifiers: dict = field(default_factory=dict)
    blocked_requests: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def favicon_hash(data: bytes) -> int:
    """Shodan-style favicon hash: mmh3 of base64 with 76-char line breaks."""
    return mmh3.hash(codecs.encode(data, "base64"))


def _is_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False                      # unresolvable: let the browser fail on its own
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


async def capture(url: str, allow_local: bool = False, out_dir: Path = EVIDENCE_DIR) -> Capture:
    cap = Capture(url=url, captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out_dir.mkdir(parents=True, exist_ok=True)
    private_cache: dict[str, bool] = {}

    async def guard(route):
        req = route.request
        host = urlparse(req.url).hostname or ""
        scheme = urlparse(req.url).scheme
        if scheme not in ("http", "https", "data", "blob"):
            cap.blocked_requests.append(req.url[:200])
            return await route.abort()
        if scheme in ("http", "https") and not allow_local:
            if host not in private_cache:
                private_cache[host] = await asyncio.to_thread(_is_private, host)
            if private_cache[host]:
                cap.blocked_requests.append(req.url[:200])
                return await route.abort()
        await route.continue_()

    bb_key = os.environ.get("BROWSERBASE_API_KEY", "")
    host = urlparse(url).hostname or ""
    is_dummy = host.endswith(".invalid") or host.endswith(".test") or host.endswith(".local") or host == "localhost"
    use_browserbase = bool(bb_key and not allow_local and not is_dummy and not _is_private(host))

    async with async_playwright() as pw:
        browser = None
        is_cloud = False
        if use_browserbase:
            try:
                # Browserbase Developer Plan with residential proxy support & captcha solving
                cdp_url = f"wss://connect.browserbase.com?apiKey={bb_key}&enableProxy=true"
                browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
                is_cloud = True
                cap.capture_tool = "haul16-capture/0.1 (browserbase-cloud-proxy)"
            except Exception as bb_err:
                cap.errors.append(f"BrowserbaseFallback: {type(bb_err).__name__}: {str(bb_err)[:150]}")
                browser = None

        if browser is None:
            browser = await pw.chromium.launch(args=["--disable-extensions", "--disable-sync", "--no-first-run"])
            cap.capture_tool = CAPTURE_TOOL

        if is_cloud and browser.contexts:
            ctx = browser.contexts[0]
        else:
            ctx = await browser.new_context(
                accept_downloads=False, java_script_enabled=True, ignore_https_errors=True,
                viewport={"width": 1280, "height": 900}, service_workers="block", permissions=[],
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
            )
        if not is_cloud:
            await ctx.route("**/*", guard)
        page = await ctx.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        try:
            resp = await page.goto(url, wait_until="load", timeout=TIMEOUT_MS)
            await page.wait_for_timeout(1500)          # let client-side rendering settle
            cap.final_url = page.url
            if resp:
                cap.status = resp.status
                h = await resp.all_headers()
                cap.server_headers = {k: h[k] for k in ("server", "x-powered-by", "cf-ray", "via") if k in h}

            html = await page.content()
            cap.html_sha256 = hashlib.sha256(html.encode()).hexdigest()
            cap.title = (await page.title())[:300]
            stem = cap.captured_at.replace(":", "").replace("-", "") + "-" + cap.html_sha256[:12]
            (out_dir / f"{stem}.html").write_text(html)

            shot = await page.screenshot(full_page=True, timeout=TIMEOUT_MS)
            (out_dir / f"{stem}.png").write_bytes(shot)
            cap.screenshot_ref = f"data/evidence/{stem}.png"
            cap.screenshot_sha256 = hashlib.sha256(shot).hexdigest()

            text = (await page.inner_text("body"))[:MAX_TEXT]
            links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            cap.identifiers = extract(text, links).as_dict()
            cap.text_excerpt = text[:5000]

            icon_href = await page.eval_on_selector(
                "link[rel~='icon'], link[rel='shortcut icon']", "e => e.href") if await page.query_selector(
                "link[rel~='icon'], link[rel='shortcut icon']") else urljoin(cap.final_url, "/favicon.ico")
            cap.favicon_url = icon_href
            icon_host = urlparse(icon_href).hostname or ""
            if icon_href.startswith("data:"):
                data = base64.b64decode(icon_href.split(",", 1)[1])
            elif not allow_local and await asyncio.to_thread(_is_private, icon_host):
                cap.blocked_requests.append(icon_href[:200])   # API requests skip the route guard
                data = b""
            else:
                r = await ctx.request.get(icon_href, timeout=TIMEOUT_MS)
                data = await r.body() if r.ok else b""
            if data:
                cap.favicon_mmh3 = favicon_hash(data)
                cap.favicon_sha256 = hashlib.sha256(data).hexdigest()
        except Exception as e:                          # evidence is partial, never invented
            cap.errors.append(f"{type(e).__name__}: {str(e)[:300]}")
        finally:
            await browser.close()
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--allow-local", action="store_true", help="permit localhost (test fixtures only)")
    args = ap.parse_args()
    print(json.dumps(asdict(asyncio.run(capture(args.url, args.allow_local))), indent=2))


if __name__ == "__main__":
    main()
