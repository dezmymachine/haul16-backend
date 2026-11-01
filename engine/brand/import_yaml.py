"""Seed a brand (and its confirmed identity facts) from a brand.yaml. Used for the fictional demo brand and for scripted setups.

  uv run python -m engine.brand.import_yaml [path/to/brand.yaml]
"""
import hashlib
import sys
from pathlib import Path

import yaml

from engine.brand.repo import BrandRepo, now
from engine.settings import ROOT
from engine.store import Store


def import_yaml(store: Store, path: Path = ROOT / "brand.yaml") -> str:
    cfg = yaml.safe_load(Path(path).read_text())
    repo = BrandRepo(store)
    bid = cfg["brand_id"]
    if repo.get(bid):
        return bid
    domains = cfg.get("official_domains", [])
    repo.create(cfg["display_name"], domains[0] if domains else None, None, fictional=bool(cfg.get("fictional")), brand_id=bid)
    ref = cfg.get("authorisation_letter")
    sha = hashlib.sha256((ROOT / ref).read_bytes()).hexdigest() if ref and (ROOT / ref).exists() else None
    repo.update(bid, seed_terms=[s.lower() for s in cfg.get("seed_terms", [])], status="ready", ownership_status="verified", verify_method="seed",
                verified_at=now(), authorisation_letter_ref=ref, authorisation_letter_sha256=sha)
    fact = lambda kind, value, label: repo.upsert_fact(bid, kind, str(value), collector="yaml", origin="user", confidence=1.0, confirmed=True, label=label)  # noqa: E731
    for d in domains:
        fact("domain", d, "Official domain")
    for d in cfg.get("allowlist_domains") or []:
        fact("partner", d, "Allow-listed partner")
    for n in cfg.get("official_numbers") or []:
        fact("phone", n, "Official number")
    for w in cfg.get("official_wallets") or []:
        fact("wallet", w, "Official wallet")
    for platform, handle in (cfg.get("official_handles") or {}).items():
        repo.upsert_fact(bid, "social", str(handle), collector="yaml", origin="user", confidence=1.0, confirmed=True, label=f"{platform} {handle}",
                         data={"platform": platform, "handle": handle})
    for h in cfg.get("official_favicon_mmh3") or []:
        repo.add_asset(bid, "favicon", f"yaml:favicon:{h}", None, None, int(h), {"from": "brand.yaml"})
    repo.save_version(bid, {"imported_from": str(path), "steps": {}, "counts": {}})
    repo.db.execute("UPDATE brands SET is_active=TRUE WHERE id=? AND NOT EXISTS (SELECT 1 FROM brands WHERE is_active)", (bid,))
    return bid


if __name__ == "__main__":
    print(import_yaml(Store(), Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand.yaml"))
