"""Brand identity: brands, versioned profiles, sourced facts, assets, people, durable jobs, signals, LLM audit trail.

Every fact keeps its provenance (source_url, collector, content_hash, fetched_at) and a verification level.
Socials, domains, contacts, press and similar groups are rows in brand_facts (kind + jsonb data) rather than one table each.
"""
from alembic import op

revision = "0002_brand_identity"
down_revision = "0001_core"
branch_labels = None
depends_on = None

SQL = """
CREATE TABLE brands (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  website TEXT,
  linkedin_url TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  ownership_status TEXT NOT NULL DEFAULT 'unverified',
  verify_token TEXT,
  verify_method TEXT,
  verified_at TEXT,
  fictional BOOLEAN NOT NULL DEFAULT FALSE,
  authorisation_letter_ref TEXT,
  authorisation_letter_sha256 TEXT,
  seed_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
  inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
  active_version INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE brand_profile_versions (
  id BIGSERIAL PRIMARY KEY,
  brand_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (brand_id, version)
);

CREATE TABLE brand_facts (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  label TEXT,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_url TEXT,
  collector TEXT NOT NULL,
  content_hash TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  verification TEXT NOT NULL DEFAULT 'single_source',
  review_status TEXT NOT NULL DEFAULT 'pending',
  needs_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
  fetched_at TEXT NOT NULL,
  profile_version INTEGER,
  UNIQUE (brand_id, kind, value)
);
CREATE INDEX brand_facts_brand_kind ON brand_facts (brand_id, kind);

CREATE TABLE brand_assets (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  url TEXT,
  sha256 TEXT,
  phash TEXT,
  mmh3 BIGINT,
  meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_url TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (brand_id, kind, url)
);

CREATE TABLE brand_people (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  name TEXT NOT NULL,
  title TEXT,
  profile_url TEXT,
  photo_url TEXT,
  bio TEXT,
  source_url TEXT,
  collector TEXT NOT NULL,
  verification TEXT NOT NULL DEFAULT 'single_source',
  review_status TEXT NOT NULL DEFAULT 'pending',
  fetched_at TEXT NOT NULL,
  UNIQUE (brand_id, name, title)
);

CREATE TABLE jobs (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  brand_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  run_after TEXT NOT NULL,
  locked_at TEXT,
  last_error TEXT,
  result JSONB,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX jobs_claim ON jobs (status, run_after, id);

CREATE TABLE signals (
  id BIGSERIAL PRIMARY KEY,
  brand_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  source TEXT NOT NULL,
  ref TEXT,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TEXT NOT NULL
);
CREATE INDEX signals_brand ON signals (brand_id, created_at DESC);

CREATE TABLE llm_calls (
  id BIGSERIAL PRIMARY KEY,
  brand_id TEXT,
  purpose TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  ok BOOLEAN NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE source_toggles (
  collector TEXT PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  note TEXT
);
"""


def upgrade() -> None:
    for stmt in [s.strip() for s in SQL.split(";\n") if s.strip()]:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("source_toggles", "llm_calls", "signals", "jobs", "brand_people", "brand_assets", "brand_facts", "brand_profile_versions", "brands"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
