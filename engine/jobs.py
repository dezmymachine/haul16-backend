"""Durable job queue in Postgres (SELECT ... FOR UPDATE SKIP LOCKED). Survives restarts; many workers can run at once."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from engine.store import Store


def _now(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat(timespec="seconds")


def enqueue(store: Store, kind: str, brand_id: str | None = None, payload: dict | None = None, max_attempts: int = 3, run_in_s: float = 0) -> int:
    row = store.db.execute(
        "INSERT INTO jobs (kind, brand_id, payload, max_attempts, run_after, created_at, updated_at) VALUES (?,?,?,?,?,?,?) RETURNING id",
        (kind, brand_id, json.dumps(payload or {}), max_attempts, _now(run_in_s), _now(), _now())).fetchone()
    return row["id"]


def claim(store: Store) -> dict | None:
    """Atomically take the next due job. A job stuck 'running' for over 15 minutes (worker died) is taken again."""
    now = _now()
    row = store.db.execute(
        """UPDATE jobs SET status='running', locked_at=?, attempts=attempts+1, updated_at=?
           WHERE id = (SELECT id FROM jobs
                       WHERE (status='queued' AND run_after <= ?) OR (status='running' AND locked_at <= ?)
                       ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING *""", (now, now, now, _now(-900))).fetchone()
    return dict(row) if row else None


def complete(store: Store, job_id: int, result: dict | None = None) -> None:
    store.db.execute("UPDATE jobs SET status='done', result=?, locked_at=NULL, updated_at=? WHERE id=?",
                     (json.dumps(result) if result is not None else None, _now(), job_id))


def fail(store: Store, job: dict, error: str) -> None:
    if job["attempts"] >= job["max_attempts"]:
        store.db.execute("UPDATE jobs SET status='failed', last_error=?, locked_at=NULL, updated_at=? WHERE id=?", (error[:500], _now(), job["id"]))
    else:  # retry with backoff
        store.db.execute("UPDATE jobs SET status='queued', last_error=?, locked_at=NULL, run_after=?, updated_at=? WHERE id=?",
                         (error[:500], _now(30 * job["attempts"]), _now(), job["id"]))


def get(store: Store, job_id: int) -> dict | None:
    r = store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def latest(store: Store, kind: str, brand_id: str) -> dict | None:
    r = store.db.execute("SELECT * FROM jobs WHERE kind=? AND brand_id=? ORDER BY id DESC LIMIT 1", (kind, brand_id)).fetchone()
    return dict(r) if r else None


Handler = Callable[[dict], Awaitable[dict | None]]


async def run_worker(store: Store, handlers: dict[str, Handler], poll_s: float = 2.0, stop: asyncio.Event | None = None) -> None:
    """Claim and run jobs until `stop` is set. One failing job never stops the loop."""
    while not (stop and stop.is_set()):
        job = await asyncio.to_thread(claim, store)
        if not job:
            await asyncio.sleep(poll_s)
            continue
        h = handlers.get(job["kind"])
        try:
            if not h:
                raise RuntimeError(f"no handler for job kind '{job['kind']}'")
            result = await h(job)
            await asyncio.to_thread(complete, store, job["id"], result)
        except Exception as e:  # noqa: BLE001
            await asyncio.to_thread(fail, store, job, repr(e))
