"""Store editable prompts and title-generation results.

Revision ID: v3_prompt_and_title_ai
Revises: v2_football_data
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v3_prompt_and_title_ai"
down_revision = "v2_football_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_templates",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "ai_title_results",
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("model_profile", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.CHAR(64), nullable=False),
        sa.Column("model_config_hash", sa.CHAR(64), nullable=False),
        sa.Column("request_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("candidates", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("requested_by_user_id", sa.BigInteger()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_ai_title_results_status"),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("batch_id", "model_profile"),
    )
    # Prompt content is imported from an existing database, not seeded from Git.


def downgrade() -> None:
    op.drop_table("ai_title_results")
    op.drop_table("prompt_templates")
