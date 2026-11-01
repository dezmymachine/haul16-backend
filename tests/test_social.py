"""Telegram and WhatsApp public pages, handle generation, and the account sweep (fixtures only, no network)."""
import asyncio

from engine.clients import telegram, whatsapp
from engine.social import claims_brand, handle_candidates, telegram_lookalikes
from engine.brand.repo import BrandRepo

CHANNEL = '<html><head><meta property="og:title" content="Telegram: Contact @x"></head><body><div class="tgme_page_title"><span>Acme Pay Official</span></div>' \
          '<div class="tgme_page_extra">12 345 subscribers</div><div class="tgme_page_description">Double your USDT with Acme. Pay 0241112223</div></body></html>'
UNUSED = '<html><head><meta property="og:title" content="Telegram: Contact @zzqq"></head><body><div class="tgme_page_description">If you have Telegram, you can contact @zzqq right away.</div></body></html>'


def test_unused_handle_is_not_an_account_even_though_the_page_has_an_og_title():
    assert telegram.parse_preview("zzqq", UNUSED) is None


def test_channel_preview_is_parsed():
    p = telegram.parse_preview("acmepay_official", CHANNEL)
    assert p["title"] == "Acme Pay Official" and p["kind"] == "channel" and p["audience"] == 12345 and "0241112223" in p["description"] and not p["bot"]
    assert telegram.parse_preview("acmebot", CHANNEL)["bot"] is True


def test_whatsapp_invite_needs_a_group_name():
    code = "ABCDEFGHIJKLMNOPQRSTUV"
    assert whatsapp.invite_code(f"https://chat.whatsapp.com/{code}") == code and whatsapp.invite_code("https://example.com") is None
    assert whatsapp.parse_invite(code, '<meta property="og:title" content="" /><meta property="og:description" content="WhatsApp Group Invite" />') is None
    g = whatsapp.parse_invite(code, '<meta property="og:title" content="Acme Pay Investors" /><meta property="og:image" content="https://x/y.png" />')
    assert g["name"] == "Acme Pay Investors" and g["url"].endswith(code)


def test_handle_candidates_are_valid_and_exclude_official():
    c = handle_candidates(["acmepay"], {"@acmepay_official"}, limit=200)
    assert "acmepay_support" in c and "acmepayofficial" in c and "realacmepay" in c and "acmepy" in c
    assert "acmepay_official" not in c and len(set(c)) == len(c)
    assert all(5 <= len(h) <= 32 and h[0].isalpha() and not h.endswith("_") for h in c)
    assert handle_candidates(["ab"]) == []                      # too short to be meaningful


def test_claims_brand_looks_at_title_description_and_handle():
    assert claims_brand({"title": "Support", "description": "", "handle": "acmepay_help"}, ["acmepay"])
    assert claims_brand({"title": "Acme Pay Official", "description": None, "handle": "zzz_group"}, ["acmepay", "acme"])
    assert not claims_brand({"title": "Fresh farm produce", "description": "tomatoes", "handle": "farmshop"}, ["acmepay"])


def test_telegram_sweep_reports_existing_accounts_and_skips_missing_ones(store):
    repo = BrandRepo(store)
    b = repo.create("Acme Pay", "acmepay.com")
    accounts = {"acmepay_support": {"handle": "acmepay_support", "title": "Acme Pay Support", "description": "", "kind": "account"},
                "acmepayofficial": {"handle": "acmepayofficial", "title": "Fresh produce", "description": "tomatoes", "kind": "channel"}}

    async def fake_preview(client, handle):
        return accounts.get(handle)

    st = asyncio.run(telegram_lookalikes(None, repo, b["id"], preview_fn=fake_preview))
    # both existing accounts have the brand in their handle, so both claim it; every other guessed handle does not exist
    assert st["existing"] == 2 and sorted(f["handle"] for f in st["found"]) == ["acmepay_support", "acmepayofficial"] and st["checked"] > 30
