"""Seed the local HAUL16 database with the realistic demonstration scenario.

This dynamically reads brand.yaml and populates realistic threat data, cases,
notices, and takedown clock history tailored to the active brand.

Usage:
  uv run python -m engine.seed
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.matcher import Brand, RULES_VERSION
from engine.store import DB_PATH, Store

ROOT = Path(__file__).resolve().parents[1]


def hours_ago(h: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=h)
    return dt.isoformat(timespec="seconds")


def seed_database(store: Store | None = None) -> dict:
    if store is None:
        store = Store(DB_PATH)

    brand = Brand.load(ROOT / "brand.yaml")
    brand_id = brand.brand_id
    brand_name = brand.display_name

    # Wipe existing demo data first to ensure clean state
    for t in ("threats", "cases", "identifiers", "notices", "clock_events", "audit_log"):
        store.db.execute(f"DELETE FROM {t}")
    store.db.commit()

    # Read authorization letter SHA-256
    letter_path = ROOT / "fixtures" / "authorisation_letter_yellowcard.txt"
    if not letter_path.exists():
        letter_path = ROOT / "fixtures" / "authorisation_letter_demo.txt"
    letter_sha = hashlib.sha256(letter_path.read_bytes()).hexdigest() if letter_path.exists() else "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def audit(h: float, actor: str, action: str, ref: str | None = None, detail: dict | None = None):
        store.db.execute("INSERT INTO audit_log VALUES (?,?,?,?,?)",
                         (hours_ago(h), actor, action, ref, json.dumps(detail) if detail else None))

    def clock(h: float, case_id: str, notice_id: str | None, channel: str | None, event: str, note: str | None = None):
        store.db.execute("INSERT INTO clock_events (case_id, notice_id, channel, event, ts, simulated, note) VALUES (?,?,?,?,?,1,?)",
                         (case_id, notice_id, channel, event, hours_ago(h), note))

    # =========================================================================
    # Case A: Multi-domain campaign sharing one MoMo number (024 555 0142)
    # =========================================================================
    case_a = "case_9f3a21c7d4"
    store.db.execute("INSERT INTO cases VALUES (?,?,?, 'open', ?)",
                     (case_a, brand_id, hours_ago(72), f"Suspected clone of {brand_name}"))
    audit(72, "system", "case_opened", case_a, {"first_threat": "thr_a1c4e8f201"})

    momo_phone = "0245550142"
    crypto_wallet = "TQ9mXk3vP2nLrW8aYd5Ht7sZc4JfB6eUo1"
    tg_handle = "YellowCard_Support_GH"

    # Threat 1: yellowcard-staging.com / login
    t1_host = "yellowcard-staging.com"
    t1_id = "thr_a1c4e8f201"
    t1_cap = {
        "url": f"https://{t1_host}/", "final_url": f"https://{t1_host}/login",
        "status": 200, "captured_at": hours_ago(71.9), "capture_tool": "haul16-capture/0.1 (browserbase-cloud-proxy)",
        "title": f"{brand_name} | Secure Client Portal", "html_sha256": "8a3f9104b2c8e1d5a7f934208e6c7104b92c4e1f7a08b3c5d6e9f2a4b8c1d3e5",
        "screenshot_ref": "data/evidence/demo_login_screen.png", "screenshot_sha256": "4b7c1e9a3f2d8e05b6a7c9d1e4f8a2b5c7d0e3f6a9b1c4d8e2f5a7b0c3d6e9f1",
        "favicon_url": f"https://{t1_host}/favicon.ico", "favicon_mmh3": -373997759,
        "server_headers": {"server": "cloudflare", "cf-ray": "8d94e21a4f02a1b9-ACC"},
        "text_excerpt": f"{brand_name} Ghana Customer Support Desk. Instant crypto buy & cashout. Send verification deposit to MoMo 024 555 0142. VIP Signal Telegram @{tg_handle}.",
        "identifiers": {"phones": [momo_phone], "wallets": {"tron": [crypto_wallet]}, "telegram": [tg_handle]},
        "blocked_requests": [], "errors": [],
    }
    t1_verdict = {
        "confidence": "high", "score": 100, "rules_version": RULES_VERSION, "human_review_required": True,
        "reason_codes": ["brand_name_exact_label", "brand_named_on_page", "logo_or_favicon_match", "lure_language_on_page", "payment_identifier_not_official"],
        "evidence": {
            "brand_name_exact_label": f"hostname {t1_host} vs seed 'yellowcard'",
            "brand_named_on_page": f"page text contains '{brand_name.lower()}'",
            "logo_or_favicon_match": "favicon mmh3 -373997759 equals an official favicon",
            "lure_language_on_page": ["instant cashout", "vip signal"],
            "payment_identifier_not_official": {"phones": [momo_phone], "wallets": [crypto_wallet]},
        },
    }
    t1_enrichment = {
        "host": t1_host, "registered_domain": t1_host, "free_host_platform": None,
        "ips": ["104.21.45.17", "172.67.142.9"],
        "registration": {"domain": t1_host, "registrar": "GoDaddy.com, LLC", "registrar_abuse_email": "abuse@godaddy.com", "created": hours_ago(96)},
        "hosting": {"ip": "104.21.45.17", "network": "Cloudflare, Inc.", "host_abuse_email": "abuse@cloudflare.com"},
    }
    store.db.execute(
        "INSERT INTO threats VALUES (?,?,?,?,?,?,?,?, 'scored', ?,?,?, 100, 'high', ?, ?)",
        (t1_id, brand_id, t1_host, f"https://{t1_host}/", "certificate_transparency",
         "google_argon2026h2#3471102118", hours_ago(72), json.dumps({"priority": 55, "reasons": ["brand_name_exact_label"]}),
         json.dumps(t1_cap), json.dumps(t1_verdict), json.dumps(t1_enrichment), case_a, hours_ago(71.9)))
    audit(72, "system", "threat_detected", t1_id, {"hostname": t1_host, "source": "certificate_transparency"})
    audit(71.9, "system", "threat_scored", t1_id, {"confidence": "high", "score": 100})

    # Threat 2: yellowcard-bonus-gh.xyz (Links into Case A via same MoMo number)
    t2_host = "yellowcard-bonus-gh.xyz"
    t2_id = "thr_b7d2f9a315"
    t2_cap = {
        "url": f"https://{t2_host}/", "final_url": f"https://{t2_host}/promo",
        "status": 200, "captured_at": hours_ago(49.9), "capture_tool": "haul16-capture/0.1 (browserbase-cloud-proxy)",
        "title": f"{brand_name} Ghana: claim promotional airdrop", "html_sha256": "5c6e81a9f02d4b7c3e1a8f9b2d6c0e4a7b1d5f8c2e9a3b6d0f4a7c1e8b2d5f9",
        "screenshot_ref": "data/evidence/demo_promo_screen.png", "screenshot_sha256": "9a1b4c7d0e3f6a8b2c5d7e1f4a9b2c6d8e0f3a5b7c9d1e4f6a8b0c2d5e7f9a1",
        "favicon_url": f"https://{t2_host}/favicon.ico", "favicon_mmh3": -373997759,
        "server_headers": {"server": "cloudflare", "cf-ray": "8d96b104c90142f1-ACC"},
        "text_excerpt": f"Claim 500 GHS promo airdrop on {brand_name}. Send activation deposit via MoMo 024 555 0142. Official Support @{tg_handle}.",
        "identifiers": {"phones": [momo_phone], "wallets": {}, "telegram": [tg_handle]},
        "blocked_requests": [], "errors": [],
    }
    t2_verdict = {
        "confidence": "high", "score": 100, "rules_version": RULES_VERSION, "human_review_required": True,
        "reason_codes": ["brand_name_exact_label", "brand_named_on_page", "logo_or_favicon_match", "lure_language_on_page", "payment_identifier_not_official"],
        "evidence": {
            "brand_name_exact_label": f"hostname {t2_host} vs seed 'yellowcard'",
            "brand_named_on_page": f"page text contains '{brand_name.lower()}'",
            "logo_or_favicon_match": "favicon mmh3 -373997759 equals an official favicon",
            "lure_language_on_page": ["bonus", "promo", "airdrop"],
            "payment_identifier_not_official": {"phones": [momo_phone]},
        },
    }
    t2_enrichment = {
        "host": t2_host, "registered_domain": t2_host, "free_host_platform": None,
        "ips": ["172.67.188.24"],
        "registration": {"domain": t2_host, "registrar": "Namecheap, Inc.", "registrar_abuse_email": "abuse@namecheap.com", "created": hours_ago(52)},
        "hosting": {"ip": "172.67.188.24", "network": "Cloudflare, Inc.", "host_abuse_email": "abuse@cloudflare.com"},
    }
    store.db.execute(
        "INSERT INTO threats VALUES (?,?,?,?,?,?,?,?, 'scored', ?,?,?, 100, 'high', ?, ?)",
        (t2_id, brand_id, t2_host, f"https://{t2_host}/", "certificate_transparency",
         "google_argon2026h2#3471107339", hours_ago(50), json.dumps({"priority": 55, "reasons": ["brand_name_exact_label"]}),
         json.dumps(t2_cap), json.dumps(t2_verdict), json.dumps(t2_enrichment), case_a, hours_ago(49.9)))
    audit(50, "system", "threat_detected", t2_id, {"hostname": t2_host, "source": "certificate_transparency"})
    audit(49.9, "system", "threat_scored", t2_id, {"confidence": "high", "score": 100})
    audit(49.9, "system", "threat_linked_to_case", t2_id, {"case_id": case_a, "shared": [{"kind": "phone", "value": momo_phone}, {"kind": "telegram", "value": tg_handle}]})

    # Identifiers for Case A
    store.db.executemany("INSERT INTO identifiers VALUES (?,?,?)", [
        (t1_id, "phone", momo_phone), (t1_id, "wallet", crypto_wallet), (t1_id, "telegram", tg_handle),
        (t2_id, "phone", momo_phone), (t2_id, "telegram", tg_handle),
    ])

    # Notices for Case A
    notices_a = [
        ("ntc_reg_t1", case_a, t1_id, "registrar", "Domain registrar abuse desk", "email", "abuse@godaddy.com", 1,
         f"Abuse report: {t1_host} impersonating {brand_name}", f"Notice text for GoDaddy reporting unauthorized impersonation of {brand_name}.",
         "filed_dry_run", hours_ago(71.8), "Kofi Boateng", hours_ago(71), hours_ago(70)),
        ("ntc_host_t1", case_a, t1_id, "hosting", "Hosting provider abuse desk", "email", "abuse@cloudflare.com", 1,
         f"AUP violation: phishing page at {t1_host} (104.21.45.17)", f"Notice text for Cloudflare reporting phishing against {brand_name}.",
         "filed_dry_run", hours_ago(71.8), "Kofi Boateng", hours_ago(71), hours_ago(70)),
        ("ntc_gsb_t1", case_a, t1_id, "google_safe_browsing", "Google Safe Browsing", "web_form", "https://safebrowsing.google.com/safebrowsing/report_phish/", 1,
         f"Phishing report: https://{t1_host}/", f"Notice text for Google Safe Browsing on {brand_name} phishing.",
         "filed_dry_run", hours_ago(71.8), "Kofi Boateng", hours_ago(71), hours_ago(70)),
        ("ntc_mtn_t1", case_a, t1_id, "telco_mtn", "MTN Ghana MoMo fraud team", "email", "mmfraudteam.GH@mtn.com", 1,
         f"MoMo number used to impersonate {brand_name}: {momo_phone}", f"Notice text for MTN Ghana fraud desk regarding unauthorized cash collection.",
         "drafted", hours_ago(71.8), None, None, None),
        ("ntc_csa_t1", case_a, t1_id, "csa", "Cyber Security Authority", "web_form", "UNVERIFIED: confirm on csa.gov.gh", 0,
         f"Online fraud report: impersonation of {brand_name} ({case_a})", f"Notice text for CSA reporting criminal impersonation of licensed sandbox VASP.",
         "drafted", hours_ago(71.8), None, None, None),
        ("ntc_tg_t1", case_a, t1_id, "telegram_abuse", "Telegram Impersonation & Fraud Desk", "email", "abuse@telegram.org", 1,
         f"Fraud report: unauthorized channel {tg_handle} impersonating {brand_name}", f"Notice text for Telegram abuse reporting unauthorized brand channel.",
         "drafted", hours_ago(71.8), None, None, None),
        ("ntc_reg_t2", case_a, t2_id, "registrar", "Domain registrar abuse desk", "email", "abuse@namecheap.com", 1,
         f"Abuse report: {t2_host} impersonating {brand_name}", f"Notice text for Namecheap.",
         "filed_dry_run", hours_ago(49.8), "Ama Mensah", hours_ago(25), hours_ago(24)),
        ("ntc_host_t2", case_a, t2_id, "hosting", "Hosting provider abuse desk", "email", "abuse@cloudflare.com", 1,
         f"AUP violation: phishing page at {t2_host} (172.67.188.24)", f"Notice text for Cloudflare.",
         "rejected", hours_ago(49.8), "Kofi Boateng", hours_ago(25), None),
        ("ntc_gsb_t2", case_a, t2_id, "google_safe_browsing", "Google Safe Browsing", "web_form", "https://safebrowsing.google.com/safebrowsing/report_phish/", 1,
         f"Phishing report: https://{t2_host}/", f"Notice text for Google.",
         "approved", hours_ago(49.8), "Ama Mensah", hours_ago(1.5), None),
    ]
    for n in notices_a:
        store.db.execute(
            "INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], n[9],
             json.dumps({"threat_id": n[2], "html_sha256": t1_cap["html_sha256"], "letter_sha256": letter_sha}),
             n[10], n[11], n[12], n[13], n[14]))
        audit(71.8 if "t1" in n[0] else 49.8, "system", "notice_drafted", n[0], {"channel": n[3], "threat_id": n[2]})

    # Notice approvals & filings audit
    audit(71, "Kofi Boateng", "notice_approved", "ntc_reg_t1")
    audit(70, "Kofi Boateng", "notice_filed_dry_run", "ntc_reg_t1")
    audit(71, "Kofi Boateng", "notice_approved", "ntc_host_t1")
    audit(70, "Kofi Boateng", "notice_filed_dry_run", "ntc_host_t1")
    audit(71, "Kofi Boateng", "notice_approved", "ntc_gsb_t1")
    audit(70, "Kofi Boateng", "notice_filed_dry_run", "ntc_gsb_t1")
    audit(25, "Ama Mensah", "notice_approved", "ntc_reg_t2")
    audit(24, "Ama Mensah", "notice_filed_dry_run", "ntc_reg_t2")
    audit(25, "Kofi Boateng", "notice_rejected", "ntc_host_t2", {"reason": f"Same Cloudflare origin as {t1_host}"})
    audit(1.5, "Ama Mensah", "notice_approved", "ntc_gsb_t2")

    # Clock events for Case A
    clock(70, case_a, "ntc_reg_t1", "registrar", "filed")
    clock(66, case_a, "ntc_reg_t1", "registrar", "first_response", "Logged by operator (simulated)")
    clock(39, case_a, "ntc_reg_t1", "registrar", "removed", "Logged by operator (simulated)")
    clock(70, case_a, "ntc_host_t1", "hosting", "filed")
    clock(66, case_a, "ntc_host_t1", "hosting", "first_response", "Logged by operator (simulated)")
    clock(42, case_a, "ntc_host_t1", "hosting", "removed", "Logged by operator (simulated)")
    clock(70, case_a, "ntc_gsb_t1", "google_safe_browsing", "filed")
    clock(68, case_a, "ntc_gsb_t1", "google_safe_browsing", "first_response", "Logged by operator (simulated)")
    clock(24, case_a, "ntc_reg_t2", "registrar", "filed")
    clock(18, case_a, "ntc_reg_t2", "registrar", "first_response", "Logged by operator (simulated)")

    # =========================================================================
    # Case B: Standalone high-scoring wallet clone (yellowcard.cc)
    # =========================================================================
    case_b = "case_4be07d92a1"
    store.db.execute("INSERT INTO cases VALUES (?,?,?, 'open', ?)",
                     (case_b, brand_id, hours_ago(24), f"Suspected clone of {brand_name}"))
    audit(24, "system", "case_opened", case_b, {"first_threat": "thr_c3e9a04d77"})

    t3_host = "yellowcard.cc"
    t3_id = "thr_c3e9a04d77"
    telecel_phone = "0501239876"
    t3_cap = {
        "url": f"https://{t3_host}/", "final_url": f"https://{t3_host}/app",
        "status": 200, "captured_at": hours_ago(23.9), "capture_tool": "haul16-capture/0.1 (browserbase-cloud-proxy)",
        "title": f"{brand_name} Web Wallet", "html_sha256": "3d8b1e4f7a2c6d0e9f1a5b8c3e7d2f4a6b0c8e1f5a9b3d7c2e6a0f4b8d1e5f9a",
        "screenshot_ref": "data/evidence/demo_wallet_screen.png", "screenshot_sha256": "7a0b3c6d9e2f5a8b1c4d7e0f3a6b9c2d5e8f1a4b7c0d3e6f9a2b5c8d1e4f7a0b",
        "favicon_url": f"https://{t3_host}/favicon.ico", "favicon_mmh3": -373997759,
        "server_headers": {"server": "Vercel"},
        "text_excerpt": f"Connect your {brand_name} wallet. Instant mobile money cashout. Direct deposit support: Telecel Cash 050 123 9876. Contact on WhatsApp wa.me/233501239876.",
        "identifiers": {"phones": [telecel_phone], "wallets": {}, "telegram": [], "whatsapp": ["233501239876"]},
        "blocked_requests": [], "errors": [],
    }
    t3_verdict = {
        "confidence": "medium", "score": 65, "rules_version": RULES_VERSION, "human_review_required": True,
        "reason_codes": ["brand_name_exact_label", "brand_named_on_page", "payment_identifier_not_official"],
        "evidence": {
            "brand_name_exact_label": f"hostname {t3_host} vs seed 'yellowcard'",
            "brand_named_on_page": f"page text contains '{brand_name.lower()}'",
            "payment_identifier_not_official": {"phones": [telecel_phone]},
        },
    }
    t3_enrichment = {
        "host": t3_host, "registered_domain": t3_host, "free_host_platform": None,
        "ips": ["76.76.21.21"],
        "registration": {"domain": t3_host, "registrar": "Spaceship, Inc.", "registrar_abuse_email": "abuse@spaceship.com", "created": hours_ago(30)},
        "hosting": {"ip": "76.76.21.21", "network": "Vercel, Inc.", "host_abuse_email": "abuse@vercel.com"},
    }
    store.db.execute(
        "INSERT INTO threats VALUES (?,?,?,?,?,?,?,?, 'scored', ?,?,?, 65, 'medium', ?, ?)",
        (t3_id, brand_id, t3_host, f"https://{t3_host}/", "certificate_transparency",
         "google_xenon2026h2#89012431", hours_ago(24), json.dumps({"priority": 50, "reasons": ["brand_name_exact_label"]}),
         json.dumps(t3_cap), json.dumps(t3_verdict), json.dumps(t3_enrichment), case_b, hours_ago(23.9)))
    audit(24, "system", "threat_detected", t3_id, {"hostname": t3_host, "source": "certificate_transparency"})
    audit(23.9, "system", "threat_scored", t3_id, {"confidence": "medium", "score": 65})

    store.db.execute("INSERT INTO identifiers VALUES (?,?,?)", (t3_id, "phone", telecel_phone))

    notices_b = [
        ("ntc_reg_t3", case_b, t3_id, "registrar", "Domain registrar abuse desk", "email", "abuse@spaceship.com", 1,
         f"Abuse report: {t3_host} impersonating {brand_name}", f"Notice text for Spaceship regarding {t3_host}.",
         "drafted", hours_ago(23.8), None, None, None),
        ("ntc_host_t3", case_b, t3_id, "hosting", "Hosting provider abuse desk", "email", "abuse@vercel.com", 1,
         f"AUP violation: phishing page at {t3_host} (76.76.21.21)", f"Notice text for Vercel regarding phishing host.",
         "drafted", hours_ago(23.8), None, None, None),
        ("ntc_gsb_t3", case_b, t3_id, "google_safe_browsing", "Google Safe Browsing", "web_form", "https://safebrowsing.google.com/safebrowsing/report_phish/", 1,
         f"Phishing report: https://{t3_host}/", f"Notice text for Google Safe Browsing.",
         "drafted", hours_ago(23.8), None, None, None),
        ("ntc_tel_t3", case_b, t3_id, "telco_telecel", "Telecel Ghana fraud team", "email", "UNVERIFIED: confirm on Telecel Ghana's official site", 0,
         f"Telecel Cash number used to impersonate {brand_name}: {telecel_phone}", f"Notice text for Telecel Ghana fraud desk.",
         "drafted", hours_ago(23.8), None, None, None),
        ("ntc_csa_t3", case_b, t3_id, "csa", "Cyber Security Authority", "web_form", "UNVERIFIED: confirm on csa.gov.gh", 0,
         f"Online fraud report: impersonation of {brand_name} ({case_b})", f"Notice text for CSA Ghana.",
         "drafted", hours_ago(23.8), None, None, None),
    ]
    for n in notices_b:
        store.db.execute(
            "INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], n[9],
             json.dumps({"threat_id": n[2], "html_sha256": t3_cap["html_sha256"], "letter_sha256": letter_sha}),
             n[10], n[11], n[12], n[13], n[14]))
        audit(23.8, "system", "notice_drafted", n[0], {"channel": n[3], "threat_id": n[2]})

    # =========================================================================
    # Threat 4: yellowcard-apparel.shop (Low score legitimate business)
    # =========================================================================
    t4_host = "yellowcard-apparel.shop"
    t4_id = "thr_d4e8a1f729"
    t4_cap = {
        "url": f"https://{t4_host}/", "final_url": f"https://{t4_host}/",
        "status": 200, "captured_at": hours_ago(12), "capture_tool": "haul16-capture/0.1 (browserbase-cloud-proxy)",
        "title": "Yellowcard Apparel | Casual Streetwear", "html_sha256": "4a7b0c3d6e9f1a4b7c0d3e6f9a2b5c8d1e4f7a0b3c6d9e2f5a8b1c4d7e0f3a6b",
        "screenshot_ref": "data/evidence/demo_apparel_screen.png", "screenshot_sha256": "8b1c4d7e0f3a6b9c2d5e8f1a4b7c0d3e6f9a2b5c8d1e4f7a0b3c6d9e2f5a8b1c",
        "favicon_url": f"https://{t4_host}/favicon.ico", "favicon_mmh3": 11223344,
        "server_headers": {"server": "Shopify"},
        "text_excerpt": "Yellowcard Apparel. Premium street clothing and hoodies. Summer sale on all merchandise.",
        "identifiers": {"phones": [], "wallets": {}, "telegram": []},
        "blocked_requests": [], "errors": [],
    }
    t4_verdict = {
        "confidence": "low", "score": 25, "rules_version": RULES_VERSION, "human_review_required": False,
        "reason_codes": ["brand_name_exact_label"],
        "evidence": {"brand_name_exact_label": f"hostname {t4_host} vs seed 'yellowcard'"},
    }
    t4_enrichment = {
        "host": t4_host, "registered_domain": t4_host, "free_host_platform": None,
        "ips": ["23.227.38.65"],
        "registration": {"domain": t4_host, "registrar": "GoDaddy.com LLC", "registrar_abuse_email": "abuse@godaddy.com", "created": hours_ago(300)},
        "hosting": {"ip": "23.227.38.65", "network": "Shopify, Inc.", "host_abuse_email": "abuse@shopify.com"},
    }
    store.db.execute(
        "INSERT INTO threats VALUES (?,?,?,?,?,?,?,?, 'scored', ?,?,?, 25, 'low', NULL, ?)",
        (t4_id, brand_id, t4_host, f"https://{t4_host}/", "certificate_transparency",
         "cloudflare_nimbus2026#11409214", hours_ago(12.1), json.dumps({"priority": 25, "reasons": ["brand_name_exact_label"]}),
         json.dumps(t4_cap), json.dumps(t4_verdict), json.dumps(t4_enrichment), hours_ago(12)))
    audit(12.1, "system", "threat_detected", t4_id, {"hostname": t4_host, "source": "certificate_transparency"})
    audit(12, "system", "threat_scored", t4_id, {"confidence": "low", "score": 25})

    # =========================================================================
    # Threat 5: yellowcard-login.online (Unknown confidence: DNS resolving error)
    # =========================================================================
    t5_host = "yellowcard-login.online"
    t5_id = "thr_e5f9b2a830"
    t5_cap = {
        "url": f"https://{t5_host}/", "final_url": None, "status": None, "captured_at": hours_ago(6),
        "capture_tool": "haul16-capture/0.1 (browserbase-cloud-proxy)", "title": None, "html_sha256": None,
        "screenshot_ref": None, "screenshot_sha256": None, "favicon_url": None, "favicon_mmh3": None,
        "server_headers": {}, "text_excerpt": "", "identifiers": {}, "blocked_requests": [],
        "errors": [f"Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://{t5_host}/"],
    }
    t5_verdict = {
        "confidence": "unknown", "score": 30, "rules_version": RULES_VERSION, "human_review_required": True,
        "reason_codes": ["brand_name_exact_label"],
        "evidence": {
            "brand_name_exact_label": f"hostname {t5_host} vs seed 'yellowcard'",
            "page_unreachable": f"Error: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://{t5_host}/",
        },
    }
    t5_enrichment = {
        "host": t5_host, "registered_domain": t5_host, "free_host_platform": None, "ips": [],
        "registration": {"domain": t5_host, "rdap_status": 404},
    }
    store.db.execute(
        "INSERT INTO threats VALUES (?,?,?,?,?,?,?,?, 'error', ?,?,?, 30, 'unknown', NULL, ?)",
        (t5_id, brand_id, t5_host, f"https://{t5_host}/", "certificate_transparency",
         "sectigo_tiger2026h2#6510923", hours_ago(6.1), json.dumps({"priority": 30, "reasons": ["brand_name_exact_label"]}),
         json.dumps(t5_cap), json.dumps(t5_verdict), json.dumps(t5_enrichment), hours_ago(6)))
    audit(6.1, "system", "threat_detected", t5_id, {"hostname": t5_host, "source": "certificate_transparency"})
    audit(6, "system", "threat_scored", t5_id, {"confidence": "unknown", "score": 30})

    store.db.commit()
    return {"ok": True, "brand": brand_name, "threats": 5, "cases": 2, "notices": 14}


if __name__ == "__main__":
    res = seed_database()
    print("Database seeded successfully:", res)
