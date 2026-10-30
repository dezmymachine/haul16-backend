"""End to end on local fixtures: serve fake sites, capture them, score them."""
import asyncio
import functools
import http.server
import threading
from pathlib import Path

import pytest

from engine.capture import capture
from engine.matcher import Brand, Matcher
from engine.score import score

ROOT = Path(__file__).resolve().parents[1]
SITES = ROOT / "fixtures" / "sites"


@pytest.fixture(scope="module")
def server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITES))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def brand():
    return Brand.load(ROOT / "brand.yaml")


def _run(url, tmp_path, allow_local=True):
    return asyncio.run(capture(url, allow_local=allow_local, out_dir=tmp_path))


def test_clone_scores_high(server, brand, tmp_path):
    cap = _run(f"{server}/clone/", tmp_path)
    assert not cap.errors, cap.errors
    assert cap.html_sha256 and cap.screenshot_sha256
    assert "0240000101" in cap.identifiers["phones"]
    cand = Matcher(brand).match("nkwaex-support.com")   # the hostname it would be served on
    v = score(brand, cand, cap)
    assert v.confidence == "high", v
    for r in ("logo_or_favicon_match", "payment_identifier_not_official", "brand_named_on_page"):
        assert r in v.reason_codes
    assert v.evidence["payment_identifier_not_official"]["phones"] == ["0240000101"]


def test_decoy_stays_low(server, brand, tmp_path):
    cap = _run(f"{server}/decoy/", tmp_path)
    v = score(brand, None, cap)
    assert v.confidence == "low", v
    assert "payment_identifier_not_official" not in v.reason_codes


def test_ssrf_blocked_without_flag(server, tmp_path):
    cap = _run(f"{server}/clone/", tmp_path, allow_local=False)
    assert cap.blocked_requests and not cap.html_sha256


def test_unreachable_is_unknown_not_safe(brand, tmp_path):
    cap = _run("http://nkwaex-does-not-exist.invalid/", tmp_path)
    assert score(brand, None, cap).confidence == "unknown"
