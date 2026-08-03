"""FastAPI application for the website workflow."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from thufootball import (
    AuthenticationError,
    ConfigurationError,
    THUFootballClient,
    THUFootballError,
    THUFootballQueryService,
)

from .auth import (
    get_session,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)
from .config import DEFAULT_ENV_FILE, PROJECT_ROOT, WebsiteSettings
from .credentials import activate_credentials, credential_status, persist_credentials
from .database import create_database_engine, create_session_factory
from .models import (
    ArticleRecord,
    EditorialDefaults,
    PreviewBatch,
    PreviewMatch,
    User,
    WechatDraft,
)
from .schemas import (
    AssignMatchRequest,
    BatchStatus,
    ChangePasswordRequest,
    CompetitionValue,
    CoverMediaIdRequest,
    CreateBatchesRequest,
    CreateWechatDraftRequest,
    EditorialRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    THUFootballCredentialsRequest,
    UpdateAdminUserRequest,
    UpdateBatchRequest,
    UpdateBodyRequest,
    UpdateMatchRequest,
    UpdateSelfRequest,
    WeatherRequest,
)
from .workflow import (
    ExternalFactories,
    WorkflowError,
    article_payload,
    batch_payload,
    create_batches,
    create_wechat_draft,
    draft_payload,
    match_payload,
    refresh_batch,
    render_batch,
    save_batch_cover,
    set_batch_cover_media_id,
    set_manual_weather,
    weather_payload,
)


def _not_found(name: str) -> HTTPException:
    return HTTPException(404, f"{name} not found")


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
    }


def _user_summary_payload(user: User) -> dict[str, Any]:
    return {"id": user.id, "display_name": user.display_name}


def _admin_user_payload(user: User, claimed_task_count: int) -> dict[str, Any]:
    return {
        **_user_payload(user),
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "claimed_task_count": claimed_task_count,
    }


def _set_login_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["auth_version"] = user.auth_version


def _task_rows(session: Session, *conditions: Any) -> list[tuple[PreviewMatch, PreviewBatch]]:
    return list(
        session.execute(
            select(PreviewMatch, PreviewBatch)
            .join(PreviewBatch, PreviewBatch.id == PreviewMatch.batch_id)
            .where(*conditions)
            .order_by(PreviewMatch.kickoff, PreviewMatch.game_id)
        )
    )


def _task_payload(match: PreviewMatch, batch: PreviewBatch) -> dict[str, Any]:
    return {**match_payload(match), "competition": batch.competition}


def _match_content_payload(match: PreviewMatch) -> dict[str, Any]:
    return {
        "game_id": match.game_id,
        "writers": match.writers,
        "body": match.body,
        "body_version": match.body_version,
    }


def _body_version_conflict(match: PreviewMatch | None) -> WorkflowError:
    return WorkflowError(
        409,
        "body_version_conflict",
        "preview match was updated",
        details={
            "body_version": match.body_version if match else None,
            "writers": match.writers if match else [],
            "body": match.body if match else "",
        },
    )


def _invalidate_match_batch(session: Session, match: PreviewMatch) -> None:
    batch = session.get(PreviewBatch, match.batch_id)
    if batch is not None:
        batch.current_article_id = None
        batch.updated_at = datetime.now(UTC)


def _save_match_content(
    session: Session,
    match: PreviewMatch,
    *,
    expected_version: int,
    writers: list[str],
    body: str,
) -> dict[str, Any]:
    if match.body_version != expected_version:
        raise _body_version_conflict(match)
    normalized_body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if writers == match.writers and normalized_body == match.body:
        return _match_content_payload(match)
    result = session.execute(
        update(PreviewMatch)
        .where(
            PreviewMatch.game_id == match.game_id,
            PreviewMatch.body_version == expected_version,
        )
        .values(
            writers=writers,
            body=normalized_body,
            body_version=PreviewMatch.body_version + 1,
            updated_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise _body_version_conflict(session.get(PreviewMatch, match.game_id))
    _invalidate_match_batch(session, match)
    session.commit()
    session.refresh(match)
    return _match_content_payload(match)


def create_app(
    *,
    settings: WebsiteSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    external_factories: ExternalFactories | None = None,
    credential_env_path: str | Path = DEFAULT_ENV_FILE,
    frontend_dist: str | Path | None = None,
) -> FastAPI:
    resolved_settings = settings or WebsiteSettings.from_environment()
    engine = None
    if session_factory is None:
        engine = create_database_engine(resolved_settings.database_url)
        session_factory = create_session_factory(engine)
    @asynccontextmanager
    async def process_environment_queries():
        async with THUFootballClient(
            openid=os.environ.get("THUFOOTBALL_OPENID") or None,
            session_key=os.environ.get("THUFOOTBALL_SESSION_KEY") or None,
            load_environment=False,
        ) as client:
            async with THUFootballQueryService(client) as service:
                yield service

    base_factories = external_factories or ExternalFactories(
        queries=process_environment_queries
    )
    thufootball_lock = asyncio.Lock()

    @asynccontextmanager
    async def locked_queries():
        async with thufootball_lock:
            async with base_factories.queries() as service:
                yield service

    resolved_factories = replace(base_factories, queries=locked_queries)
    resolved_credential_env_path = Path(credential_env_path)
    resolved_settings.artifact_root.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="清华绿茵前瞻管理 API",
        version="0.3.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.session_factory = session_factory
    app.state.external_factories = resolved_factories
    app.state.thufootball_lock = thufootball_lock
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved_settings.cookie_secret,
        session_cookie=resolved_settings.cookie_name,
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=resolved_settings.cookie_secure,
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(_: Request, exc: WorkflowError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    **({"details": exc.details} if exc.details is not None else {}),
                }
            },
        )

    @app.post("/api/auth/login")
    def login(
        payload: LoginRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.scalar(select(User).where(User.username == payload.username))
        if (
            user is None
            or not user.is_active
            or not verify_password(user.password_hash, payload.password)
        ):
            raise HTTPException(401, "invalid username or password")
        _set_login_session(request, user)
        return _user_payload(user)

    @app.post("/api/auth/register", status_code=201)
    def register(
        payload: RegisterRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = User(
            username=payload.username,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
            role="user",
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise WorkflowError(
                409,
                "username_exists",
                "username already exists",
            ) from exc
        session.refresh(user)
        _set_login_session(request, user)
        return _user_payload(user)

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        request.session.clear()
        return Response(status_code=204)

    @app.get("/api/auth/me")
    def me(user: User = Depends(require_user)) -> dict[str, Any]:
        return _user_payload(user)

    @app.patch("/api/auth/me")
    def update_me(
        payload: UpdateSelfRequest,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if user.display_name != payload.display_name:
            user.display_name = payload.display_name
            user.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(user)
        return _user_payload(user)

    @app.post("/api/auth/change-password", status_code=204)
    def change_password(
        payload: ChangePasswordRequest,
        request: Request,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> Response:
        if not verify_password(user.password_hash, payload.current_password):
            raise WorkflowError(
                400,
                "invalid_current_password",
                "current password is invalid",
            )
        user.password_hash = hash_password(payload.new_password)
        user.auth_version += 1
        user.updated_at = datetime.now(UTC)
        session.commit()
        _set_login_session(request, user)
        return Response(status_code=204)

    @app.get("/api/admin/users")
    def list_users(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        counts = {
            user_id: count
            for user_id, count in session.execute(
                select(
                    PreviewMatch.claimed_by_user_id,
                    func.count(PreviewMatch.game_id),
                )
                .where(PreviewMatch.claimed_by_user_id.is_not(None))
                .group_by(PreviewMatch.claimed_by_user_id)
            )
        }
        users = session.scalars(select(User).order_by(User.created_at, User.id))
        return {
            "items": [
                _admin_user_payload(user, counts.get(user.id, 0)) for user in users
            ]
        }

    @app.get("/api/admin/users/{user_id}")
    def get_user_summary(
        user_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(User, user_id)
        if user is None:
            raise _not_found("user")
        return _user_summary_payload(user)

    @app.patch("/api/admin/users/{user_id}")
    def update_user(
        user_id: int,
        payload: UpdateAdminUserRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(User, user_id)
        if user is None:
            raise _not_found("user")
        if user.role == "admin":
            raise WorkflowError(
                403,
                "administrator_account_protected",
                "administrator accounts cannot be modified here",
            )
        changed = False
        if (
            "display_name" in payload.model_fields_set
            and user.display_name != payload.display_name
        ):
            user.display_name = payload.display_name or ""
            changed = True
        if (
            "is_active" in payload.model_fields_set
            and user.is_active != payload.is_active
        ):
            user.is_active = bool(payload.is_active)
            user.auth_version += 1
            changed = True
        if changed:
            user.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(user)
        claimed_task_count = session.scalar(
            select(func.count(PreviewMatch.game_id)).where(
                PreviewMatch.claimed_by_user_id == user.id
            )
        )
        return _admin_user_payload(user, claimed_task_count or 0)

    @app.post("/api/admin/users/{user_id}/reset-password", status_code=204)
    def reset_user_password(
        user_id: int,
        payload: ResetPasswordRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> Response:
        user = session.get(User, user_id)
        if user is None:
            raise _not_found("user")
        if user.role == "admin":
            raise WorkflowError(
                403,
                "administrator_account_protected",
                "administrator passwords cannot be reset here",
            )
        user.password_hash = hash_password(payload.new_password)
        user.auth_version += 1
        user.updated_at = datetime.now(UTC)
        session.commit()
        return Response(status_code=204)

    @app.get("/api/settings/thufootball-credentials")
    async def get_thufootball_credentials(
        _: User = Depends(require_admin),
    ) -> dict[str, object]:
        async with thufootball_lock:
            return credential_status()

    @app.put("/api/settings/thufootball-credentials")
    async def put_thufootball_credentials(
        payload: THUFootballCredentialsRequest,
        _: User = Depends(require_admin),
    ) -> dict[str, object]:
        async with thufootball_lock:
            try:
                async with THUFootballClient(
                    openid=payload.openid,
                    session_key=payload.session_key,
                    load_environment=False,
                ) as client:
                    probe = await client.get_user_info()
            except (AuthenticationError, ConfigurationError) as exc:
                raise WorkflowError(
                    400,
                    "invalid_thufootball_credentials",
                    "THUFootball rejected the supplied credentials",
                ) from exc
            except THUFootballError as exc:
                raise WorkflowError(
                    502,
                    "thufootball_validation_failed",
                    "THUFootball credential validation failed",
                ) from exc
            if not probe.user_registered:
                raise WorkflowError(
                    400,
                    "invalid_thufootball_credentials",
                    "THUFootball user is not registered",
                )
            try:
                persist_credentials(
                    resolved_credential_env_path,
                    payload.openid,
                    payload.session_key,
                )
            except OSError as exc:
                raise WorkflowError(
                    500,
                    "credential_persistence_failed",
                    "THUFootball credentials could not be persisted",
                ) from exc
            activate_credentials(payload.openid, payload.session_key)
            return {
                **credential_status(),
                "user_registered": True,
                "updated_at": datetime.now(UTC).isoformat(),
            }

    @app.post("/api/preview-batches/create")
    async def create_preview_batches(
        payload: CreateBatchesRequest,
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        results = await create_batches(
            app.state.session_factory,
            resolved_settings,
            resolved_factories,
            payload.dates,
            [value.value for value in payload.competitions],
        )
        if results and all(
            item.get("status") == "failed"
            and item.get("error", {}).get("code") == "query_failed"
            for item in results
        ):
            raise WorkflowError(
                502,
                "preview_batch_queries_failed",
                "all preview batch queries failed",
                details={"results": results},
            )
        return {"results": results}

    @app.get("/api/preview-batches")
    def list_preview_batches(
        preview_date: date | None = None,
        competition: CompetitionValue | None = None,
        status: BatchStatus | None = None,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(PreviewBatch).order_by(
            PreviewBatch.preview_date.desc(),
            case(
                (PreviewBatch.competition == "male", 0),
                (PreviewBatch.competition == "female", 1),
                else_=2,
            ),
        )
        if preview_date is not None:
            statement = statement.where(PreviewBatch.preview_date == preview_date)
        if competition is not None:
            statement = statement.where(PreviewBatch.competition == competition.value)
        items = [batch_payload(session, batch, detail=False) for batch in session.scalars(statement)]
        if status is not None:
            items = [item for item in items if item["status"] == status.value]
        return {"items": items}

    @app.get("/api/preview-batches/{batch_id}")
    def get_preview_batch(
        batch_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        return batch_payload(session, batch, detail=True)

    @app.post("/api/preview-batches/{batch_id}/refresh-data")
    async def refresh_preview_batch(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        await refresh_batch(session, batch, resolved_factories)
        session.refresh(batch)
        return batch_payload(session, batch, detail=True)

    @app.patch("/api/preview-batches/{batch_id}")
    def update_preview_batch(
        batch_id: int,
        payload: UpdateBatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        changed = False
        for name in ("headline", "editors", "reviewers", "approvers"):
            if name not in payload.model_fields_set:
                continue
            value = getattr(payload, name)
            if name == "headline":
                value = (value or "").strip()
            if getattr(batch, name) != value:
                setattr(batch, name, value)
                changed = True
        if changed:
            batch.current_article_id = None
            batch.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(batch)
        return batch_payload(session, batch, detail=True)

    @app.get("/api/editorial-defaults")
    def get_editorial_defaults(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        value = session.get(EditorialDefaults, 1)
        if value is None:
            value = EditorialDefaults(id=1)
            session.add(value)
            session.commit()
            session.refresh(value)
        return {
            "editors": value.editors,
            "reviewers": value.reviewers,
            "approvers": value.approvers,
            "updated_at": value.updated_at.isoformat(),
        }

    @app.put("/api/editorial-defaults")
    def put_editorial_defaults(
        payload: EditorialRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        value = session.get(EditorialDefaults, 1)
        if value is None:
            value = EditorialDefaults(id=1)
            session.add(value)
        value.editors = payload.editors
        value.reviewers = payload.reviewers
        value.approvers = payload.approvers
        value.updated_at = datetime.now(UTC)
        session.commit()
        return {
            "editors": value.editors,
            "reviewers": value.reviewers,
            "approvers": value.approvers,
            "updated_at": value.updated_at.isoformat(),
        }

    @app.post("/api/preview-batches/{batch_id}/open-tasks")
    def open_tasks(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.get(PreviewBatch, batch_id) is None:
            raise _not_found("preview batch")
        matches = list(
            session.scalars(
                select(PreviewMatch).where(
                    PreviewMatch.batch_id == batch_id,
                    PreviewMatch.active.is_(True),
                )
            )
        )
        for match in matches:
            match.task_open = True
        session.commit()
        return {"game_ids": [match.game_id for match in matches], "task_open": True}

    @app.post("/api/preview-batches/{batch_id}/close-tasks")
    def close_tasks(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.get(PreviewBatch, batch_id) is None:
            raise _not_found("preview batch")
        matches = list(
            session.scalars(
                select(PreviewMatch).where(
                    PreviewMatch.batch_id == batch_id,
                    PreviewMatch.active.is_(True),
                )
            )
        )
        for match in matches:
            match.task_open = False
        session.commit()
        return {"game_ids": [match.game_id for match in matches], "task_open": False}

    @app.get("/api/preview-matches")
    def list_open_preview_matches(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            PreviewMatch.active.is_(True),
            PreviewMatch.task_open.is_(True),
        )
        return {
            "items": [
                {
                    **_task_payload(match, batch),
                    "preview_date": batch.preview_date.isoformat(),
                }
                for match, batch in rows
            ]
        }

    @app.get("/api/tasks/open")
    def list_open_tasks(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            PreviewMatch.active.is_(True),
            PreviewMatch.task_open.is_(True),
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.get("/api/tasks/wait_claim")
    def list_waiting_tasks(
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            PreviewMatch.active.is_(True),
            PreviewMatch.task_open.is_(True),
            PreviewMatch.claimed_by_user_id.is_(None),
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.get("/api/me/tasks")
    def list_my_tasks(
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            PreviewMatch.claimed_by_user_id == user.id,
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.post("/api/preview-matches/{game_id}/claim")
    def claim_preview_match(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(PreviewMatch, game_id)
        if current is None:
            raise _not_found("preview match")
        if current.claimed_by_user_id == user.id:
            batch = session.get(PreviewBatch, current.batch_id)
            return {
                "reused": True,
                "match": _task_payload(current, batch),
            }
        if not current.active or not current.task_open:
            raise WorkflowError(
                409,
                "task_unavailable",
                "preview match is not available for claiming",
            )
        result = session.execute(
            update(PreviewMatch)
            .where(
                PreviewMatch.game_id == game_id,
                PreviewMatch.active.is_(True),
                PreviewMatch.task_open.is_(True),
                PreviewMatch.claimed_by_user_id.is_(None),
            )
            .values(
                claimed_by_user_id=user.id,
                writers=[user.display_name],
                body_version=PreviewMatch.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            latest = session.get(PreviewMatch, game_id)
            if latest is not None and latest.claimed_by_user_id == user.id:
                batch = session.get(PreviewBatch, latest.batch_id)
                return {"reused": True, "match": _task_payload(latest, batch)}
            if latest is None:
                raise _not_found("preview match")
            if latest.claimed_by_user_id is not None:
                raise WorkflowError(
                    409,
                    "task_claimed",
                    "preview match has already been claimed",
                    details={"claimed_by_user_id": latest.claimed_by_user_id},
                )
            raise WorkflowError(
                409,
                "task_unavailable",
                "preview match is not available for claiming",
            )
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(PreviewBatch, current.batch_id)
        return {"reused": False, "match": _task_payload(current, batch)}

    @app.post("/api/preview-matches/{game_id}/release")
    def release_preview_match(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(PreviewMatch, game_id)
        if current is None:
            raise _not_found("preview match")
        claimed_by_user_id = current.claimed_by_user_id
        if claimed_by_user_id is None:
            raise WorkflowError(409, "task_not_claimed", "preview match is not claimed")
        if user.role != "admin" and claimed_by_user_id != user.id:
            raise WorkflowError(
                403,
                "not_claim_owner",
                "preview match is claimed by another user",
            )
        result = session.execute(
            update(PreviewMatch)
            .where(
                PreviewMatch.game_id == game_id,
                PreviewMatch.claimed_by_user_id == claimed_by_user_id,
            )
            .values(
                claimed_by_user_id=None,
                writers=[],
                body_version=PreviewMatch.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            raise WorkflowError(409, "task_changed", "preview match claim changed")
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(PreviewBatch, current.batch_id)
        return _task_payload(current, batch)

    @app.post("/api/preview-matches/{game_id}/assign")
    def assign_preview_match(
        game_id: int,
        payload: AssignMatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(PreviewMatch, game_id)
        if current is None:
            raise _not_found("preview match")
        target = None if payload.user_id is None else session.get(User, payload.user_id)
        if payload.user_id is not None and (
            target is None
            or not target.is_active
            or target.role not in {"user", "admin"}
        ):
            raise WorkflowError(
                409,
                "invalid_assignment_target",
                "assignment target must be an active user",
            )
        target_id = target.id if target is not None else None
        writers = [target.display_name] if target is not None else []
        if current.claimed_by_user_id == target_id and current.writers == writers:
            batch = session.get(PreviewBatch, current.batch_id)
            return _task_payload(current, batch)
        session.execute(
            update(PreviewMatch)
            .where(PreviewMatch.game_id == game_id)
            .values(
                claimed_by_user_id=target_id,
                writers=writers,
                body_version=PreviewMatch.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(PreviewBatch, current.batch_id)
        return _task_payload(current, batch)

    @app.patch("/api/preview-matches/{game_id}")
    def update_preview_match(
        game_id: int,
        payload: UpdateMatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(PreviewMatch, game_id)
        if current is None:
            raise _not_found("preview match")
        writers = current.writers if payload.writers is None else payload.writers
        body = current.body if payload.body is None else payload.body
        return _save_match_content(
            session,
            current,
            expected_version=payload.expected_version,
            writers=writers,
            body=body,
        )

    @app.patch("/api/preview-matches/{game_id}/body")
    def update_preview_match_body(
        game_id: int,
        payload: UpdateBodyRequest,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(PreviewMatch, game_id)
        if current is None:
            raise _not_found("preview match")
        if user.role != "admin" and current.claimed_by_user_id != user.id:
            raise WorkflowError(
                403,
                "not_claim_owner",
                "preview match is claimed by another user",
            )
        return _save_match_content(
            session,
            current,
            expected_version=payload.expected_version,
            writers=current.writers,
            body=payload.body,
        )

    @app.put("/api/weather/{target_date}")
    def put_weather(
        target_date: date,
        payload: WeatherRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        value = set_manual_weather(
            session,
            target_date,
            condition=payload.condition,
            low_c=payload.low_c,
            high_c=payload.high_c,
            wind_direction=payload.wind_direction,
            wind_level=payload.wind_level,
        )
        session.commit()
        return weather_payload(value) or {}

    @app.post("/api/preview-batches/{batch_id}/cover")
    async def upload_cover(
        batch_id: int,
        file: UploadFile = File(...),
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        content = await file.read(10 * 1024 * 1024 + 1)
        save_batch_cover(session, resolved_settings, batch, content)
        session.commit()
        return batch_payload(session, batch, detail=False)

    @app.put("/api/preview-batches/{batch_id}/cover-media-id")
    def put_cover_media_id(
        batch_id: int,
        payload: CoverMediaIdRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        set_batch_cover_media_id(batch, payload.media_id)
        session.commit()
        return batch_payload(session, batch, detail=False)

    @app.post("/api/preview-batches/{batch_id}/render")
    def render_preview_batch(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(PreviewBatch, batch_id)
        if batch is None:
            raise _not_found("preview batch")
        record, reused = render_batch(session, resolved_settings, batch)
        session.commit()
        session.refresh(record)
        return {"reused": reused, "article": article_payload(record, batch.current_article_id)}

    @app.get("/api/articles/{article_id}")
    def get_article(
        article_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        record = session.get(ArticleRecord, article_id)
        if record is None:
            raise _not_found("article")
        batch = session.get(PreviewBatch, record.batch_id)
        return article_payload(record, batch.current_article_id if batch else None)

    @app.get("/api/articles/{article_id}/preview", response_class=HTMLResponse)
    def preview_article(
        article_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        record = session.get(ArticleRecord, article_id)
        if record is None:
            raise _not_found("article")
        return HTMLResponse(
            record.body_html,
            headers={"Referrer-Policy": "no-referrer"},
        )

    @app.post("/api/wechat-drafts")
    async def post_wechat_draft(
        payload: CreateWechatDraftRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        result = await create_wechat_draft(
            session,
            resolved_settings,
            resolved_factories,
            payload.article_ids,
            confirm=payload.confirm,
        )
        session.commit()
        return result

    @app.get("/api/wechat-drafts/{draft_id}")
    def get_wechat_draft(
        draft_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        draft = session.get(WechatDraft, draft_id)
        if draft is None:
            raise _not_found("wechat draft")
        return draft_payload(draft)

    resolved_frontend_dist = (
        Path(frontend_dist)
        if frontend_dist is not None
        else PROJECT_ROOT / "frontend" / "dist"
    )
    if (resolved_frontend_dist / "index.html").is_file():
        app.mount(
            "/",
            StaticFiles(directory=resolved_frontend_dist, html=True),
            name="frontend",
        )

    return app
