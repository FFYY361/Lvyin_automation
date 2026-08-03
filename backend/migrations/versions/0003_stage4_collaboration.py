"""Add Stage 4 collaboration authentication fields.

Revision ID: 0003_stage4_collaboration
Revises: 0002_admin_backend
Create Date: 2026-08-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_stage4_collaboration"
down_revision = "0002_admin_backend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'user')",
    )
    op.create_check_constraint(
        "ck_users_auth_version",
        "users",
        "auth_version >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_auth_version", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'writer')",
    )
    op.drop_column("users", "auth_version")
