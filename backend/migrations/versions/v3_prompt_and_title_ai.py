"""Store editable prompts and title-generation results.

Revision ID: v3_prompt_and_title_ai
Revises: v2_football_data
"""

import pathlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "v3_prompt_and_title_ai"
down_revision = "v2_football_data"
branch_labels = None
depends_on = None


def _prompt(path: str) -> str:
    root = pathlib.Path(__file__).resolve().parents[3]
    return (root / path).read_text(encoding="utf-8").strip()


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
    rows = [
        ("preview_system", "src/ai_preview/prompt/system.md"),
        ("preview_writing_rules", "src/ai_preview/prompt/writing_rules.md"),
        ("preview_data_rules", "src/ai_preview/prompt/data_rules.md"),
        ("competition_male", "src/ai_preview/prompt/competition_rules/men.md"),
        ("competition_female", "src/ai_preview/prompt/competition_rules/women.md"),
        ("competition_futsal", "src/ai_preview/prompt/competition_rules/futsal.md"),
    ]
    prompt_rows = [{"key": key, "content": _prompt(path)} for key, path in rows]
    prompt_rows += [
        {"key": "title_system", "content": "你是清华大学学生马约翰杯足球赛事的标题编辑。请根据给定的整篇前瞻内容，生成适合微信公众号发布的标题候选。所有判断只能来自输入资料，不得补写比赛事实。"},
        {"key": "title_rules", "content": "每条标题必须由两个四字词语组成，中间使用中文逗号“，”。完整标题由系统提供的固定前缀、分隔符“||”和两个四字词语组成。\n\n规则：\n1. 两个词语都必须恰好四个汉字，具有成语或稳定四字熟语的完整表达。\n2. 内容必须贴合整篇前瞻的比赛状态、阶段、气质或悬念。\n3. 整体应有文采、昂扬感和新意。\n4. 不得出现球队名称、院系名称、球员姓名、教练姓名或其他个人姓名。\n5. 不得出现比分、排名、已经发生的赛果或确定性的胜负判断。\n6. 不得出现贬低、羞辱、歧视、暴力化或明显负面标签。\n7. 六条候选不得重复；至少两条使用相对少见但含义准确的四字成语。\n8. 不得为了使用生僻成语而牺牲语义准确性。\n9. 只输出 JSON，不输出解释、Markdown、序号或其他文字。\n\n输出格式：\n{\"candidates\":[{\"idiom_1\":\"四字词语\",\"idiom_2\":\"四字词语\"}]}"},
    ]
    op.bulk_insert(sa.table("prompt_templates", sa.column("key", sa.String), sa.column("content", sa.Text())), prompt_rows)


def downgrade() -> None:
    op.drop_table("ai_title_results")
    op.drop_table("prompt_templates")
