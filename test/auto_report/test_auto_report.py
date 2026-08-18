from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import wraps
from pathlib import Path
from typing import Any

import pytest

from auto_report import (
    AutoReportPipeline,
    Competition,
    PipelineRequest,
    Stage,
)
from auto_report.cli import _parser
from auto_report.errors import ArtifactValidationError, PipelineError
from auto_report.service import (
    DEFAULT_REPORT_COVER_MEDIA_ID,
    _report_warning_message,
)
from thufootball import (
    GameDetail,
    GameEventIssue,
    GameQuery,
    GameReportFile,
    GameStatus,
    GameSummary,
    PreparedGameReport,
    prepare_game_report,
)
from wechat_official import Article, CoverFile, CoverMediaId, DraftReceipt


def _async_test(function: Any) -> Any:
    @wraps(function)
    def run(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))

    return run


def _game(
    game_id: int,
    kickoff: datetime,
    *,
    tournament_id: int = 122,
    status: GameStatus = GameStatus.FINISHED,
    home_abandon: bool | None = None,
    away_abandon: bool | None = None,
    home_name: str = "A",
    away_name: str = "B",
) -> GameSummary:
    return GameSummary(
        game_id=game_id,
        tournament_id=tournament_id,
        tournament_name="Tournament",
        kickoff_utc=kickoff,
        kickoff_local=kickoff,
        status=status,
        record_active=True,
        valid=True,
        stage=None,
        group_name=None,
        round=None,
        home_tournament_team_id=game_id * 2,
        home_team_id=90_000 + game_id * 2,
        home_team_name=home_name,
        away_tournament_team_id=game_id * 2 + 1,
        away_team_id=90_000 + game_id * 2 + 1,
        away_team_name=away_name,
        home_score=1 if status is GameStatus.FINISHED else None,
        away_score=0 if status is GameStatus.FINISHED else None,
        result_text="1:0" if status is GameStatus.FINISHED else None,
        penalty_shootout=False,
        home_penalty=None,
        away_penalty=None,
        home_abandon=home_abandon,
        away_abandon=away_abandon,
        field_name="Field",
    )


class FakeFootball:
    def __init__(
        self,
        games_by_tournaments: dict[tuple[int, ...], list[GameSummary]],
        *,
        warning_game_ids: set[int] | None = None,
        mixed_game_ids: set[int] | None = None,
    ) -> None:
        self.games_by_tournaments = games_by_tournaments
        self.warning_game_ids = warning_game_ids or set()
        self.mixed_game_ids = mixed_game_ids or set()
        self.queries: list[GameQuery] = []
        self.reports: list[dict[str, object]] = []

    async def __aenter__(self) -> "FakeFootball":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        self.queries.append(query)
        return list(self.games_by_tournaments.get(query.tournament_ids, []))

    def _find_game(self, game_id: int) -> GameSummary:
        return next(
            game
            for games in self.games_by_tournaments.values()
            for game in games
            if game.game_id == game_id
        )

    async def get_prepared_game_report(self, game_id: int) -> PreparedGameReport:
        game = self._find_game(game_id)
        detail = GameDetail(
            game=game,
            events=(),
            referees=(),
            players_per_side=5 if game.tournament_id == 128 else 11,
        )
        if game_id in self.mixed_game_ids:
            rendered = replace(
                detail,
                game=replace(
                    game,
                    home_score=3,
                    away_score=0,
                    result_text="3:0",
                    home_abandon=False,
                    away_abandon=False,
                ),
            )
            return PreparedGameReport(
                source_detail=detail,
                detail=rendered,
                warnings=(
                    GameEventIssue(
                        severity="warning",
                        code="abandon_with_events_awarded_loss",
                        message="B弃赛但仍有事件，视为B被判负",
                        side="away",
                    ),
                ),
                render_image=True,
                text="AvsB的比赛，由于B被判负，记为A 3:0 B。",
            )
        if game.home_abandon is True or game.away_abandon is True:
            return prepare_game_report(detail)
        warnings = (
            (
                GameEventIssue(
                    severity="warning",
                    code="lineup_under_capacity",
                    message="under capacity",
                    side="home",
                ),
            )
            if game_id in self.warning_game_ids
            else ()
        )
        return PreparedGameReport(
            source_detail=detail,
            detail=detail,
            warnings=warnings,
            render_image=True,
            text=None,
        )

    async def render_game_detail(
        self, detail: GameDetail, *, settings: object
    ) -> tuple[bytes, int, int]:
        self.reports.append(
            {
                "game_id": detail.game.game_id,
                "settings": settings,
                "refresh_stats": False,
            }
        )
        return b"\x89PNG\r\n" + str(detail.game.game_id).encode(), 1600, 900

    async def download_game_report(
        self,
        game_id: int,
        output: Path,
        *,
        settings: object,
        refresh_stats: bool,
        overwrite: bool,
    ) -> GameReportFile:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x89PNG\r\n" + str(game_id).encode())
        self.reports.append(
            {
                "game_id": game_id,
                "output": output,
                "settings": settings,
                "refresh_stats": refresh_stats,
                "overwrite": overwrite,
            }
        )
        warnings = (
            (
                GameEventIssue(
                    severity="warning",
                    code="lineup_under_capacity",
                    message="under capacity",
                    side="home",
                ),
            )
            if game_id in self.warning_game_ids
            else ()
        )
        return GameReportFile(
            game_id=game_id,
            path=str(output),
            media_type="image/png",
            width=1600,
            height=900,
            refreshed_stats=False,
            warnings=warnings,
        )


class FailingFootball(FakeFootball):
    def __init__(
        self,
        games_by_tournaments: dict[tuple[int, ...], list[GameSummary]],
        *,
        fail_game_id: int,
    ) -> None:
        super().__init__(games_by_tournaments)
        self.fail_game_id = fail_game_id

    async def get_prepared_game_report(self, game_id: int) -> PreparedGameReport:
        if game_id == self.fail_game_id:
            raise RuntimeError("report validation failed")
        return await super().get_prepared_game_report(game_id)


@dataclass
class FakeWechat:
    media_id: str = "draft-1"

    def __post_init__(self) -> None:
        self.calls: list[tuple[Article, ...]] = []

    async def __aenter__(self) -> "FakeWechat":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def create_draft(
        self,
        articles: tuple[Article, ...],
    ) -> DraftReceipt:
        self.calls.append(articles)
        return DraftReceipt(
            media_id=self.media_id,
            content_fingerprint="f" * 64,
            created_at=datetime(2026, 4, 11, tzinfo=UTC),
        )


def _logger() -> logging.Logger:
    logger = logging.getLogger("test.auto_report")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def test_request_deduplicates_and_sorts_cartesian_product() -> None:
    request = PipelineRequest(
        report_dates=(
            date(2026, 4, 12),
            date(2026, 4, 11),
            date(2026, 4, 11),
        ),
        competitions=(
            Competition.FUTSAL,
            Competition.MALE,
            Competition.FEMALE,
            Competition.MALE,
        ),
    )

    assert request.stage is Stage.ARTICLE
    assert request.combinations == (
        (date(2026, 4, 11), Competition.MALE),
        (date(2026, 4, 11), Competition.FEMALE),
        (date(2026, 4, 11), Competition.FUTSAL),
        (date(2026, 4, 12), Competition.MALE),
        (date(2026, 4, 12), Competition.FEMALE),
        (date(2026, 4, 12), Competition.FUTSAL),
    )


def test_cli_defaults_to_article_and_cover_options_are_mutually_exclusive() -> None:
    parser = _parser()
    args = parser.parse_args(
        ["--dates", "2026-04-11", "--competitions", "male"]
    )
    assert args.stage is Stage.ARTICLE
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--dates",
                "2026-04-11",
                "--competitions",
                "male",
                "--cover",
                "cover.png",
                "--cover-media-id",
                "id",
            ]
        )


def test_report_warning_message_uses_team_name_and_friendly_time() -> None:
    warning = GameEventIssue(
        severity="warning",
        code="multiple_events_same_time",
        message="Team has multiple semantic events at the same time.",
        event_ids=(139609, 139610, 139611),
        side="away",
        minute=96,
        stoppage_minute=0,
    )

    assert _report_warning_message(
        warning,
        game_id=4237,
        home_name="A",
        away_name="新雅书院",
    ) == "新雅书院96分钟存在多个事件，请注意检查前后顺序是否正确"


@_async_test
async def test_report_queries_each_competition_once_and_caches_skips(
    tmp_path: Path,
) -> None:
    male_games = [
        _game(2, datetime(2026, 4, 12, 10, tzinfo=UTC), home_abandon=True),
        _game(1, datetime(2026, 4, 11, 9, tzinfo=UTC)),
        _game(
            3,
            datetime(2026, 4, 11, 11, tzinfo=UTC),
            status=GameStatus.SCHEDULED,
        ),
    ]
    football = FakeFootball(
        {
            (122, 124, 126): male_games,
            (123,): [],
        },
        warning_game_ids={1},
    )
    request = PipelineRequest(
        report_dates=(date(2026, 4, 11), date(2026, 4, 12)),
        competitions=(Competition.FEMALE, Competition.MALE),
        stage=Stage.REPORT,
    )
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        logger=_logger(),
    ).run(request)

    assert [query.tournament_ids for query in football.queries] == [
        (122, 124, 126),
        (123,),
    ]
    assert len(football.reports) == 1
    assert football.reports[0]["refresh_stats"] is False
    assert [run.competition for run in result.runs] == [
        Competition.MALE,
        Competition.FEMALE,
        Competition.MALE,
        Competition.FEMALE,
    ]
    first_manifest = json.loads(
        result.runs[0].report_manifest_path.read_text(encoding="utf-8")
    )
    assert first_manifest["items"][0]["artifacts"] == [
        {
            "kind": "image",
            "path": "reports/0900_1.png",
            "sha256": first_manifest["items"][0]["artifacts"][0]["sha256"],
        }
    ]
    assert first_manifest["items"][0]["warnings"][0]["code"] == (
        "lineup_under_capacity"
    )
    assert first_manifest["skipped_unfinished"][0]["game_id"] == 3
    abandon_manifest = json.loads(
        result.runs[2].report_manifest_path.read_text(encoding="utf-8")
    )
    abandon_artifact = abandon_manifest["items"][0]["artifacts"][0]
    assert abandon_artifact["kind"] == "text"
    assert (
        result.runs[2].run_directory / abandon_artifact["path"]
    ).read_text(encoding="utf-8") == "AvsB的比赛，由于A弃赛，记为A 0:3 B。"
    assert result.runs[1].reason == "no_games"
    assert result.runs[3].reason == "no_games"

    cached = FakeFootball({})
    second = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: cached,
        logger=_logger(),
    ).run(request)
    assert cached.queries == []
    assert second.runs[1].reason == "no_games"


@_async_test
async def test_override_requeries_replaces_reports_and_never_refreshes_stats(
    tmp_path: Path,
) -> None:
    first = FakeFootball(
        {
            (122, 124, 126): [
                _game(10, datetime(2026, 4, 11, 9, tzinfo=UTC))
            ]
        }
    )
    request = PipelineRequest(
        report_dates=(date(2026, 4, 11),),
        competitions=(Competition.MALE,),
        stage=Stage.ARTICLE,
    )
    await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: first,
        logger=_logger(),
    ).run(request)

    second = FakeFootball(
        {
            (122, 124, 126): [
                _game(11, datetime(2026, 4, 11, 10, tzinfo=UTC))
            ]
        }
    )
    await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: second,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=request.report_dates,
            competitions=request.competitions,
            stage=Stage.REPORT,
            override=True,
        )
    )

    report_dir = (
        tmp_path / "runs" / "auto_report" / "2026-04-11_male" / "reports"
    )
    assert [path.name for path in report_dir.glob("*.png")] == ["1000_11.png"]
    assert second.reports[0]["refresh_stats"] is False
    article_dir = report_dir.parent / "article"
    assert not article_dir.exists()

    cached = FakeFootball({})
    await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: cached,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=request.report_dates,
            competitions=request.competitions,
            stage=Stage.ARTICLE,
        )
    )
    assert cached.queries == []
    assert article_dir.is_dir()


@_async_test
async def test_article_mixes_images_and_abandon_text_in_game_order(
    tmp_path: Path,
) -> None:
    football = FakeFootball(
        {
            (128,): [
                _game(
                    20,
                    datetime(2026, 4, 11, 9, tzinfo=UTC),
                    tournament_id=128,
                ),
                _game(
                    21,
                    datetime(2026, 4, 11, 10, tzinfo=UTC),
                    tournament_id=128,
                    away_abandon=True,
                ),
            ]
        }
    )
    cover_path = tmp_path / "cover.png"
    cover_path.write_bytes(b"cover")
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=(date(2026, 4, 11),),
            competitions=(Competition.FUTSAL,),
            cover=CoverFile(cover_path),
        )
    )

    article = Article.load(result.article_directory)
    assert article.title == "【马杯五人制】今日战报"
    assert article.author == "清华绿茵"
    assert article.digest == "马杯战报"
    assert "data:image/png;base64," in article.body_html
    abandon = "AvsB的比赛，由于B弃赛，记为A 5:0 B。"
    assert abandon in article.body_html
    assert article.body_html.index("data:image/png;base64,") < (
        article.body_html.index(abandon)
    )
    assert "width:100%;height:auto" in article.body_html


@_async_test
async def test_awarded_loss_with_events_writes_png_then_text(tmp_path: Path) -> None:
    game = _game(
        22,
        datetime(2026, 4, 11, 11, tzinfo=UTC),
        away_abandon=True,
    )
    football = FakeFootball(
        {(122, 124, 126): [game]},
        mixed_game_ids={22},
    )
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=(date(2026, 4, 11),),
            competitions=(Competition.MALE,),
        )
    )

    manifest = json.loads(result.report_manifest_path.read_text(encoding="utf-8"))
    assert [item["kind"] for item in manifest["items"][0]["artifacts"]] == [
        "image",
        "text",
    ]
    assert [path.suffix for path in result.report_files] == [".png", ".txt"]
    article = Article.load(result.article_directory)
    text = "AvsB的比赛，由于B被判负，记为A 3:0 B。"
    assert article.body_html.index("data:image/png;base64,") < article.body_html.index(text)


@_async_test
async def test_corrupt_report_image_requires_override(tmp_path: Path) -> None:
    football = FakeFootball(
        {
            (122, 124, 126): [
                _game(30, datetime(2026, 4, 11, 9, tzinfo=UTC))
            ]
        }
    )
    request = PipelineRequest(
        report_dates=(date(2026, 4, 11),),
        competitions=(Competition.MALE,),
        stage=Stage.REPORT,
    )
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        logger=_logger(),
    ).run(request)
    result.report_files[0].write_bytes(b"changed")

    with pytest.raises(ArtifactValidationError, match="--override"):
        await AutoReportPipeline(
            project_root=tmp_path,
            football_service_factory=lambda: FakeFootball({}),
            logger=_logger(),
        ).run(
            PipelineRequest(
                report_dates=request.report_dates,
                competitions=request.competitions,
            )
        )


@_async_test
async def test_publish_orders_articles_shares_receipt_and_reuses_it(
    tmp_path: Path,
) -> None:
    games = {
        (122, 124, 126): [
            _game(40, datetime(2026, 4, 11, 9, tzinfo=UTC)),
            _game(41, datetime(2026, 4, 12, 9, tzinfo=UTC)),
        ],
        (123,): [
            _game(
                42,
                datetime(2026, 4, 11, 10, tzinfo=UTC),
                tournament_id=123,
            ),
            _game(
                43,
                datetime(2026, 4, 12, 10, tzinfo=UTC),
                tournament_id=123,
            ),
        ],
    }
    football = FakeFootball(games)
    wechat = FakeWechat()
    request = PipelineRequest(
        report_dates=(date(2026, 4, 12), date(2026, 4, 11)),
        competitions=(Competition.FEMALE, Competition.MALE),
        stage=Stage.PUBLISH,
    )
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        wechat_service_factory=lambda: wechat,
        logger=_logger(),
    ).run(request)

    assert [article.title for article in wechat.calls[0]] == [
        "【马杯男足】今日战报",
        "【马杯女足】今日战报",
        "【马杯男足】今日战报",
        "【马杯女足】今日战报",
    ]
    assert all(
        isinstance(article.cover, CoverMediaId)
        and article.cover.media_id == DEFAULT_REPORT_COVER_MEDIA_ID
        for article in wechat.calls[0]
    )
    assert result.draft_media_id == "draft-1"
    for run in result.runs:
        draft = json.loads(
            (run.run_directory / "draft.json").read_text(encoding="utf-8")
        )
        assert draft["receipts"][0]["media_id"] == "draft-1"
        assert len(draft["receipts"][0]["articles"]) == 4

    unused_wechat = FakeWechat("draft-2")
    cached = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: FakeFootball({}),
        wechat_service_factory=lambda: unused_wechat,
        logger=_logger(),
    ).run(request)
    assert unused_wechat.calls == []
    assert cached.draft_media_id == "draft-1"

    replacement_wechat = FakeWechat("draft-2")
    replaced = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: FakeFootball(games),
        wechat_service_factory=lambda: replacement_wechat,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=request.report_dates,
            competitions=request.competitions,
            stage=Stage.PUBLISH,
            override=True,
        )
    )
    assert len(replacement_wechat.calls) == 1
    assert replaced.draft_media_id == "draft-2"
    for run in replaced.runs:
        draft = json.loads(
            (run.run_directory / "draft.json").read_text(encoding="utf-8")
        )
        assert [receipt["media_id"] for receipt in draft["receipts"]] == [
            "draft-1",
            "draft-2",
        ]


@_async_test
async def test_all_skipped_does_not_create_wechat_draft(tmp_path: Path) -> None:
    football = FakeFootball(
        {
            (122, 124, 126): [
                _game(
                    50,
                    datetime(2026, 4, 11, 9, tzinfo=UTC),
                    status=GameStatus.STARTED,
                )
            ]
        }
    )
    wechat = FakeWechat()
    result = await AutoReportPipeline(
        project_root=tmp_path,
        football_service_factory=lambda: football,
        wechat_service_factory=lambda: wechat,
        logger=_logger(),
    ).run(
        PipelineRequest(
            report_dates=(date(2026, 4, 11),),
            competitions=(Competition.MALE,),
            stage=Stage.PUBLISH,
        )
    )
    assert result.status == "skipped"
    assert result.runs[0].reason == "no_finished_games"
    assert wechat.calls == []


@_async_test
async def test_report_error_blocks_all_articles_and_wechat(tmp_path: Path) -> None:
    football = FailingFootball(
        {
            (122, 124, 126): [
                _game(60, datetime(2026, 4, 11, 9, tzinfo=UTC))
            ],
            (123,): [
                _game(
                    61,
                    datetime(2026, 4, 11, 10, tzinfo=UTC),
                    tournament_id=123,
                )
            ],
        },
        fail_game_id=61,
    )
    wechat = FakeWechat()
    with pytest.raises(RuntimeError, match="validation"):
        await AutoReportPipeline(
            project_root=tmp_path,
            football_service_factory=lambda: football,
            wechat_service_factory=lambda: wechat,
            logger=_logger(),
        ).run(
            PipelineRequest(
                report_dates=(date(2026, 4, 11),),
                competitions=(Competition.MALE, Competition.FEMALE),
                stage=Stage.PUBLISH,
            )
        )
    assert not (
        tmp_path / "runs" / "auto_report" / "2026-04-11_male" / "article"
    ).exists()
    assert wechat.calls == []


@_async_test
async def test_publish_rejects_more_than_eight_articles_before_wechat(
    tmp_path: Path,
) -> None:
    dates = tuple(date(2026, 4, day) for day in range(1, 10))
    football = FakeFootball(
        {
            (122, 124, 126): [
                _game(
                    100 + day,
                    datetime(2026, 4, day, 9, tzinfo=UTC),
                )
                for day in range(1, 10)
            ]
        }
    )
    wechat = FakeWechat()
    with pytest.raises(PipelineError, match="8"):
        await AutoReportPipeline(
            project_root=tmp_path,
            football_service_factory=lambda: football,
            wechat_service_factory=lambda: wechat,
            logger=_logger(),
        ).run(
            PipelineRequest(
                report_dates=dates,
                competitions=(Competition.MALE,),
                stage=Stage.PUBLISH,
            )
        )
    assert wechat.calls == []
