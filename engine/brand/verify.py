"""Proof that whoever onboards a brand controls it: a DNS TXT record on the brand's domain.

Add a TXT record with the brand's token at either `<domain>` or `_haul16-verify.<domain>`. No verified owner, no filings.
"""
from engine.brand.repo import BrandRepo, domain_of, now
from engine.clients import dnsx


def instructions(brand: dict) -> dict:
    domain = domain_of(brand["website"]) if brand.get("website") else None
    return {"domain": domain, "record_name": f"_haul16-verify.{domain}" if domain else None, "record_type": "TXT", "record_value": brand["verify_token"]}


async def check(repo: BrandRepo, brand_id: str) -> dict:
    brand = repo.get(brand_id)
    if not brand or not brand.get("website"):
        return {"verified": False, "reason": "The brand has no website to verify against."}
    domain, token = domain_of(brand["website"]), brand["verify_token"]
    values = (await dnsx.txt_values(f"_haul16-verify.{domain}")) + (await dnsx.txt_values(domain))
    ok = any(token in v for v in values)
    if ok and brand["ownership_status"] != "verified":
        repo.update(brand_id, ownership_status="verified", verify_method="dns_txt", verified_at=now())
        repo.store.audit("system", "brand_ownership_verified", brand_id, {"method": "dns_txt", "domain": domain})
    return {"verified": ok, "domain": domain, "reason": None if ok else f"No TXT record containing the token was found on {domain} or _haul16-verify.{domain} yet. DNS changes can take a few minutes."}
