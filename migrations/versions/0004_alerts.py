"""In-app alerts. One row per (brand, kind, ref), so the same finding never alerts twice."""
from alembic import op

revision = "0004_alerts"
down_revision = "0003_brand_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE alerts (
      id BIGSERIAL PRIMARY KEY,
      brand_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      severity TEXT NOT NULL DEFAULT 'info',
      title TEXT NOT NULL,
      detail TEXT,
      href TEXT,
      ref TEXT NOT NULL,
      created_at TEXT NOT NULL,
      read_at TEXT,
      delivered JSONB NOT NULL DEFAULT '{}'::jsonb,
      UNIQUE (brand_id, kind, ref)
    )""")
    op.execute("CREATE INDEX alerts_brand_created ON alerts (brand_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alerts")
