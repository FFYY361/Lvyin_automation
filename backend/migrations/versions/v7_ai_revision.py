"""Separate new writing and revision result slots."""

import sqlalchemy as sa
from alembic import op

revision = "v7_ai_revision"
down_revision = "v6_tournament_team_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_preview_results", sa.Column("mode", sa.String(8), nullable=False, server_default="create"))
    op.add_column("ai_preview_results", sa.Column("revision_instruction", sa.Text(), nullable=False, server_default=""))
    op.add_column("ai_preview_results", sa.Column("source_body_version", sa.Integer()))
    op.add_column("ai_preview_results", sa.Column("source_body_hash", sa.CHAR(64)))
    primary_key = sa.inspect(op.get_bind()).get_pk_constraint("ai_preview_results")["name"]
    op.drop_constraint(primary_key, "ai_preview_results", type_="primary")
    op.create_primary_key("pk_ai_preview_results", "ai_preview_results", ["game_id", "model_profile", "mode"])
    op.create_check_constraint("ck_ai_preview_results_mode", "ai_preview_results", "mode IN ('create', 'revise')")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM ai_preview_results WHERE mode='revise')")).scalar():
        raise RuntimeError("Back up and remove revision results before downgrading")
    op.drop_constraint("ck_ai_preview_results_mode", "ai_preview_results", type_="check")
    op.drop_constraint("pk_ai_preview_results", "ai_preview_results", type_="primary")
    op.create_primary_key("pk_ai_preview_results", "ai_preview_results", ["game_id", "model_profile"])
    for name in ("source_body_hash", "source_body_version", "revision_instruction", "mode"):
        op.drop_column("ai_preview_results", name)
