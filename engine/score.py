"""Stage C: combine domain reasons (Stage A) with page evidence (Stage B) into a verdict.

Deterministic and versioned. Every reason code points at captured evidence.
There is no "safe" verdict: the lowest outcome is "low" or "unknown".
"""
import re
from dataclasses import dataclass

from engine.capture import Capture
from engine.extract import Identifiers, unofficial
from engine.matcher import RULES_VERSION, Brand, Candidate
from engine.normalize import skeleton

PAGE_WEIGHTS = {
    "logo_or_favicon_match": 35,
    "payment_identifier_not_official": 35,
    "brand_named_on_page": 20,
    "lure_language_on_page": 15,
}
STRONG = {"logo_or_favicon_match", "payment_identifier_not_official", "homoglyph_of_brand"}

LURES = re.compile(
    r"double your|guaranteed (?:returns?|profit)|\bbonus\b|airdrop|send (?:your )?deposit|activate your|"
    r"limited (?:time|offer|bonus)|withdraw(?:al)? fee|verify your (?:wallet|account)|giveaway|"
    r"customer support desk|support agent",
    re.I,
)


@dataclass
class Verdict:
    confidence: str                 # high | medium | low | unknown
    score: int
    reason_codes: list[str]
    evidence: dict                  # reason code -> the captured fact behind it
    rules_version: str = RULES_VERSION
    human_review_required: bool = True


def score(brand: Brand, cand: Candidate | None, cap: Capture) -> Verdict:
    reasons: dict[str, object] = {}
    if cand:
        for r in cand.reasons:
            reasons[r] = f"hostname {cand.display} vs seed '{cand.seed}'"

    if cap.errors and not cap.html_sha256:
        return Verdict("unknown", cand.priority if cand else 0, sorted(reasons),
                       {**reasons, "page_unreachable": cap.errors[0]})

    page = skeleton(f"{cap.title or ''}\n{cap.text_excerpt}".lower())
    names = [brand.display_name.lower()] + brand.seed_terms
    named = [n for n in names if n in page]
    if named:
        reasons["brand_named_on_page"] = f"page text contains '{named[0]}'"

    if cap.favicon_mmh3 is not None and cap.favicon_mmh3 in brand.official_favicon_mmh3:
        reasons["logo_or_favicon_match"] = f"favicon mmh3 {cap.favicon_mmh3} equals an official favicon"

    ids = Identifiers(**cap.identifiers) if cap.identifiers else Identifiers()
    foreign = unofficial(ids, brand.official_numbers, brand.official_wallets)
    # A stray number only matters on a page that is posing as the brand.
    if (foreign["phones"] or foreign["wallets"]) and (named or "logo_or_favicon_match" in reasons):
        reasons["payment_identifier_not_official"] = foreign

    lure_hits = sorted({m.group(0).lower() for m in LURES.finditer(cap.text_excerpt or "")})
    if lure_hits:
        reasons["lure_language_on_page"] = lure_hits[:5]

    total = (cand.priority if cand else 0) + sum(PAGE_WEIGHTS.get(r, 0) for r in reasons)
    strong = len(STRONG & reasons.keys())
    if strong >= 2 or (strong == 1 and named and total >= 70):
        conf = "high"
    elif named and total >= 40:
        conf = "medium"
    else:
        conf = "low"
    return Verdict(conf, min(total, 100), sorted(reasons), reasons)
