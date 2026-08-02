"""Add the Stage 2 administrator backend schema.

Revision ID: 0002_admin_backend
Revises: 0001_initial
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_admin_backend"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

JSON_EMPTY = sa.text("'[]'::jsonb")
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("role IN ('admin', 'writer')", name="ck_users_role"),
        sa.CheckConstraint(
            "length(username) BETWEEN 1 AND 64 AND username !~ '[[:space:]]'",
            name="ck_users_username",
        ),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "preview_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("preview_date", sa.Date(), nullable=False),
        sa.Column("competition", sa.String(16), nullable=False),
        sa.Column("headline", sa.String(200), nullable=False, server_default=""),
        sa.Column("editors", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("reviewers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("approvers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("cover_kind", sa.String(16), nullable=False),
        sa.Column("cover_storage_key", sa.Text(), nullable=False),
        sa.Column("cover_content_type", sa.String(64)),
        sa.Column("current_article_id", sa.BigInteger()),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "competition IN ('male', 'female', 'futsal')",
            name="ck_preview_batches_competition",
        ),
        sa.CheckConstraint(
            "cover_kind IN ('file', 'media_id')",
            name="ck_preview_batches_cover_kind",
        ),
        sa.CheckConstraint(
            "(cover_kind = 'file' AND cover_content_type IS NOT NULL) OR "
            "(cover_kind = 'media_id' AND cover_content_type IS NULL)",
            name="ck_preview_batches_cover_shape",
        ),
        sa.UniqueConstraint(
            "preview_date", "competition", name="uq_preview_batches_date_competition"
        ),
    )
    op.create_table(
        "editorial_defaults",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("editors", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("reviewers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("approvers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("id = 1", name="ck_editorial_defaults_singleton"),
    )
    op.execute(
        sa.text(
            "INSERT INTO editorial_defaults (id, editors, reviewers, approvers) "
            "VALUES (1, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb)"
        )
    )
    op.create_table(
        "weather",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("adcode", sa.CHAR(6), nullable=False),
        sa.Column("region_name", sa.String(100), nullable=False),
        sa.Column("condition", sa.String(100), nullable=False),
        sa.Column("low_c", sa.SmallInteger(), nullable=False),
        sa.Column("high_c", sa.SmallInteger(), nullable=False),
        sa.Column("wind_direction", sa.String(50), nullable=False),
        sa.Column("wind_level", sa.String(50), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("report_time", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_weather_source"),
        sa.CheckConstraint("low_c <= high_c", name="ck_weather_temperatures"),
    )
    op.create_table(
        "preview_matches",
        sa.Column("game_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("tournament_id", sa.BigInteger(), nullable=False),
        sa.Column("tournament_name", sa.String(200), nullable=False),
        sa.Column("competition_name", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("kickoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(200), nullable=False),
        sa.Column("home_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("away_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("head_to_head_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("task_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_by_user_id", sa.BigInteger()),
        sa.Column("writers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["batch_id"], ["preview_batches.id"]),
        sa.ForeignKeyConstraint(["claimed_by_user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_preview_matches_batch_active_kickoff",
        "preview_matches",
        ["batch_id", "active", "kickoff", "game_id"],
    )
    op.create_index(
        "ix_preview_matches_open_tasks",
        "preview_matches",
        ["kickoff", "game_id"],
        postgresql_where=sa.text("active AND task_open"),
    )
    op.create_index(
        "ix_preview_matches_claimed",
        "preview_matches",
        ["claimed_by_user_id", "kickoff", "game_id"],
        postgresql_where=sa.text("claimed_by_user_id IS NOT NULL"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("author", sa.String(100), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("template_version", sa.String(128), nullable=False),
        sa.Column("content_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("cover_kind", sa.String(16), nullable=False),
        sa.Column("cover_storage_key", sa.Text(), nullable=False),
        sa.Column("cover_sha256", sa.CHAR(64), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("missing_fields", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "cover_kind IN ('file', 'media_id')", name="ck_articles_cover_kind"
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["preview_batches.id"]),
        sa.UniqueConstraint("batch_id", "version_number", name="uq_articles_version"),
    )
    op.create_index(
        "ix_articles_batch_version",
        "articles",
        ["batch_id", sa.text("version_number DESC")],
    )
    op.create_foreign_key(
        "fk_preview_batches_current_article",
        "preview_batches",
        "articles",
        ["current_article_id"],
        ["id"],
    )
    op.create_table(
        "wechat_drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("articles", postgresql.JSONB(), nullable=False),
        sa.Column("publication_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("wechat_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "publication_fingerprint", name="uq_wechat_drafts_publication_fingerprint"
        ),
    )


def downgrade() -> None:
    op.drop_table("wechat_drafts")
    op.drop_constraint(
        "fk_preview_batches_current_article",
        "preview_batches",
        type_="foreignkey",
    )
    op.drop_index("ix_articles_batch_version", table_name="articles")
    op.drop_table("articles")
    op.drop_index("ix_preview_matches_claimed", table_name="preview_matches")
    op.drop_index("ix_preview_matches_open_tasks", table_name="preview_matches")
    op.drop_index(
        "ix_preview_matches_batch_active_kickoff", table_name="preview_matches"
    )
    op.drop_table("preview_matches")
    op.drop_table("weather")
    op.drop_table("editorial_defaults")
    op.drop_table("preview_batches")
    op.drop_table("users")
