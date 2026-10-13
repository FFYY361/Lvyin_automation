"""Introduce a super administrator and promote the existing sole administrator."""

import sqlalchemy as sa
from alembic import op

revision = "v5_super_admin"
down_revision = "v4_institution_predecessors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Hold the user set stable while checking and promoting the initial account.
    connection.execute(sa.text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    user_count = connection.scalar(sa.text("SELECT count(*) FROM users"))
    admin_count = connection.scalar(
        sa.text("SELECT count(*) FROM users WHERE role = 'admin'")
    )
    if user_count and admin_count != 1:
        raise RuntimeError("升级超级管理员需要恰好一个现有 admin；请先核对用户角色")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('user', 'admin', 'super_admin')"
    )
    connection.execute(
        sa.text(
            "UPDATE users SET role = 'super_admin', auth_version = auth_version + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE role = 'admin'"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE users SET role = 'admin', auth_version = auth_version + 1, "
            "updated_at = CURRENT_TIMESTAMP WHERE role = 'super_admin'"
        )
    )
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", "role IN ('user', 'admin')")
