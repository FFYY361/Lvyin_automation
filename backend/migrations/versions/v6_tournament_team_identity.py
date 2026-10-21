"""Use tournament registrations for all business team identities."""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from backend.tournament_identity_migration import apply_manifest, read_source

revision = "v6_tournament_team_identity"
down_revision = "v5_super_admin"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE institutions, tournaments, games, matches, articles, "
            "batches, prompt_templates, ai_title_results "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    source = read_source(connection)
    populated = any(source.values())
    manifest_path = context.get_x_argument(as_dictionary=True).get(
        "team_identity_manifest"
    )
    if populated and not manifest_path:
        raise ValueError(
            "Prepare the tournament identity manifest first; pass -x team_identity_manifest=PATH"
        )
    op.add_column(
        "institutions",
        sa.Column(
            "tournament_teams",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text('\'{"male":{},"female":{},"futsal":{}}\'::jsonb'),
        ),
    )
    if populated:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        apply_manifest(connection, manifest)
    for field in ("male_team_ids", "female_team_ids", "futsal_team_ids"):
        op.drop_column("institutions", field)


def downgrade():
    raise RuntimeError(
        "Restore the pre-migration database backup with the matching application version"
    )
