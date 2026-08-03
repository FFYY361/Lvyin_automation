from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import CHAR, create_engine, delete, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

import backend.api as backend_api
from backend.api import create_app
from backend.artifacts import resolve_storage_key
from backend.auth import hash_password
from backend.config import WebsiteSettings, load_env_file
from backend.credentials import persist_credentials
from backend.models import (
    Base,
    PreviewBatch,
    PreviewMatch,
    User,
    Weather,
    WechatDraft,
)
from backend.workflow import (
    ExternalFactories,
    WorkflowError,
    article_domain,
    assemble_source,
    batch_status,
    create_wechat_draft,
    match_payload,
    render_batch,
    save_batch_cover,
    set_batch_cover_media_id,
    set_manual_weather,
    upsert_source,
)
from thufootball import UserProbe
from wechat_official import CoverMediaId, DraftReceipt

SHANGHAI = timezone(timedelta(hours=8))


@pytest.fixture(scope="session")
def postgres_engine():
    load_env_file()
    database_url = os.environ.get("WEBSITE_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("WEBSITE_DATABASE_URL is not configured")
    administration = create_engine(database_url)
    schema = f"website_test_{uuid.uuid4().hex}"
    try:
        with administration.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except OperationalError as exc:
        administration.dispose()
        pytest.skip(f"PostgreSQL is unavailable: {exc}")
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()


@pytest.fixture
def session_factory(postgres_engine):
    connection = postgres_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def settings(tmp_path: Path) -> WebsiteSettings:
    return WebsiteSettings(
        database_url="postgresql+psycopg://unused",
        artifact_root=tmp_path / "artifacts",
        default_cover_media_id="website-default-cover",
        cookie_name="test_session",
        cookie_secret="test-cookie-secret-that-is-long-enough",
        cookie_secure=False,
    )


def _batch(
    session: Session,
    *,
    target_date: date = date(2026, 8, 8),
    competition: str = "female",
    complete: bool,
    game_id: int = 9001,
) -> PreviewBatch:
    batch = PreviewBatch(
        preview_date=target_date,
        competition=competition,
        headline="周末前瞻" if complete else "",
        editors=["编辑"] if complete else [],
        reviewers=["责编"] if complete else [],
        approvers=["审核"] if complete else [],
        cover_kind="media_id",
        cover_storage_key="website-default-cover",
        cover_content_type=None,
    )
    session.add(batch)
    session.flush()
    if not complete:
        return batch
    session.add(
        Weather(
            date=target_date,
            adcode="110108",
            region_name="海淀区",
            condition="晴",
            low_c=20,
            high_c=30,
            wind_direction="南风",
            wind_level="2级",
            source="manual",
            report_time=datetime.now(UTC),
        )
    )
    session.add(
        PreviewMatch(
            game_id=game_id,
            batch_id=batch.id,
            tournament_id=123,
            tournament_name="2026 马杯女足",
            competition_name="女足",
            stage="小组赛",
            kickoff=datetime.combine(
                target_date,
                datetime.min.time().replace(hour=15),
                tzinfo=SHANGHAI,
            ),
            venue="紫荆操场",
            home_snapshot={
                "team_id": game_id * 2,
                "name": "主队",
                "short_name": "主队",
                "previous_outcomes": [],
                "current_results": [],
            },
            away_snapshot={
                "team_id": game_id * 2 + 1,
                "name": "客队",
                "short_name": "客队",
                "previous_outcomes": [],
                "current_results": [],
            },
            head_to_head_snapshot=[],
            writers=["作者"],
            body="第一段。\n\n第二段。",
        )
    )
    session.flush()
    return batch


def test_required_default_cover_and_username_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WEBSITE_DATABASE_URL", "postgresql+psycopg://example")
    monkeypatch.setenv("WEBSITE_COOKIE_SECRET", "x" * 32)
    monkeypatch.delenv("WEBSITE_DEFAULT_COVER_MEDIA_ID", raising=False)
    with pytest.raises(RuntimeError, match="WEBSITE_DEFAULT_COVER_MEDIA_ID"):
        WebsiteSettings.from_environment(env_path=tmp_path / "missing.env")


def test_website_env_loader_replaces_empty_process_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "THUFOOTBALL_OPENID=file-openid\n"
        "THUFOOTBALL_SESSION_KEY=file-session\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THUFOOTBALL_OPENID", "")
    monkeypatch.setenv("THUFOOTBALL_SESSION_KEY", "")

    load_env_file(env_path)

    assert os.environ["THUFOOTBALL_OPENID"] == "file-openid"
    assert os.environ["THUFOOTBALL_SESSION_KEY"] == "file-session"


def test_database_columns_and_indexes_match_plan(postgres_engine) -> None:
    database = inspect(postgres_engine)
    expected = {
        "users": {
            "id", "username", "display_name", "password_hash", "role",
            "is_active", "auth_version", "created_at", "updated_at",
        },
        "preview_batches": {
            "id", "preview_date", "competition", "headline", "editors",
            "reviewers", "approvers", "cover_kind", "cover_storage_key",
            "cover_content_type", "current_article_id", "last_error_code",
            "last_error_message", "last_error_at", "created_at", "updated_at",
        },
        "preview_matches": {
            "game_id", "batch_id", "tournament_id", "tournament_name",
            "competition_name", "stage", "kickoff", "venue", "home_snapshot",
            "away_snapshot", "head_to_head_snapshot", "active", "task_open",
            "claimed_by_user_id", "writers", "body", "body_version",
            "created_at", "updated_at",
        },
        "weather": {
            "date", "adcode", "region_name", "condition", "low_c", "high_c",
            "wind_direction", "wind_level", "source", "report_time",
        },
        "editorial_defaults": {
            "id", "editors", "reviewers", "approvers", "updated_at",
        },
        "articles": {
            "id", "batch_id", "version_number", "input_snapshot", "title",
            "body_html", "author", "digest", "source_url", "template_version",
            "content_fingerprint", "cover_kind", "cover_storage_key",
            "cover_sha256", "is_complete", "missing_fields", "created_at",
        },
        "wechat_drafts": {
            "id", "articles", "publication_fingerprint", "media_id",
            "wechat_created_at", "created_at",
        },
    }
    for table, columns in expected.items():
        assert {item["name"] for item in database.get_columns(table)} == columns
    typed_columns = {
        table: {column["name"]: column["type"] for column in database.get_columns(table)}
        for table in ("weather", "articles", "wechat_drafts")
    }
    assert isinstance(typed_columns["weather"]["adcode"], CHAR)
    assert isinstance(typed_columns["articles"]["content_fingerprint"], CHAR)
    assert isinstance(typed_columns["articles"]["cover_sha256"], CHAR)
    assert isinstance(
        typed_columns["wechat_drafts"]["publication_fingerprint"], CHAR
    )
    assert "preview_matches_game_id_seq" not in database.get_sequence_names()
    match_indexes = {item["name"] for item in database.get_indexes("preview_matches")}
    assert {
        "ix_preview_matches_batch_active_kickoff",
        "ix_preview_matches_open_tasks",
        "ix_preview_matches_claimed",
    } <= match_indexes


def test_username_is_case_sensitive_and_database_rejects_whitespace(
    session_factory,
) -> None:
    with session_factory.begin() as session:
        session.add_all(
            [
                User(
                    username="Admin",
                    display_name="A",
                    password_hash="hash",
                    role="admin",
                ),
                User(
                    username="admin",
                    display_name="B",
                    password_hash="hash",
                    role="admin",
                ),
            ]
        )
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                User(
                    username="bad name",
                    display_name="C",
                    password_hash="hash",
                    role="admin",
                )
            )


def test_incomplete_render_uses_placeholder_and_reuses_current_article(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        batch = _batch(session, complete=False)
        first, reused = render_batch(session, settings, batch)
        assert reused is False
        assert first.is_complete is False
        assert "matches" in first.missing_fields
        assert first.cover_kind == "media_id"
        assert first.cover_sha256 == hashlib.sha256(
            b"website-default-cover"
        ).hexdigest()
        assert "待补充比赛与前瞻内容" in first.body_html
        second, reused = render_batch(session, settings, batch)
        assert reused is True
        assert second.id == first.id
        set_batch_cover_media_id(batch, "another-cover")
        assert batch.current_article_id is None


class _FakeWechat:
    def __init__(self) -> None:
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def create_draft(self, articles):
        self.calls.append(tuple(articles))
        return DraftReceipt(
            media_id=f"draft-{len(self.calls)}",
            content_fingerprint="fake",
            created_at=datetime.now(UTC),
        )


def test_default_media_id_draft_is_ordered_and_idempotent(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    fake = _FakeWechat()
    factories = ExternalFactories(wechat=lambda: fake)
    with session_factory.begin() as session:
        first_batch = _batch(session, complete=True, game_id=9101)
        second_batch = _batch(
            session,
            target_date=date(2026, 8, 9),
            competition="male",
            complete=True,
            game_id=9102,
        )
        first, _ = render_batch(session, settings, first_batch)
        second, _ = render_batch(session, settings, second_batch)
        preview = asyncio.run(
            create_wechat_draft(
                session,
                settings,
                factories,
                [second.id, first.id],
                confirm=False,
            )
        )
        assert preview["status"] == "ready"
        assert fake.calls == []
        created = asyncio.run(
            create_wechat_draft(
                session,
                settings,
                factories,
                [second.id, first.id],
                confirm=True,
            )
        )
        assert created["status"] == "created"
        assert [article.title for article in fake.calls[0]] == [
            second.title,
            first.title,
        ]
        assert all(
            article.cover == CoverMediaId("website-default-cover")
            for article in fake.calls[0]
        )
        reused = asyncio.run(
            create_wechat_draft(
                session,
                settings,
                factories,
                [second.id, first.id],
                confirm=True,
            )
        )
        assert reused["status"] == "reused"
        assert len(fake.calls) == 1
        assert session.query(WechatDraft).count() == 1
        assert batch_status(session, first_batch) == "drafted"


def test_article_media_id_sha_is_checked(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        batch = _batch(session, complete=True, game_id=9201)
        article, _ = render_batch(session, settings, batch)
        article.cover_storage_key = "changed-after-render"
        with pytest.raises(WorkflowError, match="fingerprint"):
            article_domain(settings, article)


def test_batch_exposes_latest_stale_article_and_preview_omits_referrer(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        session.add(
            User(
                username="PreviewAdmin",
                display_name="Preview Admin",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
        batch = _batch(session, complete=True, game_id=9211)
        article, _ = render_batch(session, settings, batch)
        article_id = article.id
        batch_id = batch.id
        set_batch_cover_media_id(batch, "new-cover")
        never_rendered = _batch(
            session,
            target_date=date(2026, 8, 10),
            competition="male",
            complete=False,
        )
        never_rendered_id = never_rendered.id

    app = create_app(settings=settings, session_factory=session_factory)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "PreviewAdmin", "password": "password-123"},
        ).raise_for_status()

        stale = client.get(f"/api/preview-batches/{batch_id}")
        stale.raise_for_status()
        assert stale.json()["current_article_id"] is None
        assert stale.json()["latest_article_id"] == article_id

        empty = client.get(f"/api/preview-batches/{never_rendered_id}")
        empty.raise_for_status()
        assert empty.json()["current_article_id"] is None
        assert empty.json()["latest_article_id"] is None

        preview = client.get(f"/api/articles/{article_id}/preview")
        preview.raise_for_status()
        assert preview.headers["referrer-policy"] == "no-referrer"

        draft = client.post(
            "/api/wechat-drafts",
            json={"article_ids": [article_id], "confirm": False},
        )
        assert draft.status_code == 409
        assert draft.json()["error"]["code"] == "article_stale"


def test_match_payload_fills_legacy_result_text_without_mutating_snapshot(
    session_factory,
) -> None:
    with session_factory.begin() as session:
        _batch(session, complete=True, game_id=9221)
        match = session.get(PreviewMatch, 9221)
        assert match is not None
        legacy_result = {
            "game_id": 8001,
            "home": {"team_id": 1, "name": "主队", "short_name": "主队"},
            "away": {"team_id": 2, "name": "客队", "short_name": "客队"},
            "home_score": 1,
            "away_score": 1,
            "home_penalty": 5,
            "away_penalty": 4,
        }
        match.home_snapshot = {
            **match.home_snapshot,
            "current_results": [legacy_result],
        }

        payload = match_payload(match)

        assert payload["home"]["current_results"][0]["result_text"] == "1(5):1(4)"
        assert "result_text" not in legacy_result


def test_uploaded_cover_is_content_addressed_and_checked(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    stream = BytesIO()
    Image.new("RGB", (20, 20), "green").save(stream, format="PNG")
    with session_factory.begin() as session:
        batch = _batch(session, complete=True, game_id=9251)
        save_batch_cover(session, settings, batch, stream.getvalue())
        assert batch.cover_kind == "file"
        assert batch.cover_storage_key.startswith("covers/")
        batch_id = batch.id
    with session_factory.begin() as session:
        batch = session.get(PreviewBatch, batch_id)
        assert batch is not None
        article, _ = render_batch(session, settings, batch)
        assert article_domain(settings, article).cover.path.is_file()
        set_batch_cover_media_id(batch, "new-cover")
        assert article_domain(settings, article).cover.path.is_file()
        path = resolve_storage_key(settings.artifact_root, article.cover_storage_key)
        path.write_bytes(b"changed")
        with pytest.raises(WorkflowError, match="fingerprint"):
            article_domain(settings, article)


def test_identical_weather_keeps_render_and_actual_change_invalidates(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        batch = _batch(session, complete=True, game_id=9261)
        article, _ = render_batch(session, settings, batch)
        set_manual_weather(
            session,
            batch.preview_date,
            condition="晴",
            low_c=20,
            high_c=30,
            wind_direction="南风",
            wind_level="2级",
        )
        assert batch.current_article_id == article.id
        set_manual_weather(
            session,
            batch.preview_date,
            condition="多云",
            low_c=20,
            high_c=30,
            wind_direction="南风",
            wind_level="2级",
        )
        session.refresh(batch)
        assert batch.current_article_id is None


def test_match_move_and_recovery_preserve_manual_content_and_claim(
    session_factory,
) -> None:
    with session_factory.begin() as session:
        writer = User(
            username="user",
            display_name="Writer",
            password_hash="hash",
            role="user",
        )
        session.add(writer)
        source_batch = _batch(session, complete=True, game_id=9271)
        target_batch = PreviewBatch(
            preview_date=date(2026, 8, 10),
            competition="female",
            cover_kind="media_id",
            cover_storage_key="website-default-cover",
            cover_content_type=None,
        )
        session.add(target_batch)
        session.flush()
        record = session.get(PreviewMatch, 9271)
        assert record is not None
        record.task_open = True
        record.claimed_by_user_id = writer.id
        source = assemble_source(session, source_batch)
        moved_match = replace(
            source.matches[0],
            kickoff=datetime(2026, 8, 10, 15, tzinfo=SHANGHAI),
        )
        moved_source = replace(
            source,
            preview_date=target_batch.preview_date,
            matches=(moved_match,),
        )
        game = SimpleNamespace(
            tournament_id=record.tournament_id,
            tournament_name=record.tournament_name,
            kickoff_local=moved_match.kickoff,
        )
        upsert_source(session, target_batch, moved_source, {9271: game})
        assert record.batch_id == target_batch.id
        assert record.writers == ["作者"]
        assert record.body == "第一段。\n\n第二段。"
        assert record.claimed_by_user_id == writer.id
        assert record.task_open is False
        record.active = False
        upsert_source(session, target_batch, moved_source, {9271: game})
        assert record.active is True


def test_login_case_sensitivity_and_body_version_conflict(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        session.add(
            User(
                username="Admin",
                display_name="管理员",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
        _batch(session, complete=True, game_id=9301)
    app = create_app(settings=settings, session_factory=session_factory)
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "password-123"},
        ).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"username": "bad name", "password": "password-123"},
        ).status_code == 422
        assert client.post(
            "/api/auth/login",
            json={"username": "Admin", "password": "password-123"},
        ).status_code == 200
        updated = client.patch(
            "/api/preview-matches/9301",
            json={"expected_version": 0, "body": "新正文"},
        )
        assert updated.status_code == 200
        assert updated.json()["body_version"] == 1
        conflict = client.patch(
            "/api/preview-matches/9301",
            json={"expected_version": 0, "body": "覆盖正文"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "body_version_conflict"
        too_many = client.post(
            "/api/wechat-drafts",
            json={"article_ids": list(range(1, 10)), "confirm": False},
        )
        assert too_many.status_code == 422


def test_batch_list_uses_descending_dates_and_business_competition_order(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        session.add(
            User(
                username="OrderAdmin",
                display_name="Order Admin",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
        for target_date in (date(2026, 8, 19), date(2026, 8, 20)):
            for competition in ("futsal", "female", "male"):
                session.add(
                    PreviewBatch(
                        preview_date=target_date,
                        competition=competition,
                        cover_kind="media_id",
                        cover_storage_key="website-default-cover",
                        cover_content_type=None,
                    )
                )
    app = create_app(settings=settings, session_factory=session_factory)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "OrderAdmin", "password": "password-123"},
        ).raise_for_status()
        response = client.get("/api/preview-batches")
        response.raise_for_status()
        assert [
            (item["preview_date"], item["competition"])
            for item in response.json()["items"]
        ] == [
            ("2026-08-20", "male"),
            ("2026-08-20", "female"),
            ("2026-08-20", "futsal"),
            ("2026-08-19", "male"),
            ("2026-08-19", "female"),
            ("2026-08-19", "futsal"),
        ]


def test_persist_credentials_preserves_other_dotenv_content(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_bytes(
        b"# keep this comment\r\n"
        b"THUFOOTBALL_OPENID=old-openid\r\n"
        b"OTHER_SETTING=keep-me\r\n"
        b"THUFOOTBALL_SESSION_KEY=old-session\r\n"
    )

    persist_credentials(env_path, "new-openid", "new-session")

    content = env_path.read_bytes().decode("utf-8")
    assert "# keep this comment\r\n" in content
    assert "OTHER_SETTING=keep-me\r\n" in content
    assert "THUFOOTBALL_OPENID=new-openid\r\n" in content
    assert "THUFOOTBALL_SESSION_KEY=new-session\r\n" in content
    assert "old-openid" not in content
    assert "old-session" not in content


class _FakeCredentialClient:
    user_registered = True
    calls: list[str] = []

    def __init__(
        self,
        *,
        openid: str,
        session_key: str,
        load_environment: bool,
    ) -> None:
        assert load_environment is False
        self.openid = openid
        self.session_key = session_key

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        self.calls.append("closed")

    async def get_user_info(self) -> UserProbe:
        self.calls.append("get_user_info")
        return UserProbe(user_registered=self.user_registered)


def test_credential_api_validates_masks_and_updates_runtime(
    session_factory,
    settings: WebsiteSettings,
    tmp_path: Path,
    monkeypatch,
) -> None:
    with session_factory.begin() as session:
        session.add(
            User(
                username="CredentialAdmin",
                display_name="Credential Admin",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# credentials\n"
        "THUFOOTBALL_OPENID=old-openid\n"
        "THUFOOTBALL_SESSION_KEY=old-session\n"
        "OTHER_SETTING=keep-me\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THUFOOTBALL_OPENID", "old-openid")
    monkeypatch.setenv("THUFOOTBALL_SESSION_KEY", "old-session")
    _FakeCredentialClient.calls = []
    _FakeCredentialClient.user_registered = True
    monkeypatch.setattr(backend_api, "THUFootballClient", _FakeCredentialClient)
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        credential_env_path=env_path,
    )

    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "CredentialAdmin", "password": "password-123"},
        ).raise_for_status()
        status = client.get("/api/settings/thufootball-credentials")
        status.raise_for_status()
        assert status.json() == {
            "configured": True,
            "openid_masked": "********enid",
            "session_key_masked": "********sion",
        }

        updated = client.put(
            "/api/settings/thufootball-credentials",
            json={"openid": "new-openid", "session_key": "new-session"},
        )
        updated.raise_for_status()
        assert _FakeCredentialClient.calls == ["get_user_info", "closed"]
        assert "new-openid" not in updated.text
        assert "new-session" not in updated.text
        assert os.environ["THUFOOTBALL_OPENID"] == "new-openid"
        assert os.environ["THUFOOTBALL_SESSION_KEY"] == "new-session"
        assert "OTHER_SETTING=keep-me" in env_path.read_text(encoding="utf-8")

        _FakeCredentialClient.user_registered = False
        rejected = client.put(
            "/api/settings/thufootball-credentials",
            json={"openid": "bad-openid", "session_key": "bad-session"},
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == (
            "invalid_thufootball_credentials"
        )
        assert "bad-openid" not in env_path.read_text(encoding="utf-8")
        assert os.environ["THUFOOTBALL_OPENID"] == "new-openid"


def test_query_factory_holds_credential_lock_until_client_closes(
    settings: WebsiteSettings,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    @asynccontextmanager
    async def query_factory():
        events.append("client_opened")
        entered.set()
        await release.wait()
        try:
            yield object()
        finally:
            events.append("client_closed")

    app = create_app(
        settings=settings,
        session_factory=lambda: None,
        external_factories=ExternalFactories(queries=query_factory),
    )

    async def scenario() -> None:
        async def run_query() -> None:
            async with app.state.external_factories.queries():
                events.append("query_completed")

        async def update_credentials() -> None:
            async with app.state.thufootball_lock:
                events.append("credentials_updated")

        query_task = asyncio.create_task(run_query())
        await entered.wait()
        update_task = asyncio.create_task(update_credentials())
        await asyncio.sleep(0)
        assert not update_task.done()
        release.set()
        await query_task
        await update_task

    asyncio.run(scenario())
    assert events == [
        "client_opened",
        "query_completed",
        "client_closed",
        "credentials_updated",
    ]


class _FailingExternalService:
    def __init__(self, calls: list[str], name: str) -> None:
        self.calls = calls
        self.name = name

    async def __aenter__(self):
        self.calls.append(self.name)
        raise RuntimeError(f"{self.name} failed")

    async def __aexit__(self, *args: object) -> None:
        return None


def test_create_reuses_without_query_and_only_all_query_failures_return_502(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    existing_date = date(2026, 9, 1)
    failed_date = date(2026, 9, 2)
    with session_factory.begin() as session:
        session.add(
            User(
                username="CreateAdmin",
                display_name="Create Admin",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
        existing = _batch(
            session,
            target_date=existing_date,
            competition="female",
            complete=False,
        )
        existing_id = existing.id

    calls: list[str] = []
    factories = ExternalFactories(
        queries=lambda: _FailingExternalService(calls, "queries"),
        weather=lambda: _FailingExternalService(calls, "weather"),
    )
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        external_factories=factories,
    )
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "CreateAdmin", "password": "password-123"},
        ).raise_for_status()

        reused = client.post(
            "/api/preview-batches/create",
            json={
                "dates": [existing_date.isoformat()],
                "competitions": ["female"],
            },
        )
        reused.raise_for_status()
        assert reused.json() == {
            "results": [
                {
                    "date": existing_date.isoformat(),
                    "competition": "female",
                    "status": "reused",
                    "batch_id": existing_id,
                }
            ]
        }
        assert calls == []

        partial = client.post(
            "/api/preview-batches/create",
            json={
                "dates": [existing_date.isoformat()],
                "competitions": ["male", "female"],
            },
        )
        partial.raise_for_status()
        assert [item["status"] for item in partial.json()["results"]] == [
            "failed",
            "reused",
        ]

        failed = client.post(
            "/api/preview-batches/create",
            json={
                "dates": [failed_date.isoformat()],
                "competitions": ["male"],
            },
        )
        assert failed.status_code == 502
        error = failed.json()["error"]
        assert error["code"] == "preview_batch_queries_failed"
        assert error["details"]["results"][0]["status"] == "failed"
        assert error["details"]["results"][0]["error"]["code"] == "query_failed"


def test_task_endpoints_toggle_all_active_matches_without_request_body(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        session.add(
            User(
                username="TaskAdmin",
                display_name="Task Admin",
                password_hash=hash_password("password-123"),
                role="admin",
            )
        )
        batch = _batch(
            session,
            target_date=date(2026, 9, 3),
            competition="male",
            complete=True,
            game_id=9401,
        )
        batch_id = batch.id

    app = create_app(settings=settings, session_factory=session_factory)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "TaskAdmin", "password": "password-123"},
        ).raise_for_status()
        opened = client.post(f"/api/preview-batches/{batch_id}/open-tasks")
        opened.raise_for_status()
        assert opened.json() == {"game_ids": [9401], "task_open": True}

        listed = client.get("/api/preview-matches")
        listed.raise_for_status()
        assert len(listed.json()["items"]) == 1
        item = listed.json()["items"][0]
        assert item["game_id"] == 9401
        assert item["batch_id"] == batch_id
        assert item["preview_date"] == "2026-09-03"
        assert item["competition"] == "male"
        assert item["active"] is True
        assert item["task_open"] is True

        closed = client.post(f"/api/preview-batches/{batch_id}/close-tasks")
        closed.raise_for_status()
        assert closed.json() == {"game_ids": [9401], "task_open": False}
        assert client.get("/api/preview-matches").json() == {"items": []}

    with session_factory() as session:
        match = session.get(PreviewMatch, 9401)
        assert match is not None
        assert match.task_open is False


def test_stage4_registration_profile_user_queries_and_session_invalidation(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        admin = User(
            username="Stage4Admin",
            display_name="Stage 4 Admin",
            password_hash=hash_password("admin-password"),
            role="admin",
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id

    app = create_app(settings=settings, session_factory=session_factory)
    with (
        TestClient(app) as admin_client,
        TestClient(app) as user_client,
        TestClient(app) as old_session,
    ):
        admin_client.post(
            "/api/auth/login",
            json={"username": "Stage4Admin", "password": "admin-password"},
        ).raise_for_status()
        registered = user_client.post(
            "/api/auth/register",
            json={
                "username": "Stage4User",
                "display_name": "普通用户",
                "password": "user-password",
            },
        )
        assert registered.status_code == 201
        assert registered.json()["role"] == "user"
        user_id = registered.json()["id"]
        assert user_client.get("/api/auth/me").status_code == 200
        assert user_client.get("/api/admin/users").status_code == 403
        assert user_client.get(f"/api/admin/users/{admin_id}").json() == {
            "id": admin_id,
            "display_name": "Stage 4 Admin",
        }

        profile = user_client.patch(
            "/api/auth/me", json={"display_name": "  新显示名称  "}
        )
        profile.raise_for_status()
        assert profile.json()["display_name"] == "新显示名称"
        assert user_client.get(f"/api/admin/users/{user_id}").json() == {
            "id": user_id,
            "display_name": "新显示名称",
        }

        old_session.post(
            "/api/auth/login",
            json={"username": "Stage4User", "password": "user-password"},
        ).raise_for_status()
        changed = user_client.post(
            "/api/auth/change-password",
            json={
                "current_password": "user-password",
                "new_password": "changed-password",
            },
        )
        assert changed.status_code == 204
        assert user_client.get("/api/auth/me").status_code == 200
        assert old_session.get("/api/auth/me").status_code == 401

        users = admin_client.get("/api/admin/users")
        users.raise_for_status()
        managed = next(item for item in users.json()["items"] if item["id"] == user_id)
        assert managed["username"] == "Stage4User"
        assert managed["claimed_task_count"] == 0
        assert "password_hash" not in managed
        assert "auth_version" not in managed

        reset = admin_client.post(
            f"/api/admin/users/{user_id}/reset-password",
            json={"new_password": "reset-password"},
        )
        assert reset.status_code == 204
        assert user_client.get("/api/auth/me").status_code == 401


def test_stage4_task_claim_content_release_assign_and_read_permissions(
    session_factory,
    settings: WebsiteSettings,
) -> None:
    with session_factory.begin() as session:
        admin = User(
            username="CollabAdmin",
            display_name="协作管理员",
            password_hash=hash_password("admin-password"),
            role="admin",
        )
        first = User(
            username="CollabUser1",
            display_name="用户甲",
            password_hash=hash_password("first-password"),
            role="user",
        )
        second = User(
            username="CollabUser2",
            display_name="用户乙",
            password_hash=hash_password("second-password"),
            role="user",
        )
        session.add_all([admin, first, second])
        session.flush()
        first_id = first.id
        second_id = second.id
        batch = _batch(
            session,
            target_date=date(2026, 9, 4),
            competition="futsal",
            complete=True,
            game_id=9501,
        )
        batch_id = batch.id

    app = create_app(settings=settings, session_factory=session_factory)
    with (
        TestClient(app) as admin_client,
        TestClient(app) as first_client,
        TestClient(app) as second_client,
    ):
        admin_client.post(
            "/api/auth/login",
            json={"username": "CollabAdmin", "password": "admin-password"},
        ).raise_for_status()
        first_client.post(
            "/api/auth/login",
            json={"username": "CollabUser1", "password": "first-password"},
        ).raise_for_status()
        second_client.post(
            "/api/auth/login",
            json={"username": "CollabUser2", "password": "second-password"},
        ).raise_for_status()

        rendered = admin_client.post(f"/api/preview-batches/{batch_id}/render")
        rendered.raise_for_status()
        article_id = rendered.json()["article"]["id"]
        assert first_client.get("/api/preview-batches").status_code == 200
        assert first_client.get(f"/api/preview-batches/{batch_id}").status_code == 200
        assert first_client.get(f"/api/articles/{article_id}").status_code == 200
        assert first_client.get(f"/api/articles/{article_id}/preview").status_code == 200
        assert first_client.post(f"/api/preview-batches/{batch_id}/render").status_code == 403

        admin_client.post(f"/api/preview-batches/{batch_id}/open-tasks").raise_for_status()
        waiting = first_client.get("/api/tasks/wait_claim")
        waiting.raise_for_status()
        waiting_item = waiting.json()["items"][0]
        assert waiting_item["batch_id"] == batch_id
        assert waiting_item["competition"] == "futsal"
        assert "current_article_id" not in waiting_item
        assert "preview_date" not in waiting_item
        assert second_client.get("/api/tasks/open").status_code == 403
        assert len(admin_client.get("/api/tasks/open").json()["items"]) == 1

        claimed = first_client.post("/api/preview-matches/9501/claim")
        claimed.raise_for_status()
        assert claimed.json()["reused"] is False
        assert claimed.json()["match"]["claimed_by_user_id"] == first_id
        assert claimed.json()["match"]["writers"] == ["用户甲"]
        assert first_client.get("/api/tasks/wait_claim").json() == {"items": []}
        assert len(first_client.get("/api/me/tasks").json()["items"]) == 1
        assert second_client.post("/api/preview-matches/9501/claim").status_code == 409

        version = claimed.json()["match"]["body_version"]
        assert second_client.patch(
            "/api/preview-matches/9501/body",
            json={"expected_version": version, "body": "越权正文"},
        ).status_code == 403
        with session_factory.begin() as session:
            match = session.get(PreviewMatch, 9501)
            match.active = False
            match.task_open = False
        saved = first_client.patch(
            "/api/preview-matches/9501/body",
            json={"expected_version": version, "body": "失效后仍可保存"},
        )
        saved.raise_for_status()
        assert saved.json()["body"] == "失效后仍可保存"
        assert len(first_client.get("/api/me/tasks").json()["items"]) == 1
        assert second_client.post("/api/preview-matches/9501/release").status_code == 403

        released = admin_client.post("/api/preview-matches/9501/release")
        released.raise_for_status()
        assert released.json()["claimed_by_user_id"] is None
        assert released.json()["writers"] == []
        assert released.json()["body"] == "失效后仍可保存"
        assigned = admin_client.post(
            "/api/preview-matches/9501/assign", json={"user_id": second_id}
        )
        assigned.raise_for_status()
        assert assigned.json()["claimed_by_user_id"] == second_id
        assert assigned.json()["writers"] == ["用户乙"]
        assert assigned.json()["body"] == "失效后仍可保存"

        users = admin_client.get("/api/admin/users").json()["items"]
        managed = next(item for item in users if item["id"] == second_id)
        assert managed["claimed_task_count"] == 1
        detail = first_client.get(f"/api/preview-batches/{batch_id}").json()
        assert detail["current_article_id"] is None


def test_stage4_concurrent_claim_has_one_winner(
    postgres_engine,
    settings: WebsiteSettings,
) -> None:
    direct_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    target_date = date(2099, 12, 31)
    game_id = 9599_000_000 + int(suffix, 16)
    with direct_factory.begin() as session:
        first = User(
            username=f"ConcurrentA{suffix}",
            display_name="并发甲",
            password_hash=hash_password("first-password"),
            role="user",
        )
        second = User(
            username=f"ConcurrentB{suffix}",
            display_name="并发乙",
            password_hash=hash_password("second-password"),
            role="user",
        )
        session.add_all([first, second])
        session.flush()
        user_ids = (first.id, second.id)
        batch = _batch(
            session,
            target_date=target_date,
            competition="male",
            complete=True,
            game_id=game_id,
        )
        batch_id = batch.id
        match = session.get(PreviewMatch, game_id)
        match.task_open = True
        match.writers = []
        match.body = ""

    app = create_app(settings=settings, session_factory=direct_factory)
    gate = Barrier(2)
    try:
        with TestClient(app) as first_client, TestClient(app) as second_client:
            first_client.post(
                "/api/auth/login",
                json={
                    "username": f"ConcurrentA{suffix}",
                    "password": "first-password",
                },
            ).raise_for_status()
            second_client.post(
                "/api/auth/login",
                json={
                    "username": f"ConcurrentB{suffix}",
                    "password": "second-password",
                },
            ).raise_for_status()

            def claim(client: TestClient):
                gate.wait()
                return client.post(f"/api/preview-matches/{game_id}/claim")

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(claim, (first_client, second_client))
                )
            assert sorted(response.status_code for response in responses) == [200, 409]
        with direct_factory() as session:
            match = session.get(PreviewMatch, game_id)
            assert match.claimed_by_user_id in user_ids
            assert match.writers in (["并发甲"], ["并发乙"])
            assert match.body_version == 1
    finally:
        with direct_factory.begin() as session:
            session.execute(delete(PreviewMatch).where(PreviewMatch.game_id == game_id))
            session.execute(delete(PreviewBatch).where(PreviewBatch.id == batch_id))
            session.execute(delete(Weather).where(Weather.date == target_date))
            session.execute(delete(User).where(User.id.in_(user_ids)))


def test_built_frontend_is_mounted_after_api_routes(
    session_factory,
    settings: WebsiteSettings,
    tmp_path: Path,
) -> None:
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        '<!doctype html><div id="root">frontend</div>',
        encoding="utf-8",
    )
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        frontend_dist=frontend_dist,
    )

    with TestClient(app) as client:
        assert "frontend" in client.get("/").text
        assert client.get("/api/auth/me").status_code == 401
        assert "Swagger UI" in client.get("/docs").text
