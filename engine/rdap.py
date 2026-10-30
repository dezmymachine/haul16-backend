"""Who to send the notice to: registrar and hosting network, from public RDAP.

Free, no key. Every field may come back None; notices then say "unknown" rather
than guess.
"""
import asyncio
import ipaddress
import socket

import httpx

from engine.normalize import split_host

RDAP = "https://rdap.org"


def registrable(host: str) -> tuple[str | None, str | None]:
    """(registered domain, free host platform). For a free host the registrar is the platform's."""
    attacker, suffix, free = split_host(host)
    if free:
        return None, suffix
    if not suffix:
        return None, None
    return f"{attacker.split('.')[-1]}.{suffix}", None


def _vcard(entity: dict, field: str) -> str | None:
    for item in (entity.get("vcardArray") or [None, []])[1]:
        if item and item[0] == field:
            return str(item[3])
    return None


def _entity(entities: list, role: str) -> dict | None:
    for e in entities or []:
        if role in (e.get("roles") or []):
            return e
        found = _entity(e.get("entities"), role)
        if found:
            return found
    return None


def _abuse(entities: list) -> str | None:
    e = _entity(entities, "abuse")
    return _vcard(e, "email") if e else None


async def domain_info(client: httpx.AsyncClient, domain: str) -> dict:
    r = await client.get(f"{RDAP}/domain/{domain}")
    if r.status_code != 200:
        return {"domain": domain, "rdap_status": r.status_code}
    d = r.json()
    reg = _entity(d.get("entities"), "registrar") or {}
    events = {e.get("eventAction"): e.get("eventDate") for e in d.get("events", [])}
    return {
        "domain": domain,
        "registrar": _vcard(reg, "fn"),
        "registrar_abuse_email": _abuse(d.get("entities")),
        "created": events.get("registration"),
        "status": d.get("status"),
        "nameservers": [n.get("ldhName", "").lower() for n in d.get("nameservers", [])][:4],
    }


async def ip_info(client: httpx.AsyncClient, ip: str) -> dict:
    r = await client.get(f"{RDAP}/ip/{ip}")
    if r.status_code != 200:
        return {"ip": ip, "rdap_status": r.status_code}
    d = r.json()
    return {"ip": ip, "network": d.get("name"), "country": d.get("country"),
            "host_abuse_email": _abuse(d.get("entities"))}


async def enrich(host: str) -> dict:
    domain, platform = registrable(host)
    out: dict = {"host": host, "registered_domain": domain, "free_host_platform": platform}
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        ips = list(dict.fromkeys(i[4][0] for i in infos))
    except socket.gaierror:
        ips = []
    out["ips"] = ips[:4]
    public = [ip for ip in ips if ipaddress.ip_address(ip).is_global]
    async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                 headers={"User-Agent": "haul16-rdap/0.1"}) as client:
        try:
            if domain:
                out["registration"] = await domain_info(client, domain)
            if public:
                out["hosting"] = await ip_info(client, public[0])
        except (httpx.HTTPError, ValueError) as e:
            out["error"] = repr(e)[:200]
    return out
