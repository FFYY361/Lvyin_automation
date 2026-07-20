from __future__ import annotations

import json
import logging
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from auto_preview import (
    ArtifactValidationError,
    AutoPreviewPipeline,
    Competition,
    PipelineRequest,
    Stage,
)
from auto_preview.config import competition_config
from auto_preview.cli import _parser, main as cli_main
from auto_preview.diagnostics import failure_lines
from auto_preview.logging_utils import configure_logging
from auto_preview.source import PreviewSourceBuilder
from auto_preview.state import sha256_file
from preview import SeasonOutcome
from preview.template import _head_to_head_line
from thufootball import (
    BatchQueryError,
    GameQuery,
    GameStatus,
    GameSummary,
    HeadToHeadHistory,
    HeadToHeadSummary,
    MatchResult,
    PermissionError as THUFootballPermissionError,
    QueryValidationError,
    TeamGameResult,
    TeamTournamentOutcome,
    Timeout,
)
from wechat_official import Article, CoverFile, DraftReceipt


SHANGHAI = timezone(timedelta(hours=8))


def _game(
    game_id: int,
    tournament_id: int,
    kickoff_local: datetime,
    *,
    status: GameStatus,
    home_id: int = 1,
    home_name: str = "社会科学学院女足",
    away_id: int = 2,
    away_name: str = "经济管理学院女足",
    home_short_name: str | None = None,
    away_short_name: str | None = None,
    tournament_name: str = "2025~2026马杯女足",
    home_score: int | None = None,
    away_score: int | None = None,
) -> GameSummary:
    return GameSummary(
        game_id=game_id,
        tournament_id=tournament_id,
        tournament_name=tournament_name,
        kickoff_utc=kickoff_local.astimezone(UTC),
        kickoff_local=kickoff_local,
        status=status,
        record_active=True,
        valid=True,
        stage="小组赛",
        group_name=None,
        round=1,
        home_tournament_team_id=home_id + 1000,
        home_team_id=home_id,
        home_team_name=home_name,
        away_tournament_team_id=away_id + 1000,
        away_team_id=away_id,
        away_team_name=away_name,
        home_score=home_score,
        away_score=away_score,
        result_text=(
            f"{home_score}:{away_score}"
            if home_score is not None and away_score is not None
            else None
        ),
        penalty_shootout=False,
        home_penalty=None,
        away_penalty=None,
        home_abandon=False,
        away_abandon=False,
        field_name="紫荆操场",
        home_team_brief_name=home_short_name,
        away_team_brief_name=away_short_name,
    )


def _team_result(game: GameSummary, team_id: int) -> TeamGameResult:
    is_home = game.home_team_id == team_id
    assert game.home_score is not None and game.away_score is not None
    goals_for = game.home_score if is_home else game.away_score
    goals_against = game.away_score if is_home else game.home_score
    return TeamGameResult(
        game=game,
        team_id=team_id,
        opponent_id=game.away_team_id if is_home else game.home_team_id,
        opponent_name=game.away_team_name if is_home else game.home_team_name,
        venue="home" if is_home else "away",
        goals_for=goals_for,
        goals_against=goals_against,
        penalty_goals_for=None,
        penalty_goals_against=None,
        score_text=f"{goals_for}:{goals_against}",
        result=(
            MatchResult.WIN
            if goals_for > goals_against
            else MatchResult.DRAW
            if goals_for == goals_against
            else MatchResult.LOSS
        ),
    )


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _logger() -> tuple[logging.Logger, _ListHandler]:
    logger = logging.Logger("auto-preview-test")
    handler = _ListHandler()
    logger.addHandler(handler)
    return logger, handler


class _FakeQueries:
    def __init__(self) -> None:
        self.target = _game(
            500,
            123,
            datetime(2026, 4, 11, 15, 30, tzinfo=SHANGHAI),
            status=GameStatus.SCHEDULED,
        )
        self.targets = [self.target]
        self.game_queries: list[GameQuery] = []
        self.team_match_calls: list[tuple[int, int | None, bool]] = []
        self.outcome_calls: list[tuple[int, tuple[int, ...]]] = []
        self.outcome_query_error_team_ids: set[int] = set()
        self.h2h_calls: list[tuple[int, int, tuple[int, ...]]] = []

        before_times = [
            datetime(2026, 4, day, 12, 0, tzinfo=SHANGHAI)
            for day in range(10, 4, -1)
        ]
        self.results: dict[int, list[TeamGameResult]] = {1: [], 2: []}
        for team_id in (1, 2):
            for index, kickoff in enumerate(before_times, start=1):
                game = _game(
                    600 + team_id * 10 + index,
                    123,
                    kickoff,
                    status=GameStatus.FINISHED,
                    home_score=2,
                    away_score=1,
                )
                self.results[team_id].append(_team_result(game, team_id))
            after = _game(
                690 + team_id,
                123,
                datetime(2026, 4, 12, 12, 0, tzinfo=SHANGHAI),
                status=GameStatus.FINISHED,
                home_score=1,
                away_score=0,
            )
            self.results[team_id].append(_team_result(after, team_id))

        history_games = tuple(
            _game(
                700 + index,
                tournament_id,
                datetime(2025 - index, 4, 1, 12, 0, tzinfo=SHANGHAI),
                status=GameStatus.FINISHED,
                tournament_name=season_name,
                home_score=1,
                away_score=1,
            )
            for index, (tournament_id, season_name) in enumerate(
                (
                    (102, "2024~2025马杯女足"),
                    (90, "2023~2024马杯女足"),
                    (74, "2022~2023马杯女足"),
                    (74, "2022~2023马杯女足"),
                )
            )
        )
        self.history = HeadToHeadHistory(
            team_a_id=1,
            team_b_id=2,
            tournament_ids=(102, 90, 74),
            matches=history_games,
            summary=HeadToHeadSummary(0, 4, 0),
            by_tournament=MappingProxyType({}),
        )

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        self.game_queries.append(query)
        return self.targets

    async def query_team_matches(
        self,
        team_id: int,
        tournament_id: int | None = None,
        *,
        include_unfinished: bool = False,
    ) -> list[TeamGameResult]:
        self.team_match_calls.append((team_id, tournament_id, include_unfinished))
        return self.results[team_id]

    async def query_team_outcomes(
        self, team_id: int, tournament_ids: tuple[int, ...]
    ) -> list[TeamTournamentOutcome]:
        self.outcome_calls.append((team_id, tournament_ids))
        if team_id in self.outcome_query_error_team_ids:
            raise QueryValidationError(
                "team is not present in outcome catalog",
                stage="query_team_outcomes",
            )
        team_name = "社会科学学院女足" if team_id == 1 else "经济管理学院女足"
        return [
            TeamTournamentOutcome(team_name, 102, "马杯女足2024~2025", "2024~2025", "八强"),
            TeamTournamentOutcome(team_name, 90, "马杯女足2023~2024", "2023~2024", "四强"),
        ]

    async def query_team_to_team_matches(
        self,
        team_a_id: int,
        team_b_id: int,
        tournament_ids: tuple[int, ...],
        *,
        include_unfinished: bool = False,
    ) -> HeadToHeadHistory:
        self.h2h_calls.append((team_a_id, team_b_id, tournament_ids))
        return self.history


class SourceBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_queries_only_configured_ids_and_keeps_all_pre_match_results(self) -> None:
        queries = _FakeQueries()
        logger, handler = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.FEMALE),
            logger=logger,
        )
        queries.targets.append(
            _game(
                501,
                123,
                datetime(2026, 4, 11, 19, 0, tzinfo=SHANGHAI),
                status=GameStatus.SCHEDULED,
            )
        )

        source = await builder.build(datetime(2026, 4, 11).date())

        self.assertEqual(queries.game_queries[0].tournament_ids, (123,))
        self.assertEqual(
            queries.team_match_calls,
            [(1, 123, False), (2, 123, False)],
        )
        self.assertEqual(
            queries.outcome_calls,
            [(1, (102, 90)), (2, (102, 90))],
        )
        self.assertEqual(queries.h2h_calls, [(1, 2, (102, 90, 74))])
        self.assertEqual(len(source.matches), 2)
        self.assertEqual(source.matches[0].competition_name, "女足")
        self.assertEqual(len(source.matches[0].home.previous_outcomes), 3)
        self.assertEqual(
            source.matches[0].home.previous_outcomes[-1],
            SeasonOutcome(
                season="22-23",
                competition_label=None,
                outcome="未参赛",
            ),
        )
        self.assertEqual(len(source.matches[0].home.current_results), 6)
        self.assertEqual(len(source.matches[0].away.current_results), 6)
        self.assertTrue(
            all(
                result.game_id < 690
                for result in source.matches[0].home.current_results
            )
        )
        self.assertEqual(len(source.matches[0].head_to_head), 4)
        self.assertEqual(source.matches[0].home.short_name, "社会")
        self.assertEqual(source.matches[0].away.short_name, "经济")
        self.assertTrue(
            all(
                result.home.team_id == 2
                for result in source.matches[0].away.current_results
            )
        )
        self.assertTrue(
            all(
                (result.home_score, result.away_score) == (1, 2)
                for result in source.matches[0].away.current_results
            )
        )
        self.assertEqual(
            sum("2022~2023 无法获取排名，按未参赛展示" in item for item in handler.messages),
            2,
        )

    def test_current_tournament_names_are_fixed_short_labels(self) -> None:
        self.assertEqual(
            dict(competition_config(Competition.MALE).current_tournament_names),
            {122: "男足甲级", 124: "男足乙级", 126: "男足丙级"},
        )
        self.assertEqual(
            dict(competition_config(Competition.FEMALE).current_tournament_names),
            {123: "女足"},
        )
        self.assertEqual(
            dict(competition_config(Competition.FUTSAL).current_tournament_names),
            {128: "五人制"},
        )

    def test_head_to_head_uses_configured_season_when_name_omits_year(
        self,
    ) -> None:
        queries = _FakeQueries()
        logger, _ = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.MALE),
            logger=logger,
        )

        cases = ((89, "23-24"), (72, "22-23"))
        for index, (tournament_id, expected_season) in enumerate(cases):
            with self.subTest(tournament_id=tournament_id):
                played = builder._played_match(
                    _game(
                        800 + index,
                        tournament_id,
                        datetime(2024, 3, 1, 12, 0, tzinfo=SHANGHAI),
                        status=GameStatus.FINISHED,
                        tournament_name="马杯男足甲级",
                        home_score=1,
                        away_score=0,
                    )
                )

                self.assertEqual(played.season, expected_season)
                self.assertEqual(played.competition_label, "甲")
                self.assertTrue(
                    _head_to_head_line(played).startswith(
                        f"（{expected_season}-甲）"
                    )
                )

    async def test_team_missing_from_outcome_catalog_is_shown_as_not_entered(self) -> None:
        queries = _FakeQueries()
        queries.outcome_query_error_team_ids.add(1)
        logger, _ = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.FEMALE),
            logger=logger,
        )

        source = await builder.build(datetime(2026, 4, 11).date())

        self.assertEqual(
            [item.outcome for item in source.matches[0].home.previous_outcomes],
            ["未参赛", "未参赛", "未参赛"],
        )

    async def test_finished_target_game_is_included_for_historical_replay(self) -> None:
        queries = _FakeQueries()
        queries.target = _game(
            500,
            123,
            datetime(2026, 4, 11, 15, 30, tzinfo=SHANGHAI),
            status=GameStatus.FINISHED,
            home_score=2,
            away_score=1,
        )
        queries.targets = [queries.target]
        logger, _ = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.FEMALE),
            logger=logger,
        )

        source = await builder.build(datetime(2026, 4, 11).date())

        self.assertEqual([match.game_id for match in source.matches], [500])

    async def test_uses_trusted_database_short_name_and_rejects_long_value(self) -> None:
        queries = _FakeQueries()
        queries.target = _game(
            500,
            123,
            datetime(2026, 4, 11, 15, 30, tzinfo=SHANGHAI),
            status=GameStatus.SCHEDULED,
            home_short_name="社科女足",
            away_short_name="经济管理学院女足",
        )
        queries.targets = [queries.target]
        logger, handler = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.FEMALE),
            logger=logger,
        )

        source = await builder.build(datetime(2026, 4, 11).date())

        self.assertEqual(source.matches[0].home.short_name, "社科女足")
        self.assertEqual(source.matches[0].away.short_name, "经济")
        self.assertFalse(any("team_id=1" in item and "简称不可信" in item for item in handler.messages))
        self.assertEqual(
            sum("team_id=2" in item and "简称不可信" in item for item in handler.messages),
            1,
        )

    def test_official_team_name_and_brief_name_precede_database_values(
        self,
    ) -> None:
        queries = _FakeQueries()
        logger, handler = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.MALE),
            logger=logger,
        )

        # 直接验证目录解析，避免将网络查询行为混入此单元测试。
        team = builder._team_ref(
            33,
            "数据库计算机队全称",
            "数据库中的超长简称",
        )

        self.assertEqual(team.name, "计算机科学与技术系-全球创新学院")
        self.assertEqual(team.short_name, "计算机-GIX")
        self.assertFalse(any("team_id=33" in item for item in handler.messages))

    async def test_current_results_home_normalisation_can_be_disabled(self) -> None:
        queries = _FakeQueries()
        logger, _ = _logger()
        builder = PreviewSourceBuilder(
            queries,  # type: ignore[arg-type]
            competition_config(Competition.FEMALE),
            logger=logger,
        )

        with patch("auto_preview.source.CURRENT_RESULTS_TEAM_ALWAYS_HOME", False):
            source = await builder.build(datetime(2026, 4, 11).date())

        away_results = source.matches[0].away.current_results
        self.assertTrue(away_results)
        self.assertTrue(all(result.away.team_id == 2 for result in away_results))


class CliTests(unittest.TestCase):
    def test_help_works_outside_repository_and_only_documents_supported_spelling(self) -> None:
        script = _PROJECT_ROOT / "scripts" / "auto_preview.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{male,female,futsal}", result.stdout)
        self.assertIn("--override", result.stdout)
        self.assertNotIn("--overide", result.stdout)

    def test_parser_supports_all_stages_and_rejects_conflicting_covers(self) -> None:
        parser = _parser()
        for stage in Stage:
            args = parser.parse_args(
                ["2026-04-11", "male", "--stage", stage.value]
            )
            self.assertEqual(args.stage, stage)
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "2026-04-11",
                        "female",
                        "--cover",
                        "cover.png",
                        "--cover-media-id",
                        "media-id",
                    ]
                )
            with self.assertRaises(SystemExit):
                parser.parse_args(["2026-04-11", "futsal", "--overide"])


class DefaultCoverAssetTests(unittest.TestCase):
    def test_default_cover_is_expected_png_size_and_under_upload_limit(self) -> None:
        path = _PROJECT_ROOT / "src" / "auto_preview" / "assets" / "default_cover.png"
        content = path.read_bytes()
        self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", content[16:24]), (1536, 1024))
        self.assertLess(len(content), 10 * 1024 * 1024)


class LoggingTests(unittest.TestCase):
    def test_logging_permission_error_is_diagnosed_without_traceback(self) -> None:
        stderr = StringIO()
        with patch(
            "auto_preview.cli.configure_logging",
            side_effect=PermissionError(13, "access denied", "runs"),
        ):
            with redirect_stderr(stderr):
                status = cli_main(["2026-03-13", "male", "--stage", "data"])

        rendered = stderr.getvalue()
        self.assertEqual(status, 2)
        self.assertIn("类别：本地文件权限错误", rendered)
        self.assertIn("阶段：logging", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_file_log_is_utf8_plain_text_without_ansi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with redirect_stderr(StringIO()):
                logger = configure_logging(Path(directory))
                logger.warning("⚠ [1/3] 测试日志")
            for handler in logger.handlers:
                handler.flush()
            content = (Path(directory) / "auto_preview.log").read_text(encoding="utf-8")
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        self.assertIn("[1/3] 测试日志", content)
        self.assertNotIn("\x1b", content)

    def test_batch_permission_error_has_actionable_child_details(self) -> None:
        error = BatchQueryError(
            {
                tournament_id: THUFootballPermissionError(
                    "GetTournInfo denied access to the requested resource",
                    stage="http",
                )
                for tournament_id in (122, 124, 126)
            }
        )

        rendered = "\n".join(
            failure_lines(error, log_path=Path("pipeline.log"))
        )

        self.assertIn("批量赛事查询错误（权限错误）", rendered)
        self.assertIn("赛事 IDs=(122, 124, 126)", rendered)
        self.assertIn("赛事 122：权限错误", rendered)
        self.assertIn("确认当前 THUFootball 账号能够查看失败赛事", rendered)
        self.assertIn("完整日志：pipeline.log", rendered)

    def test_network_and_local_validation_errors_are_distinguished(self) -> None:
        try:
            raise Timeout(
                "GetTournInfo timed out",
                stage="http",
                retryable=True,
                tournament_id=122,
            ) from OSError("sensitive low-level message")
        except Timeout as timeout:
            network = "\n".join(failure_lines(timeout))

        local = "\n".join(
            failure_lines(
                ArtifactValidationError(
                    "已有 source.json 验收失败",
                    stage="data-validation",
                )
            )
        )
        redacted = "\n".join(
            failure_lines(
                ValueError(
                    "request failed: session_key=secret-session, "
                    "'access_token': 'secret-token'"
                )
            )
        )

        self.assertIn("类别：网络超时", network)
        self.assertIn("可重试：是", network)
        self.assertIn("底层异常=OSError", network)
        self.assertNotIn("sensitive low-level message", network)
        self.assertIn("类别：本地产物校验错误", local)
        self.assertIn("--override 仅用于重新查询并覆盖 source.json", local)
        self.assertNotIn("secret-session", redacted)
        self.assertNotIn("secret-token", redacted)
        self.assertEqual(redacted.count("<redacted>"), 2)


class _Context:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeWechat:
    def __init__(self) -> None:
        self.articles: list[Article] = []

    async def create_draft(self, article: Article) -> DraftReceipt:
        self.articles.append(article)
        return DraftReceipt(
            media_id=f"draft-{len(self.articles)}",
            content_fingerprint=article.content_fingerprint,
            created_at=datetime(2026, 7, 16, len(self.articles), tzinfo=UTC),
        )


class PipelineStateTests(unittest.IsolatedAsyncioTestCase):
    def _root(self, directory: str) -> Path:
        root = Path(directory)
        template_directory = root / "templates" / "qhly_preview_v1"
        template_directory.mkdir(parents=True)
        shutil.copyfile(
            _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "template.html",
            template_directory / "template.html",
        )
        return root

    async def test_existing_invalid_source_errors_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            logger, _ = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                prompt=lambda _: True,
                logger=logger,
            )
            request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.DATA,
            )
            result = await runner.run(request)
            result.source_path.write_text("{broken", encoding="utf-8")
            before = result.source_path.read_bytes()

            with self.assertRaises(ArtifactValidationError):
                await runner.run(request)

            self.assertEqual(result.source_path.read_bytes(), before)
            self.assertEqual(len(queries.game_queries), 1)

    async def test_default_cover_publish_reuse_and_override_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            wechat = _FakeWechat()
            logger, _ = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                wechat_service_factory=lambda: _Context(wechat),
                prompt=lambda _: True,
                logger=logger,
            )
            request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.PUBLISH,
                override=True,
            )

            first = await runner.run(request)
            self.assertEqual(first.draft_media_id, "draft-1")
            article = Article.load(first.article_directory)
            self.assertIsInstance(article.cover, CoverFile)
            self.assertEqual(article.cover.path.name, "cover.png")

            reused = await runner.run(
                PipelineRequest(
                    request.preview_date,
                    request.competition,
                    Stage.PUBLISH,
                )
            )
            self.assertEqual(reused.draft_media_id, "draft-1")
            self.assertEqual(len(wechat.articles), 1)

            source = json.loads(first.source_path.read_text(encoding="utf-8"))
            source["headline"] = "人工修改后创建新草稿"
            first.source_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            changed = await runner.run(
                PipelineRequest(
                    request.preview_date,
                    request.competition,
                    Stage.PUBLISH,
                )
            )
            self.assertEqual(changed.draft_media_id, "draft-2")
            self.assertEqual(len(wechat.articles), 2)

            second = await runner.run(request)
            self.assertEqual(second.draft_media_id, "draft-3")
            history = json.loads(
                (second.run_directory / "draft.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(history["receipts"]), 3)

    async def test_pause_edit_source_and_resume_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            logger, _ = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                prompt=lambda _: False,
                logger=logger,
            )
            request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.ARTICLE,
            )

            paused = await runner.run(request)
            self.assertEqual(paused.status, "paused")
            self.assertFalse((paused.run_directory / "article").exists())

            raw = json.loads(paused.source_path.read_text(encoding="utf-8"))

            def fill(value):
                if isinstance(value, str) and value.startswith("【待填写"):
                    return "已填写"
                if isinstance(value, list):
                    return [fill(item) for item in value]
                if isinstance(value, dict):
                    return {key: fill(item) for key, item in value.items()}
                return value

            paused.source_path.write_text(
                json.dumps(fill(raw), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            resumed = await runner.run(request)

            self.assertEqual(resumed.status, "ok")
            self.assertTrue(resumed.article_directory.is_dir())
            self.assertEqual(len(queries.game_queries), 1)

    async def test_edited_source_rebuilds_article_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            logger, handler = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                prompt=lambda _: True,
                logger=logger,
            )
            override_request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.ARTICLE,
                override=True,
            )
            complete = await runner.run(override_request)
            raw = json.loads(complete.source_path.read_text(encoding="utf-8"))
            raw["headline"] = "人工修改后的标题"
            complete.source_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            state_path = complete.run_directory / "run.json"
            before_article = Article.load(complete.article_directory)

            rebuilt = await runner.run(
                PipelineRequest(
                    override_request.preview_date,
                    override_request.competition,
                    Stage.ARTICLE,
                )
            )

            article = Article.load(rebuilt.article_directory)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("人工修改后的标题", article.title)
            self.assertNotEqual(
                article.content_fingerprint, before_article.content_fingerprint
            )
            self.assertEqual(
                state["article"]["source_sha256"], sha256_file(complete.source_path)
            )
            self.assertEqual(
                state["article"]["cover"]["sha256"],
                sha256_file(article.cover.path),
            )
            self.assertEqual(len(queries.game_queries), 1)
            self.assertTrue(
                any("source 指纹已变化" in message for message in handler.messages)
            )

    async def test_invalid_article_is_rebuilt_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            logger, _ = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                prompt=lambda _: True,
                logger=logger,
            )
            request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.ARTICLE,
                override=True,
            )
            complete = await runner.run(request)
            (complete.article_directory / "article.json").write_text(
                "{broken", encoding="utf-8"
            )

            rebuilt = await runner.run(
                PipelineRequest(
                    request.preview_date,
                    request.competition,
                    Stage.ARTICLE,
                )
            )

            self.assertIsInstance(Article.load(rebuilt.article_directory), Article)
            self.assertEqual(len(queries.game_queries), 1)

    async def test_changed_template_rebuilds_article_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            logger, handler = _logger()
            runner = AutoPreviewPipeline(
                project_root=root,
                query_service_factory=lambda: _Context(queries),
                prompt=lambda _: True,
                logger=logger,
            )
            request = PipelineRequest(
                datetime(2026, 4, 11).date(),
                Competition.FEMALE,
                Stage.ARTICLE,
                override=True,
            )
            complete = await runner.run(request)
            before = Article.load(complete.article_directory)
            template_path = root / "templates" / "qhly_preview_v1" / "template.html"
            template_path.write_text(
                template_path.read_text(encoding="utf-8")
                + "\n<section>模板变化测试标记</section>\n",
                encoding="utf-8",
            )

            rebuilt = await runner.run(
                PipelineRequest(
                    request.preview_date,
                    request.competition,
                    Stage.ARTICLE,
                )
            )

            article = Article.load(rebuilt.article_directory)
            self.assertIn("模板变化测试标记", article.body_html)
            self.assertNotEqual(
                article.content_fingerprint, before.content_fingerprint
            )
            self.assertEqual(len(queries.game_queries), 1)
            self.assertTrue(
                any("模板指纹已变化" in message for message in handler.messages)
            )


if __name__ == "__main__":
    unittest.main()
