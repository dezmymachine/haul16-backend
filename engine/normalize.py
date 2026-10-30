"""Hostname normalisation: punycode, homoglyph skeletons, suffix stripping."""
import re
import unicodedata

import idna

# Small, explicit subset of Unicode confusables (UTS #39) that matter for brand names.
CONFUSABLES = {
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ї": "i", "ј": "j",
    "ԁ": "d", "ѕ": "s", "ԛ": "q", "ԝ": "w", "һ": "h", "ӏ": "l",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x", "ω": "w",
    # Latin oddities
    "ı": "i", "ɡ": "g", "ł": "l", "ø": "o", "đ": "d", "ß": "ss",
}
# ASCII tricks: applied only when building the skeleton, never to the display value.
ASCII_LOOKALIKES = [("rn", "m"), ("vv", "w"), ("cl", "d"), ("0", "o"), ("1", "l"),
                    ("3", "e"), ("5", "s"), ("8", "b"), ("@", "a")]

# Multi-label public suffixes we care about. Not a full PSL, enough for the spike.
MULTI_SUFFIXES = {
    "com.gh", "edu.gh", "org.gh", "gov.gh", "co.uk", "org.uk", "com.ng", "co.ke",
    "co.za", "com.au", "com.br", "co.in",
}
# Hosts where anyone can get a subdomain for free. The subdomain is attacker-chosen.
FREE_HOSTS = {
    "netlify.app", "vercel.app", "web.app", "firebaseapp.com", "github.io", "pages.dev",
    "workers.dev", "duckdns.org", "blogspot.com", "wixsite.com", "weebly.com",
    "glitch.me", "onrender.com", "herokuapp.com", "ngrok-free.app", "ngrok.io",
    "trycloudflare.com", "square.site", "godaddysites.com", "mystrikingly.com",
    "carrd.co", "webflow.io", "framer.website", "replit.app", "surge.sh",
}

_SPLIT = re.compile(r"[-_.]+")


def to_unicode(hostname: str) -> str:
    """Lowercase, drop wildcard and trailing dot, decode xn-- labels when possible."""
    host = hostname.strip().lower().rstrip(".")
    if host.startswith("*."):
        host = host[2:]
    labels = []
    for label in host.split("."):
        if label.startswith("xn--"):
            try:
                label = idna.decode(label)
            except idna.IDNAError:
                pass
        labels.append(label)
    return ".".join(labels)


def skeleton(text: str) -> str:
    """Map a string to the ASCII form a human would read it as."""
    out = []
    for ch in text:
        ch = CONFUSABLES.get(ch, ch)
        # Strip accents: é -> e
        ch = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(ch)
    s = "".join(out)
    for src, dst in ASCII_LOOKALIKES:
        s = s.replace(src, dst)
    return s


def split_host(host: str) -> tuple[str, str, bool]:
    """Return (attacker_part, suffix, on_free_host).

    attacker_part is every label the registrant chose, e.g.
      login.nkwaex-support.com     -> ("login.nkwaex-support", "com", False)
      nkwaex-login.netlify.app     -> ("nkwaex-login", "netlify.app", True)
    """
    labels = host.split(".")
    for n in (3, 2):
        if len(labels) > n:
            suffix = ".".join(labels[-n:])
            if suffix in FREE_HOSTS:
                return ".".join(labels[:-n]), suffix, True
    if len(labels) > 2 and ".".join(labels[-2:]) in MULTI_SUFFIXES:
        return ".".join(labels[:-2]), ".".join(labels[-2:]), False
    if len(labels) > 1:
        return ".".join(labels[:-1]), labels[-1], False
    return host, "", False


def tokens(attacker_part: str) -> list[str]:
    """Pieces to compare against seed terms: each label, each hyphen piece, and joins."""
    out = []
    for label in attacker_part.split("."):
        if not label:
            continue
        out.append(label)
        parts = [p for p in _SPLIT.split(label) if p]
        if len(parts) > 1:
            out.extend(parts)
            out.append("".join(parts))
    # dedupe, keep order
    return list(dict.fromkeys(out))
