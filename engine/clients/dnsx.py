"""DNS lookups for the brand's domain (email provider, SPF, nameservers, ownership TXT record). No key needed."""
import asyncio

import dns.exception
import dns.resolver


def _resolve(name: str, rtype: str) -> list[str]:
    try:
        ans = dns.resolver.resolve(name, rtype, lifetime=6)
        return [r.to_text().strip('"') for r in ans]
    except (dns.exception.DNSException, Exception):
        return []


async def records(name: str, rtype: str) -> list[str]:
    return await asyncio.to_thread(_resolve, name, rtype)


async def txt_values(name: str) -> list[str]:
    out = []
    for r in await records(name, "TXT"):
        out.append(r.replace('" "', ""))
    return out
