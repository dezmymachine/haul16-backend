"""Build the detection engine's Brand (and the notice config dict) from a brand's dossier instead of brand.yaml."""
from engine.brand.repo import BrandRepo
from engine.matcher import Brand


def brand_from_db(repo: BrandRepo, brand_id: str) -> tuple[Brand, dict]:
    d = repo.detection_inputs(brand_id)
    b = d["brand"]
    brand = Brand(brand_id=b["id"], display_name=b["display_name"], seed_terms=[s.lower() for s in (b["seed_terms"] or [])],
                  official_domains=[x.lower() for x in d["official_domains"]], allowlist_domains=d["allowlist_domains"],
                  official_numbers=d["official_numbers"], official_wallets=d["official_wallets"], official_favicon_mmh3=d["favicon_mmh3"])
    cfg = {
        "brand_id": b["id"], "display_name": b["display_name"], "fictional": bool(b["fictional"]), "seed_terms": brand.seed_terms,
        "official_domains": brand.official_domains, "official_handles": d["official_handles"], "official_numbers": brand.official_numbers,
        "official_wallets": brand.official_wallets, "official_favicon_mmh3": brand.official_favicon_mmh3, "allowlist_domains": brand.allowlist_domains,
        "authorisation_letter": b.get("authorisation_letter_ref"), "authorisation_letter_sha256": b.get("authorisation_letter_sha256"),
    }
    return brand, cfg
