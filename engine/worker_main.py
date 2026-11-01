"""Standalone job worker (brand profiling and any future job kinds). Run as its own Railway service:

  uv run python -m engine.worker_main
"""
import asyncio

from engine import jobs
from engine.brand.profiler import run_profile
from engine.store import Store


def handlers(store: Store, watch=None) -> dict:
    async def brand_profile(job: dict) -> dict:
        def progress(state: dict) -> None:
            store.db.execute("UPDATE jobs SET result=? WHERE id=?", (__import__("json").dumps(state), job["id"]))
        try:
            return await run_profile(store, job["brand_id"], progress)
        except Exception:
            # never leave a brand stuck in 'profiling' if the job itself fails
            row = store.db.execute("SELECT active_version FROM brands WHERE id=?", (job["brand_id"],)).fetchone()
            store.db.execute("UPDATE brands SET status=? WHERE id=?", ("ready" if row and row["active_version"] else "draft", job["brand_id"]))
            raise
    out = {"brand_profile": brand_profile}
    if watch is not None:        # the sweep feeds the live pipeline, so it runs where the Watch queue lives (the API process)
        from engine.discovery import run_discovery

        async def lookalike_sweep(job: dict) -> dict:
            def progress(state: dict) -> None:
                store.db.execute("UPDATE jobs SET result=? WHERE id=?", (__import__("json").dumps(state), job["id"]))
            res = await run_discovery(store, watch, job["brand_id"], progress)
            if res.get("queued"):
                from engine import alerts
                found = ", ".join(f"{v.get('queued')} from {v['label'].lower()}" for v in res["sources"].values() if v.get("queued"))
                await alerts.notify(store, job["brand_id"], "scan_done", f"Scan found {res['queued']} new thing{'s' if res['queued'] != 1 else ''} to analyse", found, "/threats",
                                    "info", ref=f"scan-{job['id']}")
            return res
        out["discovery"] = lookalike_sweep
    return out


async def main() -> None:
    store = Store()
    print("worker: waiting for jobs", flush=True)
    await jobs.run_worker(store, handlers(store))


if __name__ == "__main__":
    asyncio.run(main())
