"""SQLAlchemy engine and session construction for the website."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import load_env_file


class Base(DeclarativeBase):
    """Base class for website database models."""


def get_database_url() -> str:
    import os

    load_env_file()
    database_url = os.environ.get("WEBSITE_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "WEBSITE_DATABASE_URL is required; copy .env.example to .env and set it"
        )
    return database_url


def create_database_engine(database_url: str | None = None) -> Engine:
    return create_engine(database_url or get_database_url(), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
