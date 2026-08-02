"""SQLAlchemy engine and session construction for the website."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL_ENV = "WEBSITE_DATABASE_URL"
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Base(DeclarativeBase):
    """Base class for website database models."""


def _load_database_url_from_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    if not path.is_file() or os.environ.get(DATABASE_URL_ENV):
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != DATABASE_URL_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(DATABASE_URL_ENV, value)
        return


def get_database_url() -> str:
    _load_database_url_from_env_file()
    database_url = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not database_url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} is required; copy .env.example to .env and set it"
        )
    return database_url


def create_database_engine(database_url: str | None = None) -> Engine:
    return create_engine(database_url or get_database_url(), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
