from engine import jobs


def test_enqueue_claim_complete(store):
    jid = jobs.enqueue(store, "brand_profile", "brand_x", {"a": 1})
    job = jobs.claim(store)
    assert job["id"] == jid and job["status"] == "running" and job["attempts"] == 1
    assert jobs.claim(store) is None                       # nothing else is due
    jobs.complete(store, jid, {"ok": True})
    assert jobs.get(store, jid)["status"] == "done"


def test_failed_job_retries_then_gives_up(store):
    jid = jobs.enqueue(store, "x", None, {}, max_attempts=2)
    job = jobs.claim(store)
    jobs.fail(store, job, "boom")
    row = jobs.get(store, jid)
    assert row["status"] == "queued" and row["last_error"] == "boom"      # backed off, not yet due
    assert jobs.claim(store) is None
    store.db.execute("UPDATE jobs SET run_after='2000-01-01T00:00:00+00:00' WHERE id=?", (jid,))
    job = jobs.claim(store)
    jobs.fail(store, job, "boom again")
    assert jobs.get(store, jid)["status"] == "failed"


def test_dead_worker_job_is_reclaimed(store):
    jid = jobs.enqueue(store, "x")
    jobs.claim(store)
    store.db.execute("UPDATE jobs SET locked_at='2000-01-01T00:00:00+00:00' WHERE id=?", (jid,))
    assert jobs.claim(store)["id"] == jid                  # worker died mid-job: taken again
