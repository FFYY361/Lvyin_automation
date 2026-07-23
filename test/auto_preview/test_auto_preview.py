from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
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
    CombinationResult,
    Competition,
    PipelineError,
    PipelineRequest,
    PipelineResult,
    Stage,
)
from auto_preview.cli import _parser
from auto_preview.cli import main as cli_main
from auto_preview.config import competition_config
from auto_preview.diagnostics import failure_lines
from auto_preview.logging_utils import configure_logging
from auto_preview.source import PreviewSourceBuilder
from auto_preview.state import sha256_file
from preview import PreviewValidationError, SeasonOutcome
from preview.template import _head_to_head_line
from thufootball import (
    BatchQueryError,
    GameQuery,
    GameStatus,
    GameSummary,
    HeadToHeadHistory,
    HeadToHeadSummary,
    MatchResult,
    QueryValidationError,
    TeamGameResult,
    TeamTournamentOutcome,
    Timeout,
)
from thufootball import (
    PermissionError as THUFootballPermissionError,
)
from weather import DailyWeather, WeatherNetworkError
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
            datetime(2026, 4, day, 12, 0, tzinfo=SHANGHAI) for day in range(10, 4, -1)
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
            TeamTournamentOutcome(
                team_name, 102, "马杯女足2024~2025", "2024~2025", "八强"
            ),
            TeamTournamentOutcome(
                team_name, 90, "马杯女足2023~2024", "2023~2024", "四强"
            ),
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


class _BatchQueries(_FakeQueries):
    def __init__(self) -> None:
        super().__init__()
        self.no_games: set[tuple[date, tuple[int, ...]]] = set()
        self.available_dates = (
            date(2026, 4, 11),
            date(2026, 4, 12),
            date(2026, 4, 13),
        )

    async def query_games(self, query: GameQuery) -> list[GameSummary]:
        self.game_queries.append(query)
        assert query.match_date is None
        tournament_id = query.tournament_ids[0]
        return [
            _game(
                preview_date.toordinal() * 1000 + tournament_id,
                tournament_id,
                datetime(
                    preview_date.year,
                    preview_date.month,
                    preview_date.day,
                    15,
                    30,
                    tzinfo=SHANGHAI,
                ),
                status=GameStatus.SCHEDULED,
                tournament_name=f"赛事 {tournament_id}",
            )
            for preview_date in self.available_dates
            if (preview_date, query.tournament_ids) not in self.no_games
        ]


class SourceBuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_queries_only_configured_ids_and_keeps_all_pre_match_results(
        self,
    ) -> None:
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
        self.assertFalse(
            any(
                "按未参赛展示" in message
                or "current_results 查询" in message
                or "current_results 缓存命中" in message
                for message in handler.messages
            )
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
                    _head_to_head_line(played).startswith(f"（{expected_season}-甲）")
                )

    async def test_team_missing_from_outcome_catalog_is_shown_as_not_entered(
        self,
    ) -> None:
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

    async def test_uses_trusted_database_short_name_and_rejects_long_value(
        self,
    ) -> None:
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
        self.assertFalse(
            any(
                "team_id=1" in item and "简称不可信" in item
                for item in handler.messages
            )
        )
        self.assertEqual(
            sum(
                "team_id=2" in item and "简称不可信" in item
                for item in handler.messages
            ),
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


class CliTests(unittest.TestCase):
    def test_help_works_outside_repository_and_only_documents_supported_spelling(
        self,
    ) -> None:
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
                [
                    "--dates",
                    "2026-04-11",
                    "--competitions",
                    "male",
                    "--stage",
                    stage.value,
                ]
            )
            self.assertEqual(args.stage, stage)
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--dates",
                        "2026-04-11",
                        "--competitions",
                        "female",
                        "--cover",
                        "cover.png",
                        "--cover-media-id",
                        "media-id",
                    ]
                )
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "--dates",
                        "2026-04-11",
                        "--competitions",
                        "futsal",
                        "--overide",
                    ]
                )
            with self.assertRaises(SystemExit):
                parser.parse_args(["2026-04-11", "futsal"])

    def test_batch_arguments_are_required_and_request_is_canonical(self) -> None:
        parser = _parser()
        args = parser.parse_args(
            [
                "--dates",
                "2026-04-12",
                "2026-04-11",
                "2026-04-12",
                "--competitions",
                "futsal",
                "male",
                "female",
                "male",
            ]
        )
        request = PipelineRequest(args.dates, args.competitions)

        self.assertEqual(
            request.preview_dates,
            (date(2026, 4, 11), date(2026, 4, 12)),
        )
        self.assertEqual(
            request.competitions,
            (Competition.MALE, Competition.FEMALE, Competition.FUTSAL),
        )
        self.assertEqual(
            request.combinations,
            (
                (date(2026, 4, 11), Competition.MALE),
                (date(2026, 4, 11), Competition.FEMALE),
                (date(2026, 4, 11), Competition.FUTSAL),
                (date(2026, 4, 12), Competition.MALE),
                (date(2026, 4, 12), Competition.FEMALE),
                (date(2026, 4, 12), Competition.FUTSAL),
            ),
        )
        with self.assertRaises(ValueError):
            PipelineRequest((), (Competition.MALE,))
        with self.assertRaises(ValueError):
            PipelineRequest((date(2026, 4, 11),), ())
        with self.assertRaises(TypeError):
            PipelineRequest(date(2026, 4, 11), Competition.MALE)  # type: ignore[arg-type]

    def test_cli_logs_next_command_once_without_json_output(self) -> None:
        captured_request: list[PipelineRequest] = []
        run_directory = _PROJECT_ROOT / "runs" / "auto_preview" / "2026-04-11_male"
        result = PipelineResult(
            status="ok",
            completed_stage=Stage.ARTICLE,
            runs=(
                CombinationResult(
                    preview_date=date(2026, 4, 11),
                    competition=Competition.MALE,
                    status="ok",
                    completed_stage=Stage.ARTICLE,
                    run_directory=run_directory,
                    source_path=run_directory / "source.json",
                    article_directory=run_directory / "article",
                ),
                CombinationResult(
                    preview_date=date(2026, 4, 11),
                    competition=Competition.FEMALE,
                    status="skipped",
                    completed_stage=Stage.DATA,
                    run_directory=run_directory.with_name("2026-04-11_female"),
                    reason="no_games",
                ),
            ),
            next_command=(
                "python scripts\\auto_preview.py --dates 2026-04-11 "
                "--competitions male female --stage publish"
            ),
        )

        class _Runner:
            async def run(self, request: PipelineRequest) -> PipelineResult:
                captured_request.append(request)
                return result

        output = StringIO()
        logger, handler = _logger()
        with (
            patch("auto_preview.cli.configure_logging", return_value=logger),
            patch("auto_preview.cli.AutoPreviewPipeline", return_value=_Runner()),
            redirect_stdout(output),
        ):
            status = cli_main(
                [
                    "--dates",
                    "2026-04-11",
                    "--competitions",
                    "female",
                    "male",
                    "--stage",
                    "article",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            captured_request[0].competitions, (Competition.MALE, Competition.FEMALE)
        )
        next_commands = [
            message for message in handler.messages if message.startswith("下一步 ")
        ]
        self.assertEqual(
            next_commands,
            [f"下一步 publish 命令：{result.next_command}"],
        )


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
                status = cli_main(
                    [
                        "--dates",
                        "2026-03-13",
                        "--competitions",
                        "male",
                        "--stage",
                        "data",
                    ]
                )

        rendered = stderr.getvalue()
        self.assertEqual(status, 2)
        self.assertIn("类别：本地文件权限错误", rendered)
        self.assertIn("阶段：logging", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_logging_only_writes_to_console(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_directory = root / "runs" / "auto_preview" / "2026-04-11_female"
            stderr = StringIO()
            with redirect_stderr(stderr):
                logger = configure_logging(
                    run_directory,
                    project_root=root,
                )
                logger.warning("⚠ [1/3] 测试日志：%s", run_directory)
            for handler in logger.handlers:
                handler.flush()
            content = stderr.getvalue()
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            self.assertFalse(run_directory.exists())
        self.assertIn("[1/3] 测试日志", content)
        self.assertIn(
            str(Path("runs") / "auto_preview" / "2026-04-11_female"),
            content,
        )
        self.assertNotIn(str(root.resolve()), content)
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

        rendered = "\n".join(failure_lines(error, log_path=Path("pipeline.log")))

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
        self.assertIn(
            "--override 仅用于重新查询并覆盖 source 和正文 Markdown",
            local,
        )
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
        self.articles: list[tuple[Article, ...]] = []

    async def create_draft(
        self, article: Article | tuple[Article, ...]
    ) -> DraftReceipt:
        articles = (article,) if isinstance(article, Article) else tuple(article)
        self.articles.append(articles)
        return DraftReceipt(
            media_id=f"draft-{len(self.articles)}",
            content_fingerprint=articles[0].content_fingerprint,
            created_at=datetime(2026, 7, 16, len(self.articles), tzinfo=UTC),
        )


class _FakeWeather:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []
        self.failures: dict[date, WeatherNetworkError] = {}
        self.conditions: dict[date, str] = {}

    async def get_weather(self, adcode: str, target_date: date) -> DailyWeather:
        self.calls.append((adcode, target_date))
        if target_date in self.failures:
            raise self.failures[target_date]
        return DailyWeather(
            adcode=adcode,
            region_name="海淀区",
            forecast_date=target_date,
            condition=self.conditions.get(target_date, "多云"),
            low_c=10,
            high_c=20,
            wind_direction="微风",
            wind_level="≤3级",
            report_time=datetime(2026, 4, 10, 18, tzinfo=SHANGHAI),
        )


class PipelineStateTests(unittest.IsolatedAsyncioTestCase):
    def _root(self, directory: str, *, with_global_inputs: bool = True) -> Path:
        root = Path(directory)
        template_directory = root / "templates" / "qhly_preview_v1"
        template_directory.mkdir(parents=True)
        shutil.copyfile(
            _PROJECT_ROOT / "templates" / "qhly_preview_v1" / "template.html",
            template_directory / "template.html",
        )
        if not with_global_inputs:
            return root
        inputs_directory = root / "runs" / "auto_preview"
        inputs_directory.mkdir(parents=True)
        (inputs_directory / "weather.json").write_text(
            json.dumps(
                {
                    "2026-04-11": {
                        "condition": None,
                        "low_c": None,
                        "high_c": None,
                        "wind_direction": None,
                        "wind_level": None,
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (inputs_directory / "config.json").write_text(
            json.dumps(
                {
                    "editors": ["测试编辑"],
                    "reviewers": ["测试责编"],
                    "approvers": ["测试审核"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return root

    def _pipeline(
        self,
        root: Path,
        *,
        queries: _FakeQueries | None = None,
        weather: _FakeWeather | None = None,
        wechat: _FakeWechat | None = None,
    ) -> tuple[AutoPreviewPipeline, _FakeQueries, _ListHandler]:
        queries = queries or _FakeQueries()
        logger, handler = _logger()
        runner = AutoPreviewPipeline(
            project_root=root,
            query_service_factory=lambda: _Context(queries),
            weather_service_factory=(
                None if weather is None else lambda: _Context(weather)
            ),
            wechat_service_factory=(
                None if wechat is None else lambda: _Context(wechat)
            ),
            logger=logger,
        )
        return runner, queries, handler

    @staticmethod
    def _ensure_weather_dates(root: Path, *days: date) -> None:
        path = root / "runs" / "auto_preview" / "weather.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for day in days:
            payload[day.isoformat()] = {
                "condition": None,
                "low_c": None,
                "high_c": None,
                "wind_direction": None,
                "wind_level": None,
            }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _request(
        stage: Stage,
        *,
        override: bool = False,
    ) -> PipelineRequest:
        return PipelineRequest(
            preview_dates=(datetime(2026, 4, 11).date(),),
            competitions=(Competition.FEMALE,),
            stage=stage,
            override=override,
        )

    async def test_weather_refresh_is_deduplicated_and_uses_fixed_haidian_adcode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            weather_service = _FakeWeather()
            runner, _, _ = self._pipeline(
                root,
                queries=queries,
                weather=weather_service,
            )
            request = PipelineRequest(
                (date(2026, 4, 11),),
                (Competition.MALE, Competition.FEMALE),
                stage=Stage.DATA,
            )

            with patch.dict(
                os.environ,
                {"AMAP_WEATHER_ADCODE": "999999"},
                clear=False,
            ):
                result = await runner.run(request)

            self.assertEqual(result.status, "ok")
            self.assertEqual(
                weather_service.calls,
                [("110108", date(2026, 4, 11))],
            )
            payload = json.loads(
                (
                    root / "runs" / "auto_preview" / "weather.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["2026-04-11"],
                {
                    "condition": "多云",
                    "low_c": 10,
                    "high_c": 20,
                    "wind_direction": "微风",
                    "wind_level": "≤3级",
                },
            )

    async def test_complete_weather_skips_query_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            weather_path = root / "runs" / "auto_preview" / "weather.json"
            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-11": {
                            "condition": "晴",
                            "low_c": 8,
                            "high_c": 18,
                            "wind_direction": "东风",
                            "wind_level": "4级",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            before = weather_path.read_bytes()
            weather_service = _FakeWeather()
            runner, _, _ = self._pipeline(root, weather=weather_service)

            await runner.run(self._request(Stage.DATA))

            self.assertEqual(weather_service.calls, [])
            self.assertEqual(weather_path.read_bytes(), before)

    async def test_override_refreshes_complete_weather(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            weather_path = root / "runs" / "auto_preview" / "weather.json"
            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-11": {
                            "condition": "旧天气",
                            "low_c": 1,
                            "high_c": 2,
                            "wind_direction": "北风",
                            "wind_level": "4级",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            weather_service = _FakeWeather()
            weather_service.conditions[date(2026, 4, 11)] = "晴"
            runner, _, _ = self._pipeline(root, weather=weather_service)

            await runner.run(self._request(Stage.DATA, override=True))

            payload = json.loads(weather_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["2026-04-11"]["condition"], "晴")
            self.assertEqual(payload["2026-04-11"]["low_c"], 10)
            self.assertEqual(len(weather_service.calls), 1)

    async def test_weather_failure_preserves_existing_or_null_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            weather_path = root / "runs" / "auto_preview" / "weather.json"
            weather_service = _FakeWeather()
            weather_service.failures[date(2026, 4, 11)] = WeatherNetworkError(
                "weather request failed",
                stage="http",
                retryable=True,
            )
            runner, _, handler = self._pipeline(root, weather=weather_service)
            placeholder_before = weather_path.read_bytes()

            await runner.run(self._request(Stage.DATA))

            self.assertEqual(weather_path.read_bytes(), placeholder_before)
            self.assertTrue(
                any("保留全 null 占位" in message for message in handler.messages)
            )

            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-11": {
                            "condition": "多云",
                            "low_c": 8,
                            "high_c": 18,
                            "wind_direction": "东风",
                            "wind_level": "4级",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            existing_before = weather_path.read_bytes()
            await runner.run(self._request(Stage.DATA, override=True))

            self.assertEqual(weather_path.read_bytes(), existing_before)
            self.assertTrue(any("保留已有天气" in message for message in handler.messages))

    async def test_existing_invalid_source_errors_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.DATA)
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
            wechat = _FakeWechat()
            runner, queries, _ = self._pipeline(root, wechat=wechat)
            request = self._request(Stage.PUBLISH, override=True)

            first = await runner.run(request)
            self.assertEqual(first.draft_media_id, "draft-1")
            article = Article.load(first.article_directory)
            self.assertIsInstance(article.cover, CoverFile)
            self.assertEqual(article.cover.path.name, "cover.png")
            self.assertEqual(article.digest, "马杯前瞻")

            reused = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.PUBLISH,
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
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.PUBLISH,
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
            self.assertEqual(history["schema_version"], 2)
            self.assertEqual(len(history["receipts"][-1]["articles"]), 1)

    async def test_batch_phase_barriers_order_and_single_publish_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            wechat = _FakeWechat()
            runner, _, handler = self._pipeline(
                root,
                queries=queries,
                wechat=wechat,
            )
            events: list[str] = []
            query_data_group = runner._query_data_group
            prepare_article = runner._prepare_article
            publish_articles = runner._publish_articles

            async def traced_data_group(*args, **kwargs):
                events.append("data-group")
                return await query_data_group(*args, **kwargs)

            def traced_article(*args, **kwargs):
                events.append("article")
                return prepare_article(*args, **kwargs)

            async def traced_publish(*args, **kwargs):
                events.append("publish")
                return await publish_articles(*args, **kwargs)

            request = PipelineRequest(
                preview_dates=(date(2026, 4, 11),),
                competitions=(Competition.FEMALE, Competition.MALE),
                stage=Stage.PUBLISH,
            )
            with (
                patch.object(
                    runner,
                    "_query_data_group",
                    side_effect=traced_data_group,
                ),
                patch.object(runner, "_prepare_article", side_effect=traced_article),
                patch.object(
                    runner,
                    "_publish_articles",
                    side_effect=traced_publish,
                ),
            ):
                result = await runner.run(request)

            self.assertEqual(
                events,
                ["data-group", "data-group", "article", "article", "publish"],
            )
            self.assertEqual(
                [run.competition for run in result.runs],
                [Competition.MALE, Competition.FEMALE],
            )
            self.assertEqual(len(wechat.articles), 1)
            self.assertEqual(len(wechat.articles[0]), 2)
            self.assertEqual(
                [article.content_fingerprint for article in wechat.articles[0]],
                [
                    Article.load(run.article_directory).content_fingerprint
                    for run in result.runs
                    if run.article_directory is not None
                ],
            )
            self.assertEqual(
                {run.draft_media_id for run in result.runs},
                {result.draft_media_id},
            )
            started = [
                message
                for message in handler.messages
                if message.startswith("▶ [3/3] publish")
            ]
            completed = [
                message
                for message in handler.messages
                if message.startswith("✓ [3/3] publish")
            ]
            self.assertEqual(
                started,
                [
                    "▶ [3/3] publish 加入 "
                    "2026-04-11 / male、2026-04-11 / female"
                ],
            )
            self.assertEqual(len(completed), 1)
            self.assertTrue(completed[0].endswith("：draft-1"))

    async def test_batch_data_queries_once_per_competition_and_logs_stages_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            runner, _, handler = self._pipeline(root, queries=queries)
            request = PipelineRequest(
                (date(2026, 4, 12), date(2026, 4, 11)),
                (Competition.FEMALE, Competition.MALE),
                stage=Stage.ARTICLE,
                override=True,
            )

            result = await runner.run(request)

            self.assertEqual(result.status, "ok")
            self.assertEqual(len(queries.game_queries), 2)
            self.assertTrue(
                all(query.match_date is None for query in queries.game_queries)
            )
            messages = handler.messages
            self.assertEqual(
                sum(message.startswith("⚠ --override") for message in messages),
                1,
            )
            for marker in (
                "▶ [1/3] data",
                "✓ [1/3] data",
                "▶ [2/3] article",
                "✓ [2/3] article",
            ):
                self.assertEqual(
                    sum(message.startswith(marker) for message in messages),
                    1,
                    marker,
                )
            self.assertFalse(any("已生成" in message for message in messages))
            self.assertFalse(any("人工数据" in message for message in messages))

            source_log = next(
                message for message in messages if message.startswith("源数据：\n")
            )
            markdown_log = next(
                message
                for message in messages
                if message.startswith("正文 Markdown：\n")
            )
            self.assertEqual(source_log.count("source.json"), 4)
            self.assertEqual(markdown_log.count("previews"), 4)
            self.assertEqual(
                sum(
                    "2026-04-11 自动查询海淀天气失败" in message
                    for message in messages
                ),
                1,
            )
            self.assertEqual(
                sum(
                    "2026-04-12 自动查询海淀天气失败" in message
                    for message in messages
                ),
                1,
            )
            data_completed = next(
                index
                for index, message in enumerate(messages)
                if message.startswith("✓ [1/3] data")
            )
            first_placeholder_warning = next(
                index
                for index, message in enumerate(messages)
                if "标题尚未填写" in message
            )
            self.assertLess(data_completed, first_placeholder_warning)

    async def test_no_games_is_cached_and_override_or_scope_change_requeries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            day = date(2026, 4, 11)
            queries.no_games.add((day, (123,)))
            runner, _, _ = self._pipeline(root, queries=queries)
            request = PipelineRequest(
                (day,),
                (Competition.FEMALE,),
                stage=Stage.DATA,
            )

            first = await runner.run(request)
            cached = await runner.run(request)
            refreshed = await runner.run(
                PipelineRequest(
                    (day,),
                    (Competition.FEMALE,),
                    stage=Stage.DATA,
                    override=True,
                )
            )
            self.assertEqual(len(queries.game_queries), 2)
            self.assertEqual(first.status, "skipped")
            self.assertEqual(cached.runs[0].reason, "no_games")
            self.assertEqual(refreshed.status, "skipped")

            config = competition_config(Competition.FEMALE)
            changed_scope = replace(
                config,
                current_tournament_ids=(123, 999),
            )
            queries.no_games.add((day, changed_scope.current_tournament_ids))
            with patch(
                "auto_preview.service.competition_config",
                return_value=changed_scope,
            ):
                changed = await runner.run(request)

            self.assertEqual(changed.status, "skipped")
            self.assertEqual(len(queries.game_queries), 3)
            state = json.loads(
                (changed.runs[0].run_directory / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["schema_version"], 3)
            self.assertEqual(state["source"]["status"], "no_games")
            self.assertEqual(state["source"]["preview_date"], "2026-04-11")
            self.assertEqual(state["source"]["competition"], "female")
            self.assertEqual(state["source"]["selected_games"], [])
            self.assertEqual(len(state["source"]["query_scope_sha256"]), 64)

    async def test_partial_no_games_skips_only_that_combination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            wechat = _FakeWechat()
            day = date(2026, 4, 11)
            queries.no_games.add((day, (123,)))
            runner, _, _ = self._pipeline(root, queries=queries, wechat=wechat)

            result = await runner.run(
                PipelineRequest(
                    (day,),
                    (Competition.MALE, Competition.FEMALE),
                    stage=Stage.PUBLISH,
                )
            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(
                [(run.competition, run.status) for run in result.runs],
                [
                    (Competition.MALE, "ok"),
                    (Competition.FEMALE, "skipped"),
                ],
            )
            self.assertEqual(result.runs[1].reason, "no_games")
            self.assertIsNone(result.runs[1].source_path)
            self.assertEqual(len(wechat.articles), 1)
            self.assertEqual(len(wechat.articles[0]), 1)

    async def test_batch_draft_reuse_requires_all_matching_histories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            wechat = _FakeWechat()
            runner, _, _ = self._pipeline(root, queries=queries, wechat=wechat)
            request = PipelineRequest(
                (date(2026, 4, 11),),
                (Competition.MALE, Competition.FEMALE),
                stage=Stage.PUBLISH,
            )

            first = await runner.run(request)
            reused = await runner.run(request)
            self.assertEqual(reused.draft_media_id, first.draft_media_id)
            self.assertEqual(len(wechat.articles), 1)

            missing_history = first.runs[1].run_directory / "draft.json"
            missing_history.unlink()
            rebuilt = await runner.run(request)

            self.assertEqual(rebuilt.draft_media_id, "draft-2")
            self.assertEqual(len(wechat.articles), 2)
            for run in rebuilt.runs:
                history = json.loads(
                    (run.run_directory / "draft.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    history["receipts"][-1]["media_id"],
                    rebuilt.draft_media_id,
                )

    async def test_publish_rejects_more_than_eight_ready_combinations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            days = (
                date(2026, 4, 11),
                date(2026, 4, 12),
                date(2026, 4, 13),
            )
            self._ensure_weather_dates(root, *days)
            queries = _BatchQueries()
            wechat = _FakeWechat()
            runner, _, _ = self._pipeline(root, queries=queries, wechat=wechat)

            with self.assertRaises(PipelineError) as caught:
                await runner.run(
                    PipelineRequest(
                        days,
                        tuple(Competition),
                        stage=Stage.PUBLISH,
                    )
                )

            self.assertEqual(caught.exception.stage, "publish-validation")
            self.assertEqual(len(queries.game_queries), 3)
            self.assertEqual(wechat.articles, [])

    async def test_legacy_run_and_single_draft_schemas_are_upgraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _BatchQueries()
            wechat = _FakeWechat()
            runner, _, _ = self._pipeline(root, queries=queries, wechat=wechat)
            request = PipelineRequest(
                (date(2026, 4, 11),),
                (Competition.FEMALE,),
                stage=Stage.PUBLISH,
            )
            created = await runner.run(request)
            run = created.runs[0]
            state_path = run.run_directory / "run.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 2
            state["source"] = {
                "selected_games": state["source"]["selected_games"],
                "accepted_placeholder_sha256": state["source"][
                    "accepted_placeholder_sha256"
                ],
            }
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            draft_path = run.run_directory / "draft.json"
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
            receipt = draft["receipts"][-1]
            component = receipt["articles"][0]
            draft_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "receipts": [
                            {
                                "media_id": receipt["media_id"],
                                "created_at": receipt["created_at"],
                                "article_fingerprint": component["article_fingerprint"],
                                "cover_fingerprint": component["cover_fingerprint"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            reused = await runner.run(request)

            self.assertEqual(reused.draft_media_id, created.draft_media_id)
            self.assertEqual(len(queries.game_queries), 1)
            self.assertEqual(len(wechat.articles), 1)
            upgraded_state = json.loads(state_path.read_text(encoding="utf-8"))
            upgraded_draft = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertEqual(upgraded_state["schema_version"], 3)
            self.assertEqual(upgraded_state["source"]["status"], "ready")
            self.assertEqual(upgraded_draft["schema_version"], 2)
            self.assertEqual(
                upgraded_draft["receipts"][0]["articles"][0]["competition"],
                "female",
            )

    async def test_placeholders_warn_and_continue_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)
            request = self._request(Stage.ARTICLE)

            initial = await runner.run(request)
            self.assertEqual(initial.status, "ok")
            self.assertTrue(initial.article_directory.is_dir())
            self.assertTrue(
                any("标题尚未填写" in message for message in handler.messages)
            )
            self.assertTrue(
                any("需要补充前瞻内容、作者" in message for message in handler.messages)
            )

            raw = json.loads(initial.source_path.read_text(encoding="utf-8"))

            def fill(value):
                if isinstance(value, str) and value.startswith("【待填写"):
                    return "已填写"
                if isinstance(value, list):
                    return [fill(item) for item in value]
                if isinstance(value, dict):
                    return {key: fill(item) for key, item in value.items()}
                return value

            initial.source_path.write_text(
                json.dumps(fill(raw), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for preview in raw["previews"].values():
                (initial.run_directory / preview["article_file"]).write_text(
                    "已填写的前瞻正文。\n",
                    encoding="utf-8",
                )
            resumed = await runner.run(request)

            self.assertEqual(resumed.status, "ok")
            self.assertTrue(resumed.article_directory.is_dir())
            self.assertEqual(len(queries.game_queries), 1)

    async def test_edited_source_rebuilds_article_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)
            override_request = self._request(Stage.ARTICLE, override=True)
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
                    override_request.preview_dates,
                    override_request.competitions,
                    stage=Stage.ARTICLE,
                )
            )

            article = Article.load(rebuilt.article_directory)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("人工修改后的标题", article.title)
            self.assertNotEqual(
                article.content_fingerprint, before_article.content_fingerprint
            )
            self.assertEqual(len(state["article"]["input_sha256"]), 64)
            self.assertNotIn("source_sha256", state["article"])
            self.assertEqual(
                state["article"]["cover"]["sha256"],
                sha256_file(article.cover.path),
            )
            self.assertEqual(len(queries.game_queries), 1)
            self.assertTrue(
                any(
                    "source、正文 Markdown、天气或人员配置已变化" in message
                    for message in handler.messages
                )
            )

    async def test_edited_markdown_rebuilds_article_without_querying_again(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)
            request = self._request(Stage.ARTICLE, override=True)
            complete = await runner.run(request)
            before = Article.load(complete.article_directory)
            raw = json.loads(complete.source_path.read_text(encoding="utf-8"))
            preview = next(iter(raw["previews"].values()))
            markdown_path = complete.run_directory / preview["article_file"]
            markdown_path.write_text(
                "第一段直接粘贴的正文。\n\n第二段直接粘贴的正文。\n",
                encoding="utf-8",
            )

            rebuilt = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )

            article = Article.load(rebuilt.article_directory)
            self.assertIn("第一段直接粘贴的正文。", article.body_html)
            self.assertIn("第二段直接粘贴的正文。", article.body_html)
            self.assertNotEqual(
                article.content_fingerprint,
                before.content_fingerprint,
            )
            self.assertEqual(len(queries.game_queries), 1)
            self.assertTrue(
                any(
                    "source、正文 Markdown、天气或人员配置已变化" in message
                    for message in handler.messages
                )
            )
            await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )

    async def test_missing_markdown_is_not_recreated_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)
            request = self._request(Stage.DATA)
            complete = await runner.run(request)
            raw = json.loads(complete.source_path.read_text(encoding="utf-8"))
            preview = next(iter(raw["previews"].values()))
            markdown_path = complete.run_directory / preview["article_file"]
            markdown_path.unlink()

            with self.assertRaises(ArtifactValidationError) as caught:
                await runner.run(request)

            self.assertIn("无法读取 Markdown 文件", str(caught.exception))
            self.assertFalse(markdown_path.exists())
            self.assertEqual(len(queries.game_queries), 1)

    async def test_invalid_article_is_rebuilt_without_querying_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.ARTICLE, override=True)
            complete = await runner.run(request)
            (complete.article_directory / "article.json").write_text(
                "{broken", encoding="utf-8"
            )

            rebuilt = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )

            self.assertIsInstance(Article.load(rebuilt.article_directory), Article)
            self.assertEqual(len(queries.game_queries), 1)

    async def test_changed_template_rebuilds_article_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)
            request = self._request(Stage.ARTICLE, override=True)
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
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )

            article = Article.load(rebuilt.article_directory)
            self.assertIn("模板变化测试标记", article.body_html)
            self.assertNotEqual(article.content_fingerprint, before.content_fingerprint)
            self.assertEqual(len(queries.game_queries), 1)
            self.assertTrue(
                any("模板指纹已变化" in message for message in handler.messages)
            )

    async def test_data_writes_template_source_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, handler = self._pipeline(root)

            result = await runner.run(self._request(Stage.DATA))
            raw = json.loads(result.source_path.read_text(encoding="utf-8"))

            self.assertEqual(
                list(raw),
                [
                    "column",
                    "preview_date",
                    "headline",
                    "previews",
                    "matches",
                ],
            )
            self.assertNotIn("weather", raw)
            self.assertNotIn("credits", raw)
            match = raw["matches"][0]
            key = f"{match['home']['short_name']} vs {match['away']['short_name']}"
            self.assertEqual(list(raw["previews"]), [key])
            self.assertEqual(set(raw["previews"][key]), {"article_file", "authors"})
            article_reference = raw["previews"][key]["article_file"]
            self.assertEqual(
                article_reference,
                f"previews/{match['home']['short_name']}vs{match['away']['short_name']}.md",
            )
            article_path = result.run_directory / article_reference
            self.assertTrue(article_path.is_file())
            self.assertIn("【待填写", article_path.read_text(encoding="utf-8"))
            self.assertNotIn("preview_paragraphs", match)
            self.assertNotIn("writers", match)
            self.assertTrue(
                any(
                    "自动查询海淀天气失败" in message
                    and "保留全 null 占位" in message
                    for message in handler.messages
                )
            )
            self.assertTrue(
                any("标题尚未填写" in message for message in handler.messages)
            )
            self.assertTrue(
                any("需要补充前瞻内容、作者" in message for message in handler.messages)
            )
            self.assertFalse(
                any(str(root.resolve()) in message for message in handler.messages)
            )
            self.assertTrue(
                any(
                    str(Path("runs") / "auto_preview") in message
                    for message in handler.messages
                )
            )

    async def test_missing_global_files_warn_and_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory, with_global_inputs=False)
            runner, queries, handler = self._pipeline(root)
            request = self._request(Stage.ARTICLE)

            result = await runner.run(request)

            self.assertEqual(result.status, "ok")
            inputs = root / "runs" / "auto_preview"
            self.assertEqual(
                json.loads((inputs / "weather.json").read_text(encoding="utf-8")),
                {
                    "2026-04-11": {
                        "condition": None,
                        "low_c": None,
                        "high_c": None,
                        "wind_direction": None,
                        "wind_level": None,
                    }
                },
            )
            self.assertEqual(
                json.loads((inputs / "config.json").read_text(encoding="utf-8")),
                {
                    "editors": ["【待填写：编辑】"],
                    "reviewers": ["【待填写：责编】"],
                    "approvers": ["【待填写：审核】"],
                },
            )
            self.assertTrue(result.article_directory.is_dir())
            self.assertEqual(len(queries.game_queries), 1)
            self.assertEqual(
                sum(
                    "需要补充编辑、责编、审核" in message
                    for message in handler.messages
                ),
                1,
            )
            self.assertTrue(
                any("自动查询海淀天气失败" in message for message in handler.messages)
            )
            self.assertTrue(
                any("标题尚未填写" in message for message in handler.messages)
            )
            self.assertTrue(
                any("需要补充前瞻内容、作者" in message for message in handler.messages)
            )
            self.assertFalse(
                any(str(root.resolve()) in message for message in handler.messages)
            )

            (inputs / "config.json").write_text(
                json.dumps(
                    {
                        "editors": ["编辑"],
                        "reviewers": ["责编"],
                        "approvers": ["审核"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            resumed = await runner.run(request)
            self.assertEqual(resumed.status, "ok")
            self.assertEqual(len(queries.game_queries), 1)
            self.assertEqual(
                sum(
                    "需要补充编辑、责编、审核" in message
                    for message in handler.messages
                ),
                1,
            )

    async def test_missing_weather_date_is_added_but_does_not_pause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            inputs = root / "runs" / "auto_preview"
            (inputs / "weather.json").write_text(
                json.dumps(
                    {
                        "2026-04-10": {
                            "condition": "晴",
                            "low_c": 6,
                            "high_c": 16,
                            "wind_direction": "东风",
                            "wind_level": "2级",
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            runner, queries, handler = self._pipeline(root)

            result = await runner.run(self._request(Stage.ARTICLE))

            self.assertEqual(result.status, "ok")
            weather = json.loads((inputs / "weather.json").read_text(encoding="utf-8"))
            self.assertEqual(
                weather["2026-04-11"],
                {
                    "condition": None,
                    "low_c": None,
                    "high_c": None,
                    "wind_direction": None,
                    "wind_level": None,
                },
            )
            article = Article.load(result.article_directory)
            self.assertIn("待更新", article.body_html)
            self.assertTrue(
                any(
                    "2026-04-11 自动查询海淀天气失败" in message
                    for message in handler.messages
                )
            )

    async def test_existing_empty_config_only_blocks_article_or_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            inputs = root / "runs" / "auto_preview"
            (inputs / "config.json").write_text(
                json.dumps(
                    {"editors": [], "reviewers": [], "approvers": []},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            runner, queries, _ = self._pipeline(root)
            day = datetime(2026, 4, 11).date()

            data = await runner.run(
                PipelineRequest((day,), (Competition.FEMALE,), stage=Stage.DATA)
            )
            with self.assertRaises(ArtifactValidationError) as caught:
                await runner.run(
                    PipelineRequest(
                        (day,),
                        (Competition.FEMALE,),
                        stage=Stage.ARTICLE,
                    )
                )

            self.assertEqual(data.status, "ok")
            self.assertEqual(caught.exception.stage, "config-validation")
            self.assertIn("config.json.editors", str(caught.exception))
            self.assertEqual(len(queries.game_queries), 1)

    async def test_partial_weather_and_invalid_config_are_never_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            inputs = root / "runs" / "auto_preview"
            weather_path = inputs / "weather.json"
            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-11": {
                            "condition": None,
                            "low_c": 9,
                            "high_c": None,
                            "wind_direction": None,
                            "wind_level": None,
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            weather_before = weather_path.read_bytes()
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.ARTICLE)

            with self.assertRaises(ArtifactValidationError) as weather_error:
                await runner.run(request)
            self.assertEqual(weather_error.exception.stage, "weather-validation")
            self.assertIn("high_c", str(weather_error.exception))
            self.assertIn("wind_direction", str(weather_error.exception))
            self.assertEqual(weather_path.read_bytes(), weather_before)

            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-10": {
                            "condition": None,
                            "low_c": 9,
                            "high_c": None,
                            "wind_direction": None,
                            "wind_level": None,
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            unrelated_invalid_before = weather_path.read_bytes()
            with self.assertRaises(ArtifactValidationError):
                await runner.run(request)
            self.assertEqual(weather_path.read_bytes(), unrelated_invalid_before)

            weather_path.write_text(
                json.dumps(
                    {
                        "2026-04-11": {
                            "condition": None,
                            "low_c": None,
                            "high_c": None,
                            "wind_direction": None,
                            "wind_level": None,
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            config_path = inputs / "config.json"
            config_path.write_text(
                '{"editors": ["编辑"], "reviewers": "错误", "approvers": ["审核"]}\n',
                encoding="utf-8",
            )
            config_before = config_path.read_bytes()
            with self.assertRaises(ArtifactValidationError) as config_error:
                await runner.run(request)
            self.assertEqual(config_error.exception.stage, "config-validation")
            self.assertIn("$config.reviewers", str(config_error.exception))
            self.assertEqual(config_path.read_bytes(), config_before)

    async def test_only_current_weather_and_config_affect_article_fingerprint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            inputs = root / "runs" / "auto_preview"
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.ARTICLE, override=True)
            first = await runner.run(request)
            first_article = Article.load(first.article_directory)
            first_state = json.loads(
                (first.run_directory / "run.json").read_text(encoding="utf-8")
            )

            weather_path = inputs / "weather.json"
            weather = json.loads(weather_path.read_text(encoding="utf-8"))
            weather["2026-04-12"] = {
                "condition": "晴",
                "low_c": 11,
                "high_c": 21,
                "wind_direction": "南风",
                "wind_level": "3级",
            }
            weather_path.write_text(
                json.dumps(weather, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            unchanged = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )
            unchanged_article = Article.load(unchanged.article_directory)
            unchanged_state = json.loads(
                (unchanged.run_directory / "run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                unchanged_article.content_fingerprint,
                first_article.content_fingerprint,
            )
            self.assertEqual(
                unchanged_state["article"]["input_sha256"],
                first_state["article"]["input_sha256"],
            )

            weather["2026-04-11"] = {
                "condition": "多云",
                "low_c": 8,
                "high_c": 18,
                "wind_direction": "东南风",
                "wind_level": "2级",
            }
            weather_path.write_text(
                json.dumps(weather, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            weather_changed = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )
            weather_article = Article.load(weather_changed.article_directory)
            self.assertNotEqual(
                weather_article.content_fingerprint,
                first_article.content_fingerprint,
            )

            config_path = inputs / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["editors"] = ["新编辑"]
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            config_changed = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.ARTICLE,
                )
            )
            config_article = Article.load(config_changed.article_directory)
            self.assertNotEqual(
                config_article.content_fingerprint,
                weather_article.content_fingerprint,
            )
            self.assertEqual(len(queries.game_queries), 1)

    async def test_override_preserves_weather_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            inputs = root / "runs" / "auto_preview"
            weather_before = (inputs / "weather.json").read_bytes()
            config_before = (inputs / "config.json").read_bytes()
            runner, queries, _ = self._pipeline(root)

            await runner.run(self._request(Stage.ARTICLE, override=True))

            self.assertEqual((inputs / "weather.json").read_bytes(), weather_before)
            self.assertEqual((inputs / "config.json").read_bytes(), config_before)

    async def test_override_rebuilds_markdown_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.DATA)
            first = await runner.run(request)
            raw = json.loads(first.source_path.read_text(encoding="utf-8"))
            preview = next(iter(raw["previews"].values()))
            markdown_path = first.run_directory / preview["article_file"]
            markdown_path.write_text("人工正文。\n", encoding="utf-8")
            stale_path = first.run_directory / "previews" / "旧比赛.md"
            stale_path.write_text("旧内容。\n", encoding="utf-8")

            rebuilt = await runner.run(
                PipelineRequest(
                    request.preview_dates,
                    request.competitions,
                    stage=Stage.DATA,
                    override=True,
                )
            )

            self.assertIn("【待填写", markdown_path.read_text(encoding="utf-8"))
            self.assertFalse(stale_path.exists())
            self.assertEqual(rebuilt.status, "ok")
            self.assertEqual(len(queries.game_queries), 2)

    async def test_run_state_version_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            runner, queries, _ = self._pipeline(root)
            request = self._request(Stage.DATA)
            created = await runner.run(request)
            state_path = created.run_directory / "run.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 1
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ArtifactValidationError) as state_error:
                await runner.run(request)
            self.assertIn("run.json 版本不受支持", str(state_error.exception))

    async def test_duplicate_short_matchup_fails_with_game_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            queries = _FakeQueries()
            queries.targets.append(
                _game(
                    501,
                    123,
                    datetime(2026, 4, 11, 19, 0, tzinfo=SHANGHAI),
                    status=GameStatus.SCHEDULED,
                )
            )
            runner, _, _ = self._pipeline(root, queries=queries)

            with self.assertRaises(PreviewValidationError) as caught:
                await runner.run(self._request(Stage.DATA))

            self.assertIn("对阵简称重复", str(caught.exception))
            self.assertIn("500", str(caught.exception))
            self.assertIn("501", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
