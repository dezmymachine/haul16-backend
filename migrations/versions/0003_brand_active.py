"""Which brand the live detection pipeline (CT matcher, capture workers) is watching for."""
from alembic import op

revision = "0003_brand_active"
down_revision = "0002_brand_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE brands ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE brands DROP COLUMN is_active")
