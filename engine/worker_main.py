"""Standalone job worker (brand profiling and any future job kinds). Run as its own Railway service:

  uv run python -m engine.worker_main
"""
import asyncio

from engine import jobs
from engine.brand.profiler import run_profile
from engine.store import Store


def handlers(store: Store) -> dict:
    async def brand_profile(job: dict) -> dict:
        def progress(state: dict) -> None:
            store.db.execute("UPDATE jobs SET result=? WHERE id=?", (__import__("json").dumps(state), job["id"]))
        return await run_profile(store, job["brand_id"], progress)
    return {"brand_profile": brand_profile}


async def main() -> None:
    store = Store()
    print("worker: waiting for jobs", flush=True)
    await jobs.run_worker(store, handlers(store))


if __name__ == "__main__":
    asyncio.run(main())
