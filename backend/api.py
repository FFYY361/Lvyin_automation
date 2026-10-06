"""FastAPI application for the website workflow."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai_preview.source import prepare_prompt_repository
from thufootball import (
    AuthenticationError,
    ConfigurationError,
    THUFootballClient,
    THUFootballError,
    THUFootballQueryService,
)
from thufootball.database import FootballDataRepository

from .ai_preview import (
    _title_prefix,
    ai_preview_context,
    build_title_material,
    configured_website_profile,
    institution_detail_payload,
    institution_summary_payload,
    model_config_hash,
    model_options_payload,
    prepare_generation,
    prepare_title_generation,
    prompt_copy_payload,
    prompt_templates_payload,
    recover_interrupted_generations,
    require_match_access,
    result_payload,
    run_generation,
    run_title_generation,
    title_result_payload,
    update_institution_descriptions,
    update_match_manual_descriptions,
)
from .auth import (
    get_session,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)
from .config import DEFAULT_ENV_FILE, PROJECT_ROOT, WebsiteSettings
from .credentials import (
    AutomaticCredentialError,
    AutomaticCredentialManager,
    AutoRefreshingTHUFootballClient,
    activate_credentials,
    credential_status,
    persist_credentials,
)
from .database import create_database_engine, create_session_factory
from .models import (
    AIPreviewResult,
    AITitleResult,
    ArticleRecord,
    Batch,
    EditorialDefaults,
    Match,
    PromptTemplate,
    User,
    WechatDraft,
)
from .schemas import (
    AIPreviewGenerationRequest,
    AITitleGenerationRequest,
    AssignMatchRequest,
    BatchStatus,
    ChangePasswordRequest,
    CompetitionValue,
    CoverMediaIdRequest,
    CreateBatchesRequest,
    CreateWechatDraftRequest,
    EditorialRequest,
    InstitutionDescriptionsRequest,
    LoginRequest,
    MatchManualDescriptionsRequest,
    PromptUpdateRequest,
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
    WebsiteReportSession,
    WorkflowError,
    article_payload,
    batch_payload,
    close_batch_tasks,
    create_batches,
    create_wechat_draft,
    draft_payload,
    match_payload,
    refresh_batch,
    render_batch,
    render_match_report,
    render_report_batch,
    report_content_paths,
    save_batch_cover,
    set_batch_cover_media_id,
    set_manual_weather,
    weather_payload,
)

logger = logging.getLogger(__name__)


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


def _task_rows(session: Session, *conditions: Any) -> list[tuple[Match, Batch]]:
    return list(
        session.execute(
            select(Match, Batch)
            .join(Batch, Batch.id == Match.batch_id)
            .where(*conditions)
            .order_by(Match.kickoff, Match.game_id)
        )
    )


def _task_payload(match: Match, batch: Batch) -> dict[str, Any]:
    return {**match_payload(match), "competition": batch.competition}


def _match_content_payload(match: Match) -> dict[str, Any]:
    return {
        "game_id": match.game_id,
        "writers": match.writers,
        "body": match.body,
        "body_version": match.body_version,
    }


def _body_version_conflict(match: Match | None) -> WorkflowError:
    return WorkflowError(
        409,
        "body_version_conflict",
        "match was updated",
        details={
            "body_version": match.body_version if match else None,
            "writers": match.writers if match else [],
            "body": match.body if match else "",
        },
    )


def _invalidate_match_batch(session: Session, match: Match) -> None:
    batch = session.get(Batch, match.batch_id)
    if batch is not None:
        batch.current_preview_article_id = None
        batch.updated_at = datetime.now(UTC)


def _save_match_content(
    session: Session,
    match: Match,
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
        update(Match)
        .where(
            Match.game_id == match.game_id,
            Match.body_version == expected_version,
        )
        .values(
            writers=writers,
            body=normalized_body,
            body_version=Match.body_version + 1,
            updated_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise _body_version_conflict(session.get(Match, match.game_id))
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
    resolved_credential_env_path = Path(credential_env_path)
    automatic_credentials = AutomaticCredentialManager.from_environment()

    def load_outcome_catalog():
        with session_factory() as session:
            return FootballDataRepository(session).load_outcome_catalog()

    def create_environment_client() -> THUFootballClient:
        client_options: dict[str, Any] = {
            "openid": os.environ.get("THUFOOTBALL_OPENID") or None,
            "session_key": os.environ.get("THUFOOTBALL_SESSION_KEY") or None,
            "load_environment": False,
        }
        if automatic_credentials.configured:
            return AutoRefreshingTHUFootballClient(
                **client_options,
                credential_refresher=automatic_credentials.refresh,
                authentication_retries=2,
            )
        return THUFootballClient(**client_options)

    @asynccontextmanager
    async def process_environment_queries():
        client = create_environment_client()
        async with client:
            async with THUFootballQueryService(
                client, outcome_catalog=load_outcome_catalog()
            ) as service:
                yield service

    @asynccontextmanager
    async def process_environment_reports():
        async with WebsiteReportSession(
            load_outcome_catalog(), client=create_environment_client()
        ) as service:
            yield service

    base_factories = external_factories or ExternalFactories(
        queries=process_environment_queries,
        reports=process_environment_reports,
    )
    thufootball_lock = asyncio.Lock()

    @asynccontextmanager
    async def locked_queries():
        async with thufootball_lock:
            async with base_factories.queries() as service:
                yield service

    @asynccontextmanager
    async def locked_reports():
        async with thufootball_lock:
            async with base_factories.reports() as service:
                yield service

    resolved_factories = replace(
        base_factories,
        queries=locked_queries,
        reports=locked_reports,
    )
    resolved_settings.artifact_root.mkdir(parents=True, exist_ok=True)
    ai_tasks: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if hasattr(session_factory, "begin"):
            recover_interrupted_generations(session_factory)
        if automatic_credentials.configured:
            try:
                await automatic_credentials.refresh()
            except AutomaticCredentialError as exc:
                logger.warning(
                    "Automatic THUFootball credential refresh failed at startup: %s. "
                    "The server will remain available for manual credential updates.",
                    exc,
                )
        yield
        pending_tasks = list(ai_tasks)
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="清华绿茵内容管理 API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.docs_enabled else None,
        redoc_url="/redoc" if resolved_settings.docs_enabled else None,
        openapi_url="/openapi.json" if resolved_settings.docs_enabled else None,
    )
    app.state.settings = resolved_settings
    app.state.session_factory = session_factory
    app.state.external_factories = resolved_factories
    app.state.prompt_client_factory = create_environment_client
    app.state.thufootball_lock = thufootball_lock
    app.state.automatic_credentials = automatic_credentials
    app.state.ai_tasks = ai_tasks
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(resolved_settings.allowed_hosts),
    )
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

    @app.get("/api/health")
    def health(session: Session = Depends(get_session)) -> JSONResponse:
        try:
            session.execute(text("SELECT 1"))
            artifact_root = resolved_settings.artifact_root
            if not (
                artifact_root.is_dir()
                and os.access(artifact_root, os.R_OK | os.W_OK)
            ):
                raise OSError("artifact root is unavailable")
        except Exception:
            logger.exception("Website health check failed")
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    @app.post("/api/auth/register", status_code=201)
    def register(
        payload: RegisterRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if not secrets.compare_digest(
            payload.invite_code,
            resolved_settings.invite_code,
        ):
            raise WorkflowError(403, "invalid_invite_code", "invalid invite code")
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
                    Match.claimed_by_user_id,
                    func.count(Match.game_id),
                )
                .where(Match.claimed_by_user_id.is_not(None))
                .group_by(Match.claimed_by_user_id)
            )
        }
        users = session.scalars(select(User).order_by(User.created_at, User.id))
        return {
            "items": [
                _admin_user_payload(user, counts.get(user.id, 0)) for user in users
            ]
        }

    @app.get("/api/admin/prompts")
    def list_prompts(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return prompt_templates_payload(session)

    @app.put("/api/admin/prompts/{prompt_key}")
    def update_prompt(
        prompt_key: str,
        payload: PromptUpdateRequest,
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        row = session.get(PromptTemplate, prompt_key)
        if row is None:
            raise _not_found("prompt")
        if payload.expected_updated_at and row.updated_at.isoformat() != payload.expected_updated_at:
            raise WorkflowError(409, "prompt_updated", "Prompt 已被其他管理员修改，请刷新后重试。")
        row.content = payload.content.replace("\r\n", "\n").replace("\r", "\n").strip()
        row.updated_by_user_id = user.id
        row.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        return {"key": row.key, "content": row.content, "updated_at": row.updated_at.isoformat(), "updated_by": user.display_name}

    @app.get("/api/admin/institutions")
    def list_institutions(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        repository = FootballDataRepository(session)
        return {
            "items": [
                institution_summary_payload(record)
                for record in repository.list_institutions()
            ]
        }

    @app.get("/api/admin/institutions/{institution_name}")
    def get_institution(
        institution_name: str,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            record = FootballDataRepository(session).get_institution(
                institution_name
            )
        except ConfigurationError as exc:
            raise _not_found("institution") from exc
        return institution_detail_payload(record)

    @app.put("/api/admin/institutions/{institution_name}")
    def put_institution(
        institution_name: str,
        payload: InstitutionDescriptionsRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return update_institution_descriptions(
            session,
            institution_name,
            male_description=payload.male_description,
            female_description=payload.female_description,
            futsal_description=payload.futsal_description,
            player_descriptions=payload.player_descriptions,
        )

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
            select(func.count(Match.game_id)).where(
                Match.claimed_by_user_id == user.id
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

    @app.post("/api/batches/create")
    async def create_batch_records(
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
                "batch_queries_failed",
                "all batch queries failed",
                details={"results": results},
            )
        return {"results": results}

    @app.get("/api/batches")
    def list_batches(
        batch_date: date | None = None,
        competition: CompetitionValue | None = None,
        preview_status: BatchStatus | None = None,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Batch).order_by(
            Batch.batch_date.desc(),
            case(
                (Batch.competition == "male", 0),
                (Batch.competition == "female", 1),
                else_=2,
            ),
        )
        if batch_date is not None:
            statement = statement.where(Batch.batch_date == batch_date)
        if competition is not None:
            statement = statement.where(Batch.competition == competition.value)
        items = [batch_payload(session, batch, detail=False) for batch in session.scalars(statement)]
        if preview_status is not None:
            items = [
                item for item in items
                if item["preview_status"] == preview_status.value
            ]
        return {"items": items}

    @app.get("/api/batches/{batch_id}")
    def get_batch(
        batch_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        return batch_payload(session, batch, detail=True)

    @app.post("/api/batches/{batch_id}/refresh-data")
    async def refresh_batch_data(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        await refresh_batch(session, batch, resolved_factories)
        session.refresh(batch)
        return batch_payload(session, batch, detail=True)

    @app.patch("/api/batches/{batch_id}")
    def update_batch(
        batch_id: int,
        payload: UpdateBatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
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
            batch.current_preview_article_id = None
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

    @app.post("/api/batches/{batch_id}/open-tasks")
    def open_tasks(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.get(Batch, batch_id) is None:
            raise _not_found("batch")
        matches = list(
            session.scalars(
                select(Match).where(
                    Match.batch_id == batch_id,
                    Match.active.is_(True),
                )
            )
        )
        for match in matches:
            match.task_open = True
        session.commit()
        return {"game_ids": [match.game_id for match in matches], "task_open": True}

    @app.post("/api/batches/{batch_id}/close-tasks")
    def close_tasks(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.get(Batch, batch_id) is None:
            raise _not_found("batch")
        game_ids = close_batch_tasks(session, [batch_id])
        session.commit()
        return {"game_ids": game_ids, "task_open": False}

    @app.get("/api/matches")
    def list_open_matches(
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            Match.active.is_(True),
            Match.task_open.is_(True),
        )
        return {
            "items": [
                {
                    **_task_payload(match, batch),
                    "batch_date": batch.batch_date.isoformat(),
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
            Match.active.is_(True),
            Match.task_open.is_(True),
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.get("/api/tasks/wait_claim")
    def list_waiting_tasks(
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            Match.active.is_(True),
            Match.task_open.is_(True),
            Match.claimed_by_user_id.is_(None),
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.get("/api/me/tasks")
    def list_my_tasks(
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        rows = _task_rows(
            session,
            Match.claimed_by_user_id == user.id,
        )
        return {"items": [_task_payload(match, batch) for match, batch in rows]}

    @app.post("/api/matches/{game_id}/claim")
    def claim_match(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(Match, game_id)
        if current is None:
            raise _not_found("match")
        if current.claimed_by_user_id == user.id:
            batch = session.get(Batch, current.batch_id)
            return {
                "reused": True,
                "match": _task_payload(current, batch),
            }
        if not current.active or not current.task_open:
            raise WorkflowError(
                409,
                "task_unavailable",
                "match is not available for claiming",
            )
        result = session.execute(
            update(Match)
            .where(
                Match.game_id == game_id,
                Match.active.is_(True),
                Match.task_open.is_(True),
                Match.claimed_by_user_id.is_(None),
            )
            .values(
                claimed_by_user_id=user.id,
                writers=[user.display_name],
                body_version=Match.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            latest = session.get(Match, game_id)
            if latest is not None and latest.claimed_by_user_id == user.id:
                batch = session.get(Batch, latest.batch_id)
                return {"reused": True, "match": _task_payload(latest, batch)}
            if latest is None:
                raise _not_found("match")
            if latest.claimed_by_user_id is not None:
                raise WorkflowError(
                    409,
                    "task_claimed",
                    "match has already been claimed",
                    details={"claimed_by_user_id": latest.claimed_by_user_id},
                )
            raise WorkflowError(
                409,
                "task_unavailable",
                "match is not available for claiming",
            )
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(Batch, current.batch_id)
        return {"reused": False, "match": _task_payload(current, batch)}

    @app.post("/api/matches/{game_id}/release")
    def release_match(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(Match, game_id)
        if current is None:
            raise _not_found("match")
        claimed_by_user_id = current.claimed_by_user_id
        if claimed_by_user_id is None:
            raise WorkflowError(409, "task_not_claimed", "match is not claimed")
        if user.role != "admin" and claimed_by_user_id != user.id:
            raise WorkflowError(
                403,
                "not_claim_owner",
                "match is claimed by another user",
            )
        result = session.execute(
            update(Match)
            .where(
                Match.game_id == game_id,
                Match.claimed_by_user_id == claimed_by_user_id,
            )
            .values(
                claimed_by_user_id=None,
                writers=[],
                body_version=Match.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            raise WorkflowError(409, "task_changed", "match claim changed")
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(Batch, current.batch_id)
        return _task_payload(current, batch)

    @app.post("/api/matches/{game_id}/assign")
    def assign_match(
        game_id: int,
        payload: AssignMatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(Match, game_id)
        if current is None:
            raise _not_found("match")
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
            batch = session.get(Batch, current.batch_id)
            return _task_payload(current, batch)
        session.execute(
            update(Match)
            .where(Match.game_id == game_id)
            .values(
                claimed_by_user_id=target_id,
                writers=writers,
                body_version=Match.body_version + 1,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        _invalidate_match_batch(session, current)
        session.commit()
        session.refresh(current)
        batch = session.get(Batch, current.batch_id)
        return _task_payload(current, batch)

    @app.patch("/api/matches/{game_id}")
    def update_match(
        game_id: int,
        payload: UpdateMatchRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(Match, game_id)
        if current is None:
            raise _not_found("match")
        writers = current.writers if payload.writers is None else payload.writers
        body = current.body if payload.body is None else payload.body
        return _save_match_content(
            session,
            current,
            expected_version=payload.expected_version,
            writers=writers,
            body=body,
        )

    @app.patch("/api/matches/{game_id}/body")
    def update_match_body(
        game_id: int,
        payload: UpdateBodyRequest,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        current = session.get(Match, game_id)
        if current is None:
            raise _not_found("match")
        if user.role != "admin" and current.claimed_by_user_id != user.id:
            raise WorkflowError(
                403,
                "not_claim_owner",
                "match is claimed by another user",
            )
        return _save_match_content(
            session,
            current,
            expected_version=payload.expected_version,
            writers=current.writers,
            body=payload.body,
        )

    async def prepare_match_football(session: Session, game_id: int, *, include_details: bool = True):
        match = session.get(Match, game_id)
        try:
            async with app.state.prompt_client_factory() as client:
                return await prepare_prompt_repository(
                    game_id, repository=FootballDataRepository(session), client=client,
                    tournament_id=match.tournament_id, include_details=include_details,
                )
        except (THUFootballError, AutomaticCredentialError, ValueError) as exc:
            raise WorkflowError(
                500, "ai_preview_context_invalid",
                "AI 前瞻资料读取失败，暂时无法组装 Prompt。",
            ) from exc

    @app.get("/api/matches/{game_id}/ai-preview-context")
    async def get_ai_preview_context(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        require_match_access(session, game_id, user)
        repository = await prepare_match_football(session, game_id)
        return ai_preview_context(session, game_id, repository=repository)

    @app.get("/api/matches/{game_id}/ai-preview-prompt")
    async def get_ai_preview_prompt(
        game_id: int,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        require_match_access(session, game_id, user)
        repository = await prepare_match_football(session, game_id)
        return prompt_copy_payload(session, game_id, repository=repository)

    @app.post("/api/matches/{game_id}/ai-preview-generations")
    async def create_ai_preview_generation(
        game_id: int,
        payload: AIPreviewGenerationRequest,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        require_match_access(session, game_id, user)
        repository = await prepare_match_football(session, game_id)
        result, material = prepare_generation(
            session,
            game_id=game_id,
            model_profile=payload.model_profile,
            requested_by_user_id=user.id,
            repository=repository,
        )
        if material is not None:
            config_hash = result.model_config_hash
            task = asyncio.create_task(
                run_generation(
                    app.state.session_factory,
                    app.state.external_factories.ai,
                    game_id=game_id,
                    model_profile=result.model_profile,
                    material=material,
                    request_token=result.request_token,
                    config_hash=config_hash,
                )
            )
            ai_tasks.add(task)
            task.add_done_callback(ai_tasks.discard)
        status_code = 202 if result.status in {"queued", "running"} else 200
        return JSONResponse(
            status_code=status_code,
            content={
                **result_payload(result),
                "reused": material is None,
            },
        )

    @app.get(
        "/api/matches/{game_id}/ai-preview-results/{model_profile}"
    )
    def get_ai_preview_result(
        game_id: int,
        model_profile: str,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        require_match_access(session, game_id, user)
        profile = configured_website_profile(model_profile)
        result = session.get(AIPreviewResult, (game_id, model_profile))
        if result is None:
            raise _not_found("AI preview result")
        return result_payload(
            result,
            current_config_hash=model_config_hash(profile),
        )

    @app.get("/api/batches/{batch_id}/ai-title-context")
    def get_ai_title_context(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        material = build_title_material(session, batch)
        models, hashes = model_options_payload()
        results = {
            result.model_profile: title_result_payload(result, material.prompt_hash, hashes.get(result.model_profile))
            for result in session.scalars(select(AITitleResult).where(AITitleResult.batch_id == batch_id))
        }
        return {"prefix": _title_prefix(batch), "models": models, "results": results}

    @app.get("/api/batches/{batch_id}/ai-title-prompt")
    def get_ai_title_prompt(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        material = build_title_material(session, batch)
        return {
            "prompt_hash": material.prompt_hash,
            "messages": [{"role": item.role, "content": item.content} for item in material.messages],
            "copy_text": "\n\n".join(f"【{item.role}】\n{item.content}" for item in material.messages),
        }

    @app.post("/api/batches/{batch_id}/ai-title-generations")
    async def create_ai_title_generation(
        batch_id: int,
        payload: AITitleGenerationRequest,
        user: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        result, material = prepare_title_generation(session, batch_id=batch_id, model_profile=payload.model_profile, requested_by_user_id=user.id)
        if material is not None:
            task = asyncio.create_task(run_title_generation(app.state.session_factory, app.state.external_factories.ai, batch_id=batch_id, model_profile=result.model_profile, material=material, request_token=result.request_token, config_hash=result.model_config_hash))
            ai_tasks.add(task)
            task.add_done_callback(ai_tasks.discard)
        return JSONResponse(
            status_code=202 if result.status in {"queued", "running"} else 200,
            content={**title_result_payload(result), "reused": material is None},
        )

    @app.get("/api/batches/{batch_id}/ai-title-results/{model_profile}")
    def get_ai_title_result(
        batch_id: int,
        model_profile: str,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        result = session.get(AITitleResult, (batch_id, model_profile))
        if result is None:
            raise _not_found("AI title result")
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        profile = configured_website_profile(model_profile)
        return title_result_payload(result, current_prompt_hash=build_title_material(session, batch).prompt_hash, current_config_hash=model_config_hash(profile))

    @app.put("/api/matches/{game_id}/manual-descriptions")
    async def put_match_manual_descriptions(
        game_id: int,
        payload: MatchManualDescriptionsRequest,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        require_match_access(session, game_id, user)
        repository = await prepare_match_football(session, game_id, include_details=False)
        return update_match_manual_descriptions(
            session,
            game_id,
            home_team_description=payload.home_team.team_description,
            home_player_descriptions=payload.home_team.player_descriptions,
            away_team_description=payload.away_team.team_description,
            away_player_descriptions=payload.away_team.player_descriptions,
            repository=repository,
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

    @app.post("/api/batches/{batch_id}/cover")
    async def upload_cover(
        batch_id: int,
        file: UploadFile = File(...),
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        content = await file.read(10 * 1024 * 1024 + 1)
        save_batch_cover(session, resolved_settings, batch, content)
        session.commit()
        return batch_payload(session, batch, detail=False)

    @app.put("/api/batches/{batch_id}/cover-media-id")
    def put_cover_media_id(
        batch_id: int,
        payload: CoverMediaIdRequest,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        set_batch_cover_media_id(batch, payload.media_id)
        session.commit()
        return batch_payload(session, batch, detail=False)

    @app.post("/api/batches/{batch_id}/render-preview")
    def render_preview_batch(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        record, reused = render_batch(session, resolved_settings, batch)
        session.commit()
        session.refresh(record)
        return {
            "reused": reused,
            "article": article_payload(record, batch.current_preview_article_id),
        }

    @app.post("/api/batches/{batch_id}/render-report")
    async def render_batch_report_article(
        batch_id: int,
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise _not_found("batch")
        record, reused, diagnostics = await render_report_batch(
            session, resolved_settings, resolved_factories, batch
        )
        session.commit()
        session.refresh(record)
        return {
            "reused": reused,
            "article": article_payload(record, batch.current_report_article_id),
            "diagnostics": diagnostics,
        }

    @app.get("/api/matches/{game_id}/report")
    def get_match_report(
        game_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        match = session.get(Match, game_id)
        if match is None:
            raise _not_found("match")
        return match_payload(match)

    @app.get("/api/matches/{game_id}/report/content")
    def get_match_report_content(
        game_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        match = session.get(Match, game_id)
        if match is None:
            raise _not_found("match")
        paths = report_content_paths(resolved_settings, match)
        image = paths.get("image")
        text = paths.get("text")
        content = {
            "image": (
                None
                if image is None
                else {
                    "media_type": "image/png",
                    "base64": base64.b64encode(image.read_bytes()).decode("ascii"),
                }
            ),
            "text": (
                None
                if text is None
                else {
                    "media_type": "text/plain; charset=utf-8",
                    "content": text.read_text(encoding="utf-8"),
                }
            ),
        }
        return JSONResponse(
            content=content,
            headers={"Cache-Control": "private, no-cache"},
        )

    @app.post("/api/matches/{game_id}/render-report")
    async def render_single_match_report(
        game_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        match = session.get(Match, game_id)
        if match is None:
            raise _not_found("match")
        reused, diagnostics = await render_match_report(
            session, resolved_settings, resolved_factories, match
        )
        session.commit()
        session.refresh(match)
        return {
            "reused": reused,
            "match": match_payload(match),
            "diagnostics": diagnostics,
        }

    @app.get("/api/articles/candidates")
    def list_article_candidates(
        article_type: str = "all",
        _: User = Depends(require_admin),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if article_type not in {"all", "preview", "report"}:
            raise WorkflowError(
                422,
                "invalid_article_type",
                "article_type must be all, preview or report",
            )
        current_article_conditions = []
        if article_type in {"all", "preview"}:
            current_article_conditions.append(
                and_(
                    ArticleRecord.id == Batch.current_preview_article_id,
                    ArticleRecord.article_type == "preview",
                )
            )
        if article_type in {"all", "report"}:
            current_article_conditions.append(
                and_(
                    ArticleRecord.id == Batch.current_report_article_id,
                    ArticleRecord.article_type == "report",
                )
            )
        rows = session.execute(
            select(
                Batch.id.label("batch_id"),
                Batch.batch_date,
                Batch.competition,
                ArticleRecord.id.label("article_id"),
                ArticleRecord.article_type,
                ArticleRecord.version_number,
                ArticleRecord.title,
            )
            .join(ArticleRecord, or_(*current_article_conditions))
            .where(ArticleRecord.is_complete.is_(True))
            .order_by(
                Batch.batch_date.desc(),
                Batch.id.desc(),
                ArticleRecord.article_type,
            )
        ).mappings()
        return {
            "items": [
                {
                    "batch": {
                        "id": row["batch_id"],
                        "batch_date": row["batch_date"].isoformat(),
                        "competition": row["competition"],
                    },
                    "article": {
                        "id": row["article_id"],
                        "article_type": row["article_type"],
                        "version_number": row["version_number"],
                        "title": row["title"],
                    },
                }
                for row in rows
            ]
        }

    @app.get("/api/articles/{article_id}")
    def get_article(
        article_id: int,
        _: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        record = session.get(ArticleRecord, article_id)
        if record is None:
            raise _not_found("article")
        batch = session.get(Batch, record.batch_id)
        current_id = (
            batch.current_preview_article_id
            if batch is not None and record.article_type == "preview"
            else batch.current_report_article_id
            if batch is not None
            else None
        )
        return article_payload(record, current_id)

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
