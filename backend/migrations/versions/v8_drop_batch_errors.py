"""Remove persisted batch refresh errors."""

import sqlalchemy as sa
from alembic import op

revision = "v8_drop_batch_errors"
down_revision = "v7_ai_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("last_error_code", "last_error_message", "last_error_at"):
        op.drop_column("batches", column)


def downgrade() -> None:
    op.add_column("batches", sa.Column("last_error_code", sa.String(64)))
    op.add_column("batches", sa.Column("last_error_message", sa.Text()))
    op.add_column("batches", sa.Column("last_error_at", sa.DateTime(timezone=True)))
