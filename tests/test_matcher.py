from pathlib import Path

import pytest

from engine.matcher import Brand, Matcher

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def m():
    return Matcher(Brand.load(ROOT / "brand.yaml"))


@pytest.mark.parametrize("host,reason", [
    ("nkwaex-support.com", "brand_plus_lure_word"),
    ("nkwaexsupport.xyz", "brand_plus_lure_word"),
    ("nkwaex.xyz", "brand_name_exact_label"),
    ("login.nkwaex.online", "brand_plus_lure_word"),
    ("nkvvaex.com", "homoglyph_of_brand"),          # vv -> w
    ("nkwa3x.com", "homoglyph_of_brand"),           # 3 -> e
    ("nkwaxe.com", "typo_of_brand"),                # transposition
    ("nkwex-login.com", "typo_of_brand"),           # omission
    ("nkwaxesupport.com", "typo_of_brand"),         # typo glued to a lure word
    ("nkwaex-gh.netlify.app", "free_hosting_subdomain"),
    ("*.nkwaex-bonus.top", "brand_plus_lure_word"),
    ("nkwaexchange-promo.com", "brand_plus_lure_word"),
])
def test_flags_lookalikes(m, host, reason):
    c = m.match(host)
    assert c is not None, host
    assert reason in c.reasons, (host, c.reasons)


def test_cyrillic_homoglyph(m):
    # "nkwаex" with Cyrillic а (U+0430), encoded as punycode the way CT logs show it.
    puny = "nkwаex.com".encode("idna").decode()
    assert puny.startswith("xn--")
    c = m.match(puny)
    assert c is not None and "homoglyph_of_brand" in c.reasons


@pytest.mark.parametrize("host", [
    "nkwaex.com",              # official
    "app.nkwaex.com",          # official subdomain
    "network.com",
    "nkwa.com",                # too far from the seed
    "kwame-exchange.com",
    "example.org",
    "kwaexp.io",
])
def test_ignores(m, host):
    assert m.match(host) is None, (host, m.match(host))


def test_priority_orders_risk(m):
    weak = m.match("nkwaex.xyz")
    strong = m.match("nkwаex-support.com".encode("idna").decode())
    assert strong.priority > weak.priority
