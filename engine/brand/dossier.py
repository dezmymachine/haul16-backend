"""Turn collected page text into structured, cited facts with the LLM (when a key is configured).

Defences against a hostile or sloppy page: the model only sees text; every item must cite one of the URLs we supplied; and each value
must literally appear in the cited page. Anything else is dropped, not trusted.
"""
import re

from pydantic import BaseModel, Field

from engine.llm import LLM


class Cited(BaseModel):
    value: str
    source_url: str


class Leader(BaseModel):
    name: str
    title: str | None = None
    source_url: str


class Extraction(BaseModel):
    legal_names: list[Cited] = Field(default_factory=list)
    trading_names: list[Cited] = Field(default_factory=list)
    registration_numbers: list[Cited] = Field(default_factory=list)
    licences: list[Cited] = Field(default_factory=list)
    regulators: list[Cited] = Field(default_factory=list)
    incorporated: list[Cited] = Field(default_factory=list)
    hq_addresses: list[Cited] = Field(default_factory=list)
    leadership: list[Leader] = Field(default_factory=list)
    official_phones: list[Cited] = Field(default_factory=list)
    official_emails: list[Cited] = Field(default_factory=list)
    payment_instructions: list[Cited] = Field(default_factory=list)
    summary: str | None = None


INSTRUCTION = (
    "From the pages of one company's own website, extract: legal company names, other trading names, company registration numbers, "
    "regulatory licences or registrations (with the regulator named), regulators, incorporation date, head-office and branch addresses, "
    "leadership (directors, executives, founders, with titles), official phone numbers, official email addresses, and any instructions "
    "telling customers where to send money (phone numbers, bank accounts, wallets). For every item give the exact source_url of the page "
    "it came from. Also give a two-sentence neutral summary of what the company does. Fields are lists of {value, source_url} unless noted."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _present(value: str, text: str) -> bool:
    v = _norm(value)
    return bool(v) and v in _norm(text)


async def extract(llm: LLM, brand_id: str, pages: list[dict]) -> Extraction:
    """pages: [{url, text}]. Returns only items whose citation and literal text check out."""
    texts = {p["url"]: p["text"] for p in pages if p.get("text")}
    blob = "\n\n".join(f"SOURCE: {u}\n{t[:6000]}" for u, t in list(texts.items())[:8])[:28_000]
    raw = await llm.complete_json("brand_dossier", INSTRUCTION, blob, Extraction, brand_id)

    def keep(items: list[Cited]) -> list[Cited]:
        return [i for i in items if i.source_url in texts and _present(i.value, texts[i.source_url])]

    out = Extraction(summary=raw.summary)
    for f in ("legal_names", "trading_names", "registration_numbers", "licences", "regulators", "incorporated", "hq_addresses",
              "official_phones", "official_emails", "payment_instructions"):
        setattr(out, f, keep(getattr(raw, f)))
    out.leadership = [p for p in raw.leadership if p.source_url in texts and _present(p.name, texts[p.source_url])]
    return out
