"""Patrols: scan an active brand again on a schedule, so protection continues without anyone pressing a button.

One loop in the API process. Every minute it checks the active brand: if it has not been scanned within its interval and no scan is running,
it queues a discovery job. The interval is per brand (Settings), falling back to PATROL_HOURS; 0 turns patrols off.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from engine import jobs
from engine.settings import get_settings


def interval_hours(brand: dict) -> int:
    v = (brand.get("inputs") or {}).get("patrol_hours")
    return int(v) if v is not None else get_settings().patrol_hours


def last_scan(store, brand_id: str) -> datetime | None:
    j = jobs.latest(store, "discovery", brand_id)
    return datetime.fromisoformat(j["updated_at"]) if j else None


def next_scan(store, brand: dict) -> datetime | None:
    h = interval_hours(brand)
    if h <= 0:
        return None
    last = last_scan(store, brand["id"])
    return (last + timedelta(hours=h)) if last else datetime.now(timezone.utc)


def due(store, brand: dict, now: datetime | None = None) -> bool:
    nxt = next_scan(store, brand)
    j = jobs.latest(store, "discovery", brand["id"])
    return bool(nxt and (now or datetime.now(timezone.utc)) >= nxt and not (j and j["status"] in ("queued", "running")))


def tick(store, watch) -> bool:
    """One check. Returns True when a scan was queued."""
    from engine.brand.repo import BrandRepo
    if watch.brand.brand_id == "__none__":
        return False
    brand = BrandRepo(store).get(watch.brand.brand_id)
    if brand and brand["is_active"] and brand["status"] == "ready" and due(store, brand):
        jobs.enqueue(store, "discovery", brand["id"], {"patrol": True}, max_attempts=1)
        store.audit("system", "patrol_scan_queued", brand["id"])
        return True
    return False


async def patrol_loop(store, watch, every_s: float = 60) -> None:
    while True:
        await asyncio.sleep(every_s)
        try:
            await asyncio.to_thread(tick, store, watch)
        except Exception as e:  # noqa: BLE001  a patrol hiccup must never take the API down
            print(f"patrol: {e!r}", flush=True)
