"""Evidence pack: everything a person (or a platform's form) needs to act on one notice, as a single zip."""
import io
import json
import zipfile
from pathlib import Path

from engine.settings import ROOT

LETTERS = ROOT / "data" / "letters"


def letter_file(brand: dict | None) -> Path | None:
    """The uploaded authorisation letter on disk, if this server has it."""
    if not brand or not brand.get("authorisation_letter_sha256"):
        return None
    hits = sorted(LETTERS.glob(f"{brand['id']}-{brand['authorisation_letter_sha256'][:12]}-*")) if LETTERS.exists() else []
    return hits[0] if hits else None


def build_pack(notice: dict, threat: dict, brand: dict | None) -> bytes:
    cap, verdict, enr = threat.get("capture") or {}, threat.get("verdict") or {}, threat.get("enrichment") or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("notice.txt", f"To: {notice['recipient']}\nChannel: {notice['channel_name']} ({notice['method']})\nSubject: {notice['subject']}\n\n{notice['body']}\n")
        z.writestr("evidence.json", json.dumps({
            "notice_id": notice["id"], "case_id": notice["case_id"], "status": notice["status"],
            "site": {"hostname": threat["hostname"], "url": cap.get("final_url") or cap.get("url") or threat.get("url"), "detected_at": threat["detected_at"], "source": threat["source"]},
            "capture": {k: cap.get(k) for k in ("captured_at", "capture_tool", "title", "html_sha256", "screenshot_sha256", "favicon_mmh3", "identifiers", "status")},
            "verdict": {k: verdict.get(k) for k in ("confidence", "score", "reason_codes", "evidence", "rules_version")},
            "enrichment": enr,
            "authorisation_letter_sha256": (brand or {}).get("authorisation_letter_sha256"),
        }, indent=2, default=str))
        ref = cap.get("screenshot_ref")
        if ref:
            png = ROOT / ref
            html = png.with_suffix(".html")
            if png.exists():
                z.write(png, "screenshot.png")
            if html.exists():
                z.write(html, "page-source.html")
        letter = letter_file(brand)
        if letter:
            z.write(letter, f"authorisation-letter{letter.suffix or ''}")
        z.writestr("README.txt", "HAUL16 evidence pack\n\nnotice.txt is the text to send. evidence.json holds the hashes: the SHA-256 values match the files here and the "
                                 "notice. screenshot.png and page-source.html are what was captured, untouched. Verify with sha256sum.\n")
    return buf.getvalue()
