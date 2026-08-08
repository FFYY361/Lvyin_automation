"""Repeatable Stage 6 API smoke test against real PostgreSQL."""

from __future__ import annotations

import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.api import create_app
from backend.auth import hash_password
from backend.config import WebsiteSettings
from backend.database import create_session_factory
from backend.models import Base, Batch, Match, User, Weather
from backend.workflow import ExternalFactories
from wechat_official import CoverMediaId, DraftReceipt

SHANGHAI = timezone(timedelta(hours=8))


class _FakeWechat:
    def __init__(self) -> None:
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def create_draft(self, articles):
        resolved = tuple(articles)
        assert len(resolved) == 1
        assert isinstance(resolved[0].cover, CoverMediaId)
        self.calls += 1
        return DraftReceipt(
            media_id="smoke-draft-media-id",
            content_fingerprint="smoke",
            created_at=datetime.now(UTC),
        )


def _seed(factory, default_cover_media_id: str) -> tuple[int, int]:
    target_date = date(2026, 8, 8)
    with factory.begin() as session:
        user = User(
            username="smoke-admin",
            display_name="Smoke Admin",
            password_hash=hash_password("smoke-password"),
            role="admin",
        )
        session.add(user)
        batch = Batch(
            batch_date=target_date,
            competition="female",
            headline="API smoke test",
            editors=["Editor"],
            reviewers=["Reviewer"],
            approvers=["Approver"],
            cover_kind="media_id",
            cover_storage_key=default_cover_media_id,
            cover_content_type=None,
        )
        session.add(batch)
        session.flush()
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
            Match(
                game_id=990000001,
                batch_id=batch.id,
                tournament_id=123,
                tournament_name="Smoke Tournament",
                competition_name="女足",
                stage="测试",
                kickoff=datetime(2026, 8, 8, 15, tzinfo=SHANGHAI),
                venue="紫荆操场",
                home_snapshot={
                    "team_id": 990001,
                    "name": "主队",
                    "short_name": "主队",
                    "previous_outcomes": [],
                    "current_results": [],
                },
                away_snapshot={
                    "team_id": 990002,
                    "name": "客队",
                    "short_name": "客队",
                    "previous_outcomes": [],
                    "current_results": [],
                },
                head_to_head_snapshot=[],
                writers=["Writer"],
                body="Smoke body.",
            )
        )
        session.flush()
        return user.id, batch.id


def main() -> int:
    settings = WebsiteSettings.from_environment()
    administration = create_engine(settings.database_url)
    schema = f"website_smoke_{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="lvyin-website-smoke-") as directory:
        smoke_settings = replace(
            settings,
            artifact_root=Path(directory) / "artifacts",
            cookie_name="smoke_session",
        )
        try:
            with administration.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_engine(
                settings.database_url,
                connect_args={"options": f"-csearch_path={schema}"},
            )
            Base.metadata.create_all(engine)
            factory = create_session_factory(engine)
            _, batch_id = _seed(factory, settings.default_cover_media_id)
            fake_wechat = _FakeWechat()
            app = create_app(
                settings=smoke_settings,
                session_factory=factory,
                external_factories=ExternalFactories(wechat=lambda: fake_wechat),
            )
            with TestClient(app) as client, TestClient(app) as user_client:
                login = client.post(
                    "/api/auth/login",
                    json={
                        "username": "smoke-admin",
                        "password": "smoke-password",
                    },
                )
                login.raise_for_status()
                registered = user_client.post(
                    "/api/auth/register",
                    json={
                        "username": "smoke-user",
                        "display_name": "Smoke User",
                        "password": "smoke-user-password",
                        "invite_code": settings.invite_code,
                    },
                )
                registered.raise_for_status()
                client.post(
                    f"/api/batches/{batch_id}/open-tasks"
                ).raise_for_status()
                waiting = user_client.get("/api/tasks/wait_claim")
                waiting.raise_for_status()
                assert len(waiting.json()["items"]) == 1
                claim = user_client.post("/api/matches/990000001/claim")
                claim.raise_for_status()
                version = claim.json()["match"]["body_version"]
                saved = user_client.patch(
                    "/api/matches/990000001/body",
                    json={
                        "expected_version": version,
                        "body": "Stage 6 smoke body.",
                    },
                )
                saved.raise_for_status()
                assert len(user_client.get("/api/me/tasks").json()["items"]) == 1
                assert user_client.get("/api/batches").status_code == 200
                assert (
                    user_client.get(f"/api/batches/{batch_id}").status_code
                    == 200
                )
                rendered = client.post(f"/api/batches/{batch_id}/render-preview")
                rendered.raise_for_status()
                article_id = rendered.json()["article"]["id"]
                preview = user_client.get(f"/api/articles/{article_id}/preview")
                preview.raise_for_status()
                dry_run = client.post(
                    "/api/wechat-drafts",
                    json={"article_ids": [article_id], "confirm": False},
                )
                dry_run.raise_for_status()
                assert dry_run.json()["status"] == "ready"
                confirmed = client.post(
                    "/api/wechat-drafts",
                    json={"article_ids": [article_id], "confirm": True},
                )
                confirmed.raise_for_status()
                assert confirmed.json()["status"] == "created"
                assert fake_wechat.calls == 1
                batch = client.get(f"/api/batches/{batch_id}")
                batch.raise_for_status()
                assert batch.json()["preview_status"] == "drafted"
            print("Stage 6 PostgreSQL API smoke test passed")
            return 0
        finally:
            if "engine" in locals():
                engine.dispose()
            with administration.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            administration.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
