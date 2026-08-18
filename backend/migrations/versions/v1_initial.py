"""Create the v1 initial website schema.

Revision ID: v1_initial
Revises:
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v1_initial"
down_revision = None
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
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        sa.CheckConstraint("auth_version >= 0", name="ck_users_auth_version"),
        sa.CheckConstraint(
            "length(username) BETWEEN 1 AND 64 AND username !~ '[[:space:]]'",
            name="ck_users_username",
        ),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("batch_date", sa.Date(), nullable=False),
        sa.Column("competition", sa.String(16), nullable=False),
        sa.Column("headline", sa.String(200), nullable=False, server_default=""),
        sa.Column("editors", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("reviewers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("approvers", postgresql.JSONB(), nullable=False, server_default=JSON_EMPTY),
        sa.Column("cover_kind", sa.String(16), nullable=False),
        sa.Column("cover_storage_key", sa.Text(), nullable=False),
        sa.Column("cover_content_type", sa.String(64)),
        sa.Column("current_preview_article_id", sa.BigInteger()),
        sa.Column("current_report_article_id", sa.BigInteger()),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "competition IN ('male', 'female', 'futsal')",
            name="ck_batches_competition",
        ),
        sa.CheckConstraint(
            "cover_kind IN ('file', 'media_id')",
            name="ck_batches_cover_kind",
        ),
        sa.CheckConstraint(
            "(cover_kind = 'file' AND cover_content_type IS NOT NULL) OR "
            "(cover_kind = 'media_id' AND cover_content_type IS NULL)",
            name="ck_batches_cover_shape",
        ),
        sa.UniqueConstraint(
            "batch_date", "competition", name="uq_batches_date_competition"
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
        "INSERT INTO editorial_defaults (id, editors, reviewers, approvers) "
        "VALUES (1, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb)"
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
        "matches",
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
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("report_input_sha256", sa.CHAR(64)),
        sa.Column("report_storage_key", sa.Text()),
        sa.Column("report_content_sha256", sa.CHAR(64)),
        sa.Column("report_rendered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "status IN ('scheduled', 'started', 'finished', 'unknown')",
            name="ck_matches_status",
        ),
        sa.CheckConstraint(
            "(report_input_sha256 IS NULL AND report_storage_key IS NULL "
            "AND report_content_sha256 IS NULL AND report_rendered_at IS NULL) OR "
            "(report_input_sha256 IS NOT NULL AND report_storage_key IS NOT NULL "
            "AND report_content_sha256 IS NOT NULL AND report_rendered_at IS NOT NULL)",
            name="ck_matches_report_shape",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"]),
        sa.ForeignKeyConstraint(["claimed_by_user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_matches_batch_active_kickoff",
        "matches",
        ["batch_id", "active", "kickoff", "game_id"],
    )
    op.create_index(
        "ix_matches_open_tasks",
        "matches",
        ["kickoff", "game_id"],
        postgresql_where=sa.text("active AND task_open"),
    )
    op.create_index(
        "ix_matches_claimed",
        "matches",
        ["claimed_by_user_id", "kickoff", "game_id"],
        postgresql_where=sa.text("claimed_by_user_id IS NOT NULL"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("article_type", sa.String(16), nullable=False, server_default="preview"),
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
            "article_type IN ('preview', 'report')", name="ck_articles_type"
        ),
        sa.CheckConstraint(
            "cover_kind IN ('file', 'media_id')", name="ck_articles_cover_kind"
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"]),
        sa.UniqueConstraint(
            "batch_id", "article_type", "version_number", name="uq_articles_version"
        ),
    )
    op.create_index(
        "ix_articles_batch_type_version",
        "articles",
        ["batch_id", "article_type", sa.text("version_number DESC")],
    )
    op.create_foreign_key(
        "fk_batches_current_preview_article",
        "batches",
        "articles",
        ["current_preview_article_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_batches_current_report_article",
        "batches",
        "articles",
        ["current_report_article_id"],
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
        "fk_batches_current_report_article", "batches", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_batches_current_preview_article", "batches", type_="foreignkey"
    )
    op.drop_index("ix_articles_batch_type_version", table_name="articles")
    op.drop_table("articles")
    op.drop_index("ix_matches_claimed", table_name="matches")
    op.drop_index("ix_matches_open_tasks", table_name="matches")
    op.drop_index("ix_matches_batch_active_kickoff", table_name="matches")
    op.drop_table("matches")
    op.drop_table("weather")
    op.drop_table("editorial_defaults")
    op.drop_table("batches")
    op.drop_table("users")
