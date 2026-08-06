"""Unify batch and match storage and add website reports.

Revision ID: 0004_stage6_reports
Revises: 0003_stage4_collaboration
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_stage6_reports"
down_revision = "0003_stage4_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("preview_batches", "batches")
    op.rename_table("preview_matches", "matches")
    op.alter_column("batches", "preview_date", new_column_name="batch_date")
    op.alter_column(
        "batches", "current_article_id", new_column_name="current_preview_article_id"
    )

    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "uq_preview_batches_date_competition TO uq_batches_date_competition"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_preview_batches_competition TO ck_batches_competition"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_preview_batches_cover_kind TO ck_batches_cover_kind"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_preview_batches_cover_shape TO ck_batches_cover_shape"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "fk_preview_batches_current_article TO fk_batches_current_preview_article"
    )
    op.execute(
        "ALTER INDEX ix_preview_matches_batch_active_kickoff "
        "RENAME TO ix_matches_batch_active_kickoff"
    )
    op.execute(
        "ALTER INDEX ix_preview_matches_open_tasks RENAME TO ix_matches_open_tasks"
    )
    op.execute(
        "ALTER INDEX ix_preview_matches_claimed RENAME TO ix_matches_claimed"
    )

    op.add_column(
        "matches",
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="unknown"
        ),
    )
    op.add_column("matches", sa.Column("report_input_sha256", sa.CHAR(64)))
    op.add_column("matches", sa.Column("report_storage_key", sa.Text()))
    op.add_column("matches", sa.Column("report_content_sha256", sa.CHAR(64)))
    op.add_column(
        "matches", sa.Column("report_rendered_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_matches_status",
        "matches",
        "status IN ('scheduled', 'started', 'finished', 'unknown')",
    )
    op.create_check_constraint(
        "ck_matches_report_shape",
        "matches",
        "(report_input_sha256 IS NULL AND report_storage_key IS NULL "
        "AND report_content_sha256 IS NULL AND report_rendered_at IS NULL) OR "
        "(report_input_sha256 IS NOT NULL AND report_storage_key IS NOT NULL "
        "AND report_content_sha256 IS NOT NULL AND report_rendered_at IS NOT NULL)",
    )

    op.add_column(
        "articles",
        sa.Column(
            "article_type", sa.String(16), nullable=False, server_default="preview"
        ),
    )
    op.create_check_constraint(
        "ck_articles_type",
        "articles",
        "article_type IN ('preview', 'report')",
    )
    op.drop_constraint("uq_articles_version", "articles", type_="unique")
    op.drop_index("ix_articles_batch_version", table_name="articles")
    op.create_unique_constraint(
        "uq_articles_version",
        "articles",
        ["batch_id", "article_type", "version_number"],
    )
    op.create_index(
        "ix_articles_batch_type_version",
        "articles",
        ["batch_id", "article_type", sa.text("version_number DESC")],
    )

    op.add_column("batches", sa.Column("current_report_article_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_batches_current_report_article",
        "batches",
        "articles",
        ["current_report_article_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_batches_current_report_article", "batches", type_="foreignkey"
    )
    op.drop_column("batches", "current_report_article_id")

    op.drop_index("ix_articles_batch_type_version", table_name="articles")
    op.drop_constraint("uq_articles_version", "articles", type_="unique")
    op.create_unique_constraint(
        "uq_articles_version", "articles", ["batch_id", "version_number"]
    )
    op.create_index(
        "ix_articles_batch_version",
        "articles",
        ["batch_id", sa.text("version_number DESC")],
    )
    op.drop_constraint("ck_articles_type", "articles", type_="check")
    op.drop_column("articles", "article_type")

    op.drop_constraint("ck_matches_report_shape", "matches", type_="check")
    op.drop_constraint("ck_matches_status", "matches", type_="check")
    op.drop_column("matches", "report_rendered_at")
    op.drop_column("matches", "report_content_sha256")
    op.drop_column("matches", "report_storage_key")
    op.drop_column("matches", "report_input_sha256")
    op.drop_column("matches", "status")

    op.execute(
        "ALTER INDEX ix_matches_claimed RENAME TO ix_preview_matches_claimed"
    )
    op.execute(
        "ALTER INDEX ix_matches_open_tasks RENAME TO ix_preview_matches_open_tasks"
    )
    op.execute(
        "ALTER INDEX ix_matches_batch_active_kickoff "
        "RENAME TO ix_preview_matches_batch_active_kickoff"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "fk_batches_current_preview_article TO fk_preview_batches_current_article"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_batches_cover_shape TO ck_preview_batches_cover_shape"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_batches_cover_kind TO ck_preview_batches_cover_kind"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "ck_batches_competition TO ck_preview_batches_competition"
    )
    op.execute(
        "ALTER TABLE batches RENAME CONSTRAINT "
        "uq_batches_date_competition TO uq_preview_batches_date_competition"
    )
    op.alter_column(
        "batches", "current_preview_article_id", new_column_name="current_article_id"
    )
    op.alter_column("batches", "batch_date", new_column_name="preview_date")
    op.rename_table("matches", "preview_matches")
    op.rename_table("batches", "preview_batches")
