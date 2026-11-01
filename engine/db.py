"""Postgres access with the small slice of the sqlite3 interface the store uses.

`Database.execute(sql, params)` accepts `?` placeholders, runs the statement in autocommit mode on a pooled
connection, and returns a `Result` whose rows are dicts that also support integer indexing (`row[0]`).
"""
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parents[1]


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Result:
    def __init__(self, rows: list[Row], rowcount: int):
        self.rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def _q(sql: str) -> str:
    return sql.replace("%", "%%").replace("?", "%s")


class Database:
    def __init__(self, url: str, min_size: int = 1, max_size: int = 8):
        self.url = url
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, open=True,
            kwargs={"autocommit": True, "row_factory": _row_factory},
            check=ConnectionPool.check_connection, max_idle=240,
        )

    def execute(self, sql: str, params=()) -> Result:
        with self.pool.connection() as conn:
            cur = conn.execute(_q(sql), tuple(params))
            rows = cur.fetchall() if cur.description else []
            return Result(rows, cur.rowcount)

    def executemany(self, sql: str, seq) -> None:
        seq = list(seq)
        if not seq:
            return
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(_q(sql), seq)

    def commit(self) -> None:  # autocommit: kept so existing call sites read the same
        pass

    def close(self) -> None:
        self.pool.close()


def sqlalchemy_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def migrate(url: str, revision: str = "head") -> None:
    """Apply Alembic migrations to the database at `url`."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    command.upgrade(cfg, revision)


def can_connect(url: str) -> bool:
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
