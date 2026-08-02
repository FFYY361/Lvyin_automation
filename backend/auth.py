"""Password and current-admin helpers."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import create_database_engine, create_session_factory
from .models import User

PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def get_session(request: Request):
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def require_admin(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrator role required")
    return user


def create_admin(
    database_url: str,
    *,
    username: str,
    display_name: str,
    password: str,
) -> int:
    from .schemas import validate_username

    validate_username(username)
    if not display_name.strip():
        raise ValueError("display_name must not be empty")
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            existing = session.scalar(select(User).where(User.username == username))
            if existing is not None:
                raise ValueError("username already exists")
            user = User(
                username=username,
                display_name=display_name.strip(),
                password_hash=hash_password(password),
                role="admin",
            )
            session.add(user)
            session.flush()
            return user.id
    finally:
        engine.dispose()
