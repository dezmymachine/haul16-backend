"""Telegram and WhatsApp links are tracked as threats too. They have no domain, so the "hostname" is the platform plus the handle or code."""
import re

TG = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?#].*)?$", re.I)
WA = re.compile(r"^(?:https?://)?chat\.whatsapp\.com/(?:invite/)?([A-Za-z0-9]{16,32})(?:[/?#].*)?$", re.I)


def social_host(url: str) -> str | None:
    """'t.me/<handle>' or 'chat.whatsapp.com/<code>' for a social link, else None."""
    if m := TG.match(url.strip()):
        return f"t.me/{m.group(1).lower()}"
    if m := WA.match(url.strip()):
        return f"chat.whatsapp.com/{m.group(1)}"
    return None


def is_social(host: str) -> bool:
    return host.startswith(("t.me/", "chat.whatsapp.com/"))


def capture_urls(host: str) -> list[str]:
    """Pages to capture as evidence. Telegram's /s/ page shows a channel's public posts, where wallets and numbers appear."""
    if host.startswith("t.me/"):
        h = host.split("/", 1)[1]
        return [f"https://t.me/s/{h}", f"https://t.me/{h}"]
    return [f"https://{host}"]
