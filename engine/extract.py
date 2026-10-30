"""Pull payment and contact identifiers out of captured page text and links.

Deterministic regexes only. Output feeds reason codes and the evidence pack, so a
value appears here only if it literally appears on the page.
"""
import re
from dataclasses import dataclass, field

# Ghana numbers: 0XX XXX XXXX, +233 XX XXX XXXX, 233XXXXXXXXX, with spaces or dashes.
GH_PHONE = re.compile(r"(?<![\d+])(?:\+?233[\s-]?|0)([235]\d)((?:[\s-]?\d){7})(?!\d)")
WALLETS = {
    "tron": re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])T[1-9A-HJ-NP-Za-km-z]{33}(?![1-9A-HJ-NP-Za-km-z])"),
    "ethereum": re.compile(r"(?<![0-9a-fA-Fx])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])"),
    "bitcoin": re.compile(r"(?<![0-9a-z])bc1[ac-hj-np-z02-9]{25,59}(?![0-9a-z])"),
}
TELEGRAM = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(?:\+|joinchat/)?([A-Za-z0-9_+-]{4,64})", re.I)
WHATSAPP = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send/?\?phone=|chat\.whatsapp\.com/)([0-9A-Za-z+]{6,40})", re.I)


@dataclass
class Identifiers:
    phones: list[str] = field(default_factory=list)       # normalised 0XXXXXXXXX
    wallets: dict[str, list[str]] = field(default_factory=dict)
    telegram: list[str] = field(default_factory=list)
    whatsapp: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"phones": self.phones, "wallets": self.wallets,
                "telegram": self.telegram, "whatsapp": self.whatsapp}


def normalise_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("233") and len(digits) == 12:
        digits = "0" + digits[3:]
    return digits if len(digits) == 10 and digits[0] == "0" else None


def _uniq(xs):
    return list(dict.fromkeys(xs))


def extract(text: str, links: list[str] = ()) -> Identifiers:
    blob = text + "\n" + "\n".join(links)
    ids = Identifiers()
    ids.phones = _uniq(p for m in GH_PHONE.finditer(blob) if (p := normalise_phone(m.group(0))))
    for kind, rx in WALLETS.items():
        found = _uniq(rx.findall(blob))
        if found:
            ids.wallets[kind] = found
    ids.telegram = _uniq(m.group(1) for m in TELEGRAM.finditer(blob))
    ids.whatsapp = _uniq(m.group(1) for m in WHATSAPP.finditer(blob))
    # A wa.me number is also a phone number we can compare with the register.
    for w in ids.whatsapp:
        if (p := normalise_phone(w)) and p not in ids.phones:
            ids.phones.append(p)
    return ids


def unofficial(ids: Identifiers, official_numbers: list[str], official_wallets: list[str]) -> dict:
    """Identifiers on the page that the brand never registered. The strongest clone signal."""
    off_n = {normalise_phone(n) or n for n in official_numbers}
    off_w = {w.lower() for w in official_wallets}
    return {
        "phones": [p for p in ids.phones if p not in off_n],
        "wallets": [w for ws in ids.wallets.values() for w in ws if w.lower() not in off_w],
    }
