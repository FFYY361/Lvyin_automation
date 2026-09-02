"""Create canonical football reference-data tables.

Revision ID: v2_football_data
Revises: v1_initial
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v2_football_data"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column(
            "male_team_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "female_team_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "futsal_team_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("male_description", sa.Text(), nullable=False),
        sa.Column("female_description", sa.Text(), nullable=False),
        sa.Column("futsal_description", sa.Text(), nullable=False),
        sa.Column("player_descriptions", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "tournaments",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("competition", sa.String(16), nullable=False),
        sa.Column("final_rankings", postgresql.JSONB(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "is_finalized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint(
            "competition IN ('male', 'female', 'futsal')",
            name="ck_tournaments_competition",
        ),
        sa.UniqueConstraint("name", name="uq_tournaments_name"),
    )
    op.create_table(
        "games",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=False
        ),
        sa.Column("tournament_id", sa.BigInteger(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tournament_id"],
            ["tournaments.id"],
            name="fk_games_tournament_id_tournaments",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_games_tournament_id", "games", ["tournament_id"])
    op.create_table(
        "ai_preview_results",
        sa.Column("game_id", sa.BigInteger(), nullable=False),
        sa.Column("model_profile", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.CHAR(64), nullable=False),
        sa.Column("model_config_hash", sa.CHAR(64), nullable=False),
        sa.Column(
            "request_token", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("content", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("requested_by_user_id", sa.BigInteger()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ai_preview_results_status",
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["matches.game_id"],
            name="fk_ai_preview_results_game_id_matches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_ai_preview_results_requested_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "game_id", "model_profile", name="pk_ai_preview_results"
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_preview_results")
    op.drop_index("ix_games_tournament_id", table_name="games")
    op.drop_table("games")
    op.drop_table("tournaments")
    op.drop_table("institutions")
