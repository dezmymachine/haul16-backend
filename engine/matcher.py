"""Stage A: fast candidate filter for lookalike hostnames.

Deterministic and versioned. It decides only whether a hostname is worth
enriching, with reason codes. Confidence about a clone comes later, from page
evidence (Stage B/C), never from the domain name alone.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rapidfuzz.distance import DamerauLevenshtein

from engine.normalize import skeleton, split_host, to_unicode, tokens

RULES_VERSION = "2026-10-30.1"

LURE_WORDS = {
    "login", "signin", "support", "help", "helpdesk", "care", "customer", "service",
    "bonus", "promo", "reward", "airdrop", "giveaway", "invest", "investment", "profit",
    "wallet", "app", "secure", "verify", "verification", "kyc", "account", "update",
    "gh", "ghana", "official", "pay", "momo", "withdraw", "deposit", "trade", "trading",
    "crypto", "usdt", "claim", "refund", "online", "web", "portal", "mobile",
}

# Priority weights for Stage A only. Final confidence needs page evidence.
WEIGHTS = {
    "homoglyph_of_brand": 40,
    "typo_of_brand": 30,
    "brand_name_exact_label": 30,
    "brand_name_in_domain": 20,
    "brand_plus_lure_word": 25,
    "free_hosting_subdomain": 10,
}


@dataclass
class Brand:
    brand_id: str
    display_name: str
    seed_terms: list[str]
    official_domains: list[str]
    allowlist_domains: list[str] = field(default_factory=list)
    official_numbers: list[str] = field(default_factory=list)
    official_wallets: list[str] = field(default_factory=list)
    official_favicon_mmh3: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Brand":
        data = yaml.safe_load(Path(path).read_text())
        return cls(
            brand_id=data["brand_id"],
            display_name=data["display_name"],
            seed_terms=[s.lower() for s in data["seed_terms"]],
            official_domains=[d.lower() for d in data.get("official_domains", [])],
            allowlist_domains=[d.lower() for d in data.get("allowlist_domains", []) or []],
            official_numbers=[str(n) for n in data.get("official_numbers", []) or []],
            official_wallets=list(data.get("official_wallets", []) or []),
            official_favicon_mmh3=[int(h) for h in data.get("official_favicon_mmh3", []) or []],
        )


@dataclass
class Candidate:
    hostname: str          # as seen in the source
    display: str           # unicode form
    seed: str
    reasons: list[str]
    priority: int
    rules_version: str = RULES_VERSION


def _max_edits(seed: str) -> int:
    if len(seed) >= 9:
        return 2
    if len(seed) >= 5:
        return 1
    return 0


def _trigrams(s: str) -> set[str]:
    return {s[i:i + 3] for i in range(len(s) - 2)}


class Matcher:
    def __init__(self, brand: Brand):
        self.brand = brand
        self.trusted = brand.official_domains + brand.allowlist_domains
        self.seed_tri = {s: _trigrams(s) for s in brand.seed_terms}

    def is_trusted(self, host: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in self.trusted)

    def match(self, hostname: str) -> Candidate | None:
        host = to_unicode(hostname)
        if self.is_trusted(host):
            return None
        attacker, _suffix, free_host = split_host(host)
        toks = tokens(attacker)
        if not toks:
            return None

        best: Candidate | None = None
        for seed in self.brand.seed_terms:
            reasons = self._reasons(seed, toks)
            if not reasons:
                continue
            if free_host:
                reasons.append("free_hosting_subdomain")
            if self._has_lure(seed, toks):
                reasons.append("brand_plus_lure_word")
            if "brand_name_exact_label" in reasons:
                # nkwaex-support yields both the label "nkwaex" and the join "nkwaexsupport".
                reasons = [r for r in reasons if r != "brand_name_in_domain"]
            priority = sum(WEIGHTS[r] for r in set(reasons))
            if best is None or priority > best.priority:
                best = Candidate(hostname, host, seed, sorted(set(reasons)), min(priority, 100))
        return best

    def _reasons(self, seed: str, toks: list[str]) -> list[str]:
        reasons = []
        k = _max_edits(seed)
        need = max(1, len(self.seed_tri[seed]) - 3 * max(k, 1))
        for tok in toks:
            if tok == seed:
                reasons.append("brand_name_exact_label")
                continue
            if seed in tok:
                reasons.append("brand_name_in_domain")
                continue
            sk = skeleton(tok)
            if sk == seed or (seed in sk and len(sk) > len(seed)):
                reasons.append("homoglyph_of_brand")
                continue
            if k == 0:
                continue
            # Cheap prefilter before edit distance.
            if len(_trigrams(sk) & self.seed_tri[seed]) < need:
                continue
            if any(DamerauLevenshtein.distance(seed, core, score_cutoff=k) <= k
                   for core in self._cores(sk)):
                reasons.append("typo_of_brand")
        return reasons

    @staticmethod
    def _has_lure(seed: str, toks: list[str]) -> bool:
        """nkwaex-support, nkwaexsupport, login.nkwaex all count."""
        if any(t in LURE_WORDS for t in toks):
            return True
        for t in toks:
            sk = skeleton(t)
            if seed in sk:
                rest = sk.replace(seed, "")
                if any(lure in rest for lure in LURE_WORDS if len(lure) >= 4):
                    return True
        return False

    @staticmethod
    def _cores(tok: str) -> list[str]:
        """The token, plus the token with one lure word cut off either end.

        Typo matching runs only on these, never on arbitrary substrings: a sliding
        window flags anything containing most of the brand's letters, which is
        thousands of false hits per hour on the CT firehose.
        """
        cores = [tok]
        for lure in LURE_WORDS:
            if len(lure) < 3:
                continue
            if tok.endswith(lure) and len(tok) > len(lure):
                cores.append(tok[: -len(lure)])
            if tok.startswith(lure) and len(tok) > len(lure):
                cores.append(tok[len(lure):])
        return cores
