"""Core takedown tables, ported from the SQLite schema.

Timestamps stay ISO-8601 TEXT (UTC) on purpose: the API and frontend already speak that format and compare it as a string.
JSON columns are jsonb.
"""
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

SQL = """
CREATE TABLE threats (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  hostname TEXT NOT NULL,
  url TEXT,
  source TEXT NOT NULL,
  source_ref TEXT,
  detected_at TEXT NOT NULL,
  stage_a JSONB,
  status TEXT NOT NULL,
  capture JSONB,
  verdict JSONB,
  enrichment JSONB,
  confidence TEXT,
  score INTEGER,
  case_id TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE (brand_id, hostname)
);
CREATE INDEX threats_brand_detected ON threats (brand_id, detected_at DESC);
CREATE INDEX threats_status ON threats (status);

CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT
);

CREATE TABLE identifiers (
  threat_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (threat_id, kind, value)
);
CREATE INDEX identifiers_value ON identifiers (kind, value);

CREATE TABLE notices (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  threat_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  channel_name TEXT,
  method TEXT,
  recipient TEXT,
  recipient_verified INTEGER,
  subject TEXT,
  body TEXT,
  evidence_refs JSONB,
  status TEXT NOT NULL,
  drafted_at TEXT NOT NULL,
  approved_by TEXT,
  approved_at TEXT,
  filed_at TEXT,
  UNIQUE (threat_id, channel)
);

CREATE TABLE clock_events (
  id BIGSERIAL PRIMARY KEY,
  case_id TEXT NOT NULL,
  notice_id TEXT,
  channel TEXT,
  event TEXT NOT NULL,
  ts TEXT NOT NULL,
  simulated INTEGER NOT NULL,
  note TEXT
);

CREATE TABLE audit_log (
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  ref TEXT,
  detail JSONB
);
CREATE INDEX audit_ref ON audit_log (ref);
"""


def upgrade() -> None:
    for stmt in [s.strip() for s in SQL.split(";\n") if s.strip()]:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("audit_log", "clock_events", "notices", "identifiers", "cases", "threats"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
