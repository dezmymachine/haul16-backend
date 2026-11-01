"""Draft takedown notices from the channel catalogue. Templates only: no model output.

Every claim line is generated from a reason code plus the captured evidence behind
it. If a field is not in the evidence, the notice says "unknown".
"""
import hashlib
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHANNELS_PATH = ROOT / "channels.yaml"

# Ghana mobile prefixes by network. Number portability means this is a hint, not proof.
TELCO_PREFIX = {
    "mtn": ("024", "025", "053", "054", "055", "059"),
    "telecel": ("020", "050"),
    "at": ("026", "027", "056", "057"),
}

CLAIM_TEXT = {
    "logo_or_favicon_match": "The page uses the same site icon as {brand}'s official site (favicon fingerprint {v}).",
    "payment_identifier_not_official": "The page asks for payment to identifiers {brand} has not registered: {v}.",
    "brand_named_on_page": "The page presents itself as {brand} ({v}).",
    "lure_language_on_page": "The page uses investment-lure wording: {v}.",
    "homoglyph_of_brand": "The hostname imitates {brand}'s name using look-alike characters ({v}).",
    "typo_of_brand": "The hostname is a misspelling of {brand}'s name ({v}).",
    "brand_name_exact_label": "The hostname uses {brand}'s name on a domain {brand} does not own ({v}).",
    "brand_name_in_domain": "The hostname contains {brand}'s name ({v}).",
    "brand_plus_lure_word": "The hostname combines {brand}'s name with a lure word ({v}).",
    "free_hosting_subdomain": "The page sits on a free hosting subdomain ({v}).",
}


def load_channels() -> list[dict]:
    return yaml.safe_load(CHANNELS_PATH.read_text())["channels"]


def _fmt(v) -> str:
    if isinstance(v, dict):
        parts = [", ".join(x) for x in v.values() if x]
        return "; ".join(p for p in parts if p) or "none"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


def claims(brand_name: str, verdict: dict) -> list[str]:
    out = []
    for code in verdict.get("reason_codes", []):
        if code in CLAIM_TEXT:
            out.append(CLAIM_TEXT[code].format(brand=brand_name, v=_fmt(verdict["evidence"].get(code, ""))))
    return out


def letter(brand_cfg: dict) -> tuple[str, str]:
    if brand_cfg.get("authorisation_letter_sha256"):          # brands onboarded through the API store the hash directly
        return brand_cfg.get("authorisation_letter") or "uploaded letter", brand_cfg["authorisation_letter_sha256"]
    ref = brand_cfg.get("authorisation_letter")
    if not ref:
        return "not on file", "unknown"
    p = ROOT / ref
    return ref, hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "file missing"


def facts_for(threat: dict, brand, brand_cfg: dict, case_id: str) -> dict:
    cap, verdict, enr = threat.get("capture") or {}, threat.get("verdict") or {}, threat.get("enrichment") or {}
    reg, hosting = enr.get("registration") or {}, enr.get("hosting") or {}
    foreign = (verdict.get("evidence") or {}).get("payment_identifier_not_official") or {}
    letter_ref, letter_sha = letter(brand_cfg)
    claim_lines = claims(brand.display_name, verdict)
    return {
        "brand": brand.display_name,
        "brand_domain": ", ".join(brand.official_domains) or "unknown",
        "host": threat["hostname"],
        "url": cap.get("final_url") or cap.get("url") or threat.get("url") or f"https://{threat['hostname']}/",
        "detected_at": threat["detected_at"],
        "captured_at": cap.get("captured_at") or "unknown",
        "html_sha256": cap.get("html_sha256") or "unknown",
        "screenshot_sha256": cap.get("screenshot_sha256") or "unknown",
        "screenshot_ref": cap.get("screenshot_ref") or "unknown",
        "claims": "\n".join(f"- {c}" for c in claim_lines) or "- (no evidence-backed claims)",
        "claim_list": claim_lines,
        "phones": ", ".join(foreign.get("phones", [])) or "none",
        "wallets": ", ".join(foreign.get("wallets", [])) or "none",
        "phone_list": foreign.get("phones", []),
        "wallet_list": foreign.get("wallets", []),
        "registrar": reg.get("registrar") or "unknown",
        "registrar_abuse_email": reg.get("registrar_abuse_email") or None,
        # Only a domain the registry actually knows has a registrar to write to.
        "registered_domain": (reg.get("domain") if reg.get("registrar") else None) or "unknown",
        "created": reg.get("created") or "unknown",
        "network": hosting.get("network") or "unknown",
        "host_abuse_email": hosting.get("host_abuse_email") or None,
        "ip": (enr.get("ips") or ["unknown"])[0],
        "letter_ref": letter_ref,
        "letter_sha256": letter_sha,
        "case_id": case_id,
    }


def applies(rule: str, f: dict) -> bool:
    phones = f["phone_list"]
    return {
        "has_url": f["url"] != "unknown",
        "has_registered_domain": f["registered_domain"] != "unknown",
        "has_hosting": f["network"] != "unknown",
        "has_payment_identifier": bool(phones or f["wallet_list"]),
        "has_phone_mtn": any(p.startswith(TELCO_PREFIX["mtn"]) for p in phones),
        "has_phone_telecel": any(p.startswith(TELCO_PREFIX["telecel"]) for p in phones),
        "has_phone_at": any(p.startswith(TELCO_PREFIX["at"]) for p in phones),
    }.get(rule, False)


def draft(threat: dict, brand, brand_cfg: dict, case_id: str) -> list[dict]:
    f = facts_for(threat, brand, brand_cfg, case_id)
    fmt = defaultdict(lambda: "unknown", {k: v for k, v in f.items() if isinstance(v, str)})
    out = []
    for ch in load_channels():
        if not applies(ch["applies_when"], f):
            continue
        recipient = ch.get("recipient") or f.get(ch.get("recipient_from", "")) or "unknown: no abuse contact in RDAP"
        out.append({
            "channel": ch["id"],
            "channel_name": ch["name"],
            "method": ch["method"],
            "recipient": recipient,
            "recipient_verified": bool(ch.get("verified")) and not recipient.startswith("unknown"),
            "subject": ch["subject"].format_map(fmt),
            "body": ch["body"].format_map(fmt),
            "evidence_refs": {
                "threat_id": threat["id"], "html_sha256": f["html_sha256"],
                "screenshot_sha256": f["screenshot_sha256"], "screenshot_ref": f["screenshot_ref"],
                "letter_sha256": f["letter_sha256"],
            },
            "evidence_checklist": ch.get("evidence_checklist", []),
            "expected_response": ch.get("expected_response"),
        })
    return out
