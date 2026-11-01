"""Test database: a separate Postgres (TEST_DATABASE_URL), migrated once, truncated between tests."""
import os

import pytest

from engine.db import Database, can_connect, migrate
from engine.settings import get_settings
from engine.store import Store

TABLES = ("threats", "cases", "identifiers", "notices", "clock_events", "audit_log", "brands", "brand_profile_versions",
          "brand_facts", "brand_assets", "brand_people", "jobs", "signals", "llm_calls", "source_toggles")


def _test_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().test_database_url or "postgresql://haul16:haul16@127.0.0.1:55433/haul16_test"


@pytest.fixture(scope="session")
def db_url():
    url = _test_url()
    base = url.rsplit("/", 1)[0] + "/postgres"
    name = url.rsplit("/", 1)[1].split("?")[0]
    if not can_connect(url):
        import psycopg
        try:
            with psycopg.connect(base, autocommit=True, connect_timeout=5) as c:
                c.execute(f'CREATE DATABASE "{name}"')
        except Exception as e:  # pragma: no cover
            pytest.skip(f"no Postgres for tests ({e!r}); set TEST_DATABASE_URL")
    migrate(url)
    return url


@pytest.fixture()
def store(db_url):
    s = Store(db_url, migrate_schema=False)
    s.db.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    yield s
    s.db.close()
