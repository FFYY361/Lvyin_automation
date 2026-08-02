"""SQLAlchemy models for the website backend."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'writer')", name="ck_users_role"),
        CheckConstraint(
            "length(username) BETWEEN 1 AND 64 "
            "AND username !~ '[[:space:]]'",
            name="ck_users_username",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PreviewBatch(Base):
    __tablename__ = "preview_batches"
    __table_args__ = (
        UniqueConstraint(
            "preview_date", "competition", name="uq_preview_batches_date_competition"
        ),
        CheckConstraint(
            "competition IN ('male', 'female', 'futsal')",
            name="ck_preview_batches_competition",
        ),
        CheckConstraint(
            "cover_kind IN ('file', 'media_id')",
            name="ck_preview_batches_cover_kind",
        ),
        CheckConstraint(
            "(cover_kind = 'file' AND cover_content_type IS NOT NULL) OR "
            "(cover_kind = 'media_id' AND cover_content_type IS NULL)",
            name="ck_preview_batches_cover_shape",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    preview_date: Mapped[date] = mapped_column(Date, nullable=False)
    competition: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    editors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    reviewers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    approvers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    cover_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    cover_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    cover_content_type: Mapped[str | None] = mapped_column(String(64))
    current_article_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "articles.id",
            name="fk_preview_batches_current_article",
            use_alter=True,
        ),
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PreviewMatch(Base):
    __tablename__ = "preview_matches"
    __table_args__ = (
        Index(
            "ix_preview_matches_batch_active_kickoff",
            "batch_id",
            "active",
            "kickoff",
            "game_id",
        ),
        Index(
            "ix_preview_matches_open_tasks",
            "kickoff",
            "game_id",
            postgresql_where=text("active AND task_open"),
        ),
        Index(
            "ix_preview_matches_claimed",
            "claimed_by_user_id",
            "kickoff",
            "game_id",
            postgresql_where=text("claimed_by_user_id IS NOT NULL"),
        ),
    )

    game_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    batch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("preview_batches.id"), nullable=False
    )
    tournament_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tournament_name: Mapped[str] = mapped_column(String(200), nullable=False)
    competition_name: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str] = mapped_column(String(200), nullable=False)
    home_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    away_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    head_to_head_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    task_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id")
    )
    writers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Weather(Base):
    __tablename__ = "weather"
    __table_args__ = (
        CheckConstraint("source IN ('auto', 'manual')", name="ck_weather_source"),
        CheckConstraint("low_c <= high_c", name="ck_weather_temperatures"),
    )

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    adcode: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    region_name: Mapped[str] = mapped_column(String(100), nullable=False)
    condition: Mapped[str] = mapped_column(String(100), nullable=False)
    low_c: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    high_c: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    wind_direction: Mapped[str] = mapped_column(String(50), nullable=False)
    wind_level: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    report_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EditorialDefaults(Base):
    __tablename__ = "editorial_defaults"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_editorial_defaults_singleton"),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, default=1
    )
    editors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    reviewers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    approvers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArticleRecord(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("batch_id", "version_number", name="uq_articles_version"),
        CheckConstraint(
            "cover_kind IN ('file', 'media_id')", name="ck_articles_cover_kind"
        ),
        Index("ix_articles_batch_version", "batch_id", text("version_number DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("preview_batches.id"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    template_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    cover_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    cover_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    cover_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WechatDraft(Base):
    __tablename__ = "wechat_drafts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    articles: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    publication_fingerprint: Mapped[str] = mapped_column(
        CHAR(64), nullable=False, unique=True
    )
    media_id: Mapped[str] = mapped_column(Text, nullable=False)
    wechat_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
