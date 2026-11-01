"""Test database: a separate Postgres (TEST_DATABASE_URL), migrated once, truncated between tests."""
import os

import pytest

# Point the whole process at the TEST database before anything reads settings, so tests can never touch a dev or prod database.
os.environ.setdefault("TEST_DATABASE_URL", "postgresql://haul16:haul16@127.0.0.1:55433/haul16_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["HAUL16_CT"] = "0"
os.environ["EMBEDDED_WORKER"] = "false"
for _k in ("OPENAI_API_KEY", "FIRECRAWL_API_KEY", "APIFY_TOKEN", "SEARCH_API_KEY", "URLSCAN_API_KEY"):
    os.environ[_k] = ""          # tests never call real vendors; collectors without a key skip

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
