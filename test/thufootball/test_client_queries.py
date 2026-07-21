from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from thufootball import (
    BLACKLISTED_TOURNAMENT_IDS,
    AuthenticationError,
    BatchQueryError,
    ConfigurationError,
    DataConflict,
    GameQuery,
    GameStatus,
    InvalidResponse,
    MatchResult,
    QueryValidationError,
    SchemaError,
    THUFootballClient,
    THUFootballQueryService,
    Timeout,
)
from thufootball.mappers import (
    map_game_detail,
    map_game_summary,
    map_tournament_snapshot,
)


@dataclass(frozen=True)
class _LiveSmokeConfig:
    tournament_ids: tuple[int, ...]
    game_id: int | None
    match_date: date | None
    team_id: int | None
    opponent_id: int | None
    include_unfinished: bool
    full_output: bool
    outcomes: bool


_LIVE_SMOKE_CONFIG: _LiveSmokeConfig | None = None


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _positive_cli_id(raw: str) -> int:
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if parsed <= 0 or str(parsed) != raw:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def _cli_date(raw: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须使用 YYYY-MM-DD 格式") from exc
    if parsed.isoformat() != raw:
        raise argparse.ArgumentTypeError("必须使用 YYYY-MM-DD 格式")
    return parsed


def _game(
    game_id: int = 1001,
    tournament_id: int = 10,
    *,
    kickoff: str = "2026-07-14 08:00:00",
    started: bool = False,
    ended: bool = False,
    active: bool = True,
    valid: int = 1,
    home_team_id: int = 101,
    away_team_id: int = 202,
    home_tournament_team_id: int = 1101,
    away_tournament_team_id: int = 1202,
    home_goal: object = 2,
    away_goal: object = 1,
    penalty_shootout: int = 0,
    home_penalty: object = None,
    away_penalty: object = None,
    home_abandon: object = None,
    away_abandon: object = None,
) -> dict[str, Any]:
    return {
        "id": game_id,
        "tourn_id": tournament_id,
        "tourn_info": {
            "id": tournament_id,
            "name": f"赛事{tournament_id}",
            "brief_name": f"赛{tournament_id}",
        },
        "time": kickoff,
        "start": started,
        "end": ended,
        "status": active,
        "valid": valid,
        "stage": "小组赛",
        "group_name": "A",
        "round": 1,
        "home_tourn_team_id": home_tournament_team_id,
        "away_tourn_team_id": away_tournament_team_id,
        "home_tourn_team_info": {
            "id": home_tournament_team_id,
            "team_id": home_team_id,
            "name": f"主队{home_team_id}",
            "brief_name": f"主{home_team_id}",
        },
        "away_tourn_team_info": {
            "id": away_tournament_team_id,
            "team_id": away_team_id,
            "name": f"客队{away_team_id}",
            "brief_name": f"客{away_team_id}",
        },
        "home_goal": home_goal,
        "away_goal": away_goal,
        "result": f"{home_goal}:{away_goal}",
        "penalty_shootout": penalty_shootout,
        "home_penalty": home_penalty,
        "away_penalty": away_penalty,
        "home_abandon": home_abandon,
        "away_abandon": away_abandon,
        "field_info": {"id": 1, "name": "测试球场", "brief_name": "测试场"},
    }


def _team(
    tournament_id: int,
    tournament_team_id: int,
    team_id: int,
    *,
    rank: int = 0,
) -> dict[str, Any]:
    return {
        "id": tournament_team_id,
        "tourn_id": tournament_id,
        "team_id": team_id,
        "name": f"球队{team_id}",
        "brief_name": f"队{team_id}",
        "group_place": "A1",
        "win": 1,
        "draw": 2,
        "lose": 3,
        "goal": 4,
        "concede": 5,
        "point": 6,
        "rank": rank,
    }


def _tournament_payload(
    tournament_id: int,
    games: list[dict[str, Any]] | None = None,
    *,
    players_per_side: int = 11,
) -> dict[str, Any]:
    return {
        "success": True,
        "info": "ok",
        "tourn_info": {
            "id": tournament_id,
            "name": f"赛事{tournament_id}",
            "brief_name": f"赛{tournament_id}",
            "season": "2025~2026",
            "begin": "2025-09-01",
            "end": "2026-08-01",
            "players": players_per_side,
        },
        "season_ids": {"2025~2026": tournament_id},
        "registered_teams": [
            _team(tournament_id, tournament_id * 100 + 1, tournament_id * 10 + 1),
            _team(
                tournament_id,
                tournament_id * 100 + 2,
                tournament_id * 10 + 2,
                rank=2,
            ),
        ],
        "games": games or [],
    }


def _detail_payload() -> dict[str, Any]:
    payload = {
        "success": True,
        "info": "ok",
        "game_info": _game(1001, 10, started=True, ended=True),
        "tourn_info": {"id": 10, "name": "详情赛事", "brief_name": "详情赛"},
        "events": [
            {
                "id": 9001,
                "game_id": 1001,
                "tourn_team_id": 1101,
                "tourn_team_player_id": 7001,
                "player_id": 501,
                "name": "事件球员",
                "side": "HOME",
                "type": "GOAL",
                "time": 20,
                "stoppage_time": 1,
                "kitnum": 9,
                "during_penalty_shootout": 0,
                "valid": True,
            }
        ],
        "referees": [
            {
                "id": 8001,
                "game_id": 1001,
                "referee_id": 601,
                "position": "R",
                "name": "测试裁判",
                "fee": 999.0,
            }
        ],
        "home_tourn_team_players": [{"mobile": "18800000000"}],
        "comments": [{"content": "sensitive-comment"}],
        "officials": [{"session_key": "secret-session"}],
        "game_time_metadata": {"status": "START"},
    }
    return payload


class LiveSmokeTests(unittest.IsolatedAsyncioTestCase):
    """Real read-only smoke test, enabled only by this file's CLI entry point."""

    async def test_live_smoke(self) -> None:
        config = _LIVE_SMOKE_CONFIG
        if config is None:
            self.skipTest("真实冒烟仅在直接运行本文件时启用，离线发现测试不会访问 API")

        if config.outcomes:
            assert config.team_id is not None
            async with THUFootballClient(load_environment=False) as client:
                service = THUFootballQueryService(client)
                team_outcomes = await service.query_team_outcomes(
                    config.team_id,
                    config.tournament_ids or None,
                )
            if config.full_output:
                output = {
                    "query": {
                        "scope": "static_team_outcomes",
                        "tournament_ids": config.tournament_ids,
                        "team_id": config.team_id,
                    },
                    "team_outcomes": team_outcomes,
                }
            else:
                output = {
                    "query_scope": "static_team_outcomes",
                    "query_tournament_ids": config.tournament_ids,
                    "team_id": config.team_id,
                    "outcome_team_names": list(
                        dict.fromkeys(outcome.team_name for outcome in team_outcomes)
                    ),
                    "outcome_count": len(team_outcomes),
                    "team_outcomes": team_outcomes,
                }
            print(json.dumps(_jsonable(output), ensure_ascii=False, indent=2))
            return

        query_date = config.match_date
        if config.tournament_ids:
            query_scope = "specified_tournaments"
        elif query_date is not None:
            query_scope = "all_tournaments_on_date"
        else:
            query_scope = "all_accessible_tournaments"

        async with THUFootballClient() as client:
            probe = await client.get_user_info()
            tournaments = await client.get_accessible_tournaments()
            service = THUFootballQueryService(client)
            games = []
            team_matches = []
            team_to_team_matches = None
            if config.team_id is None:
                games = await service.query_games(
                    GameQuery(
                        tournament_ids=config.tournament_ids,
                        match_date=query_date,
                    )
                )
            elif config.opponent_id is None:
                team_matches = await service.query_team_matches(
                    config.team_id,
                    (config.tournament_ids[0] if config.tournament_ids else None),
                    include_unfinished=config.include_unfinished,
                )
            else:
                team_to_team_matches = await service.query_team_to_team_matches(
                    config.team_id,
                    config.opponent_id,
                    config.tournament_ids or None,
                    include_unfinished=config.include_unfinished,
                )
            detail = (
                await client.get_game_info(config.game_id)
                if config.game_id is not None
                else None
            )

        self.assertIsInstance(probe.user_registered, bool)
        self.assertIsInstance(tournaments, list)
        self.assertIsInstance(games, list)
        self.assertIsInstance(team_matches, list)
        if config.game_id is not None:
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail.game.game_id, config.game_id)

        if config.full_output:
            output: dict[str, object] = {
                "query": {
                    "scope": query_scope,
                    "tournament_ids": config.tournament_ids,
                    "game_id": config.game_id,
                    "match_date": query_date,
                    "team_id": config.team_id,
                    "opponent_id": config.opponent_id,
                    "include_unfinished": (
                        True if config.team_id is None else config.include_unfinished
                    ),
                },
                "user_probe": probe,
                "accessible_tournament_count": len(tournaments),
                "queryable_tournament_count": sum(
                    tournament.tournament_id not in BLACKLISTED_TOURNAMENT_IDS
                    for tournament in tournaments
                ),
                "games": games,
                "team_matches": team_matches,
                "team_to_team_matches": team_to_team_matches,
                "game_detail": detail,
            }
        else:
            output = {
                "authenticated": True,
                "user_registered": probe.user_registered,
                "accessible_tournament_count": len(tournaments),
                "queryable_tournament_count": sum(
                    tournament.tournament_id not in BLACKLISTED_TOURNAMENT_IDS
                    for tournament in tournaments
                ),
                "query_scope": query_scope,
                "query_tournament_ids": config.tournament_ids,
                "query_match_date": query_date,
                "query_game_count": len(games),
                "team_id": config.team_id,
                "opponent_id": config.opponent_id,
                "team_match_count": len(team_matches),
                "team_to_team_match_count": (
                    len(team_to_team_matches.matches)
                    if team_to_team_matches is not None
                    else 0
                ),
                "team_to_team_summary": (
                    team_to_team_matches.summary
                    if team_to_team_matches is not None
                    else None
                ),
                "requested_game_id": config.game_id,
                "detail_loaded": detail is not None,
                "detail_event_count": len(detail.events) if detail else 0,
                "detail_referee_count": len(detail.referees) if detail else 0,
            }
        print(json.dumps(_jsonable(output), ensure_ascii=False, indent=2))


class MapperTests(unittest.TestCase):
    def test_maps_time_ids_scores_and_all_statuses(self) -> None:
        now = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
        scheduled = map_game_summary(
            _game(kickoff="2026-07-15 00:00:00"), "game", now=now
        )
        started = map_game_summary(_game(started=True), "game", now=now)
        finished = map_game_summary(_game(started=True, ended=True), "game", now=now)
        unknown = map_game_summary(
            _game(active=False, started=True, ended=True), "game", now=now
        )

        self.assertEqual(scheduled.status, GameStatus.SCHEDULED)
        self.assertEqual(started.status, GameStatus.STARTED)
        self.assertEqual(finished.status, GameStatus.FINISHED)
        self.assertEqual(unknown.status, GameStatus.UNKNOWN)
        self.assertEqual(scheduled.kickoff_local.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(scheduled.home_tournament_team_id, 1101)
        self.assertEqual(scheduled.home_team_id, 101)
        self.assertEqual(scheduled.home_team_brief_name, "主101")
        self.assertEqual(scheduled.away_team_brief_name, "客202")
        self.assertEqual(scheduled.tournament_name, "赛事10")
        self.assertEqual(scheduled.home_score, 2)
        self.assertFalse(scheduled.penalty_shootout)
        self.assertIsNone(scheduled.away_abandon)

    def test_invalid_and_legacy_game_fields_fail_strictly(self) -> None:
        cases = (
            ("game.home_goal", {"home_goal": -1}),
            ("game.away_goal", {"away_goal": "2"}),
            ("game.valid", {"valid": None}),
            ("game.penalty_shootout", {"penalty_shootout": None}),
            ("game.home_penalty", {"home_penalty": -1}),
            ("game.away_penalty", {"away_penalty": "3"}),
            ("game.home_abandon", {"home_abandon": 2}),
        )
        for field_path, changes in cases:
            with self.subTest(field_path=field_path):
                with self.assertRaises(SchemaError) as caught:
                    map_game_summary(_game(**changes), "game")
                self.assertEqual(caught.exception.field_path, field_path)

    def test_inconsistent_finished_core_fields_fail_strictly(self) -> None:
        cases = (
            (
                "game.home_abandon",
                {"home_abandon": 1, "away_abandon": 1},
            ),
            ("game.home_goal", {"home_goal": None}),
        )
        for field_path, changes in cases:
            with self.subTest(field_path=field_path):
                with self.assertRaises(SchemaError) as caught:
                    map_game_summary(
                        _game(started=True, ended=True, **changes),
                        "game",
                    )
                self.assertEqual(caught.exception.field_path, field_path)

    def test_penalty_shootout_is_rule_flag_not_occurrence_flag(self) -> None:
        non_draw = map_game_summary(
            _game(
                started=True,
                ended=True,
                home_goal=5,
                away_goal=0,
                penalty_shootout=1,
                home_penalty=0,
                away_penalty=0,
            ),
            "game",
        )
        enabled_without_shootout = map_game_summary(
            _game(
                started=True,
                ended=True,
                home_goal=1,
                away_goal=1,
                penalty_shootout=1,
                home_penalty=0,
                away_penalty=0,
            ),
            "game",
        )
        disabled_with_penalty_scores = map_game_summary(
            _game(
                started=True,
                ended=True,
                home_goal=1,
                away_goal=1,
                penalty_shootout=0,
                home_penalty=4,
                away_penalty=3,
            ),
            "game",
        )
        decided_on_penalties = map_game_summary(
            _game(
                started=True,
                ended=True,
                home_goal=1,
                away_goal=1,
                penalty_shootout=1,
                home_penalty=4,
                away_penalty=3,
            ),
            "game",
        )

        self.assertTrue(non_draw.penalty_shootout)
        self.assertFalse(non_draw.decided_by_penalty_shootout)
        self.assertTrue(enabled_without_shootout.penalty_shootout)
        self.assertFalse(enabled_without_shootout.decided_by_penalty_shootout)
        self.assertFalse(disabled_with_penalty_scores.penalty_shootout)
        self.assertFalse(disabled_with_penalty_scores.decided_by_penalty_shootout)
        self.assertTrue(decided_on_penalties.penalty_shootout)
        self.assertTrue(decided_on_penalties.decided_by_penalty_shootout)

    def test_legacy_tournament_shapes_fail_strictly(self) -> None:
        cases = ("missing_team", "blank_season", "negative_counter")
        for scenario in cases:
            with self.subTest(scenario=scenario):
                game = _game(
                    1,
                    10,
                    home_tournament_team_id=1001,
                    away_tournament_team_id=1002,
                    home_team_id=101,
                    away_team_id=102,
                )
                payload = _tournament_payload(10, [game])
                if scenario == "missing_team":
                    game["home_tourn_team_info"] = None
                    expected = "games[0].home_tourn_team_info"
                elif scenario == "blank_season":
                    payload["season_ids"] = {"": 10}
                    expected = "season_ids.<key>"
                else:
                    payload["registered_teams"][0]["draw"] = -1
                    expected = "registered_teams[0].draw"
                with self.assertRaises(SchemaError) as caught:
                    map_tournament_snapshot(payload)
                self.assertEqual(caught.exception.field_path, expected)

    def test_game_detail_is_a_sensitive_field_whitelist(self) -> None:
        detail = map_game_detail(_detail_payload(), expected_game_id=1001)
        rendered = repr(asdict(detail))

        self.assertEqual(detail.events[0].side, "home")
        self.assertEqual(detail.game.tournament_name, "详情赛事")
        self.assertEqual(detail.referees[0].referee_id, 601)
        self.assertNotIn("18800000000", rendered)
        self.assertNotIn("sensitive-comment", rendered)
        self.assertNotIn("secret-session", rendered)
        self.assertNotIn("999.0", rendered)

    def test_missing_core_identity_reports_only_field_path(self) -> None:
        raw = _game()
        raw.pop("id")
        with self.assertRaises(SchemaError) as caught:
            map_game_summary(raw, "game")
        self.assertEqual(caught.exception.field_path, "game.id")
        self.assertNotIn(repr(raw), str(caught.exception))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_current_games_uses_params_and_maps_response(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"success": True, "current_games": [_game()]},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(load_environment=False, http_client=http)
            games = await client.get_current_games(
                history_bound=date(2026, 7, 13),
                future_bound=date(2026, 7, 15),
                field_id=3,
            )

        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].tournament_name, "赛事10")
        query = seen[0].url.params
        self.assertEqual(query["history_bound"], "2026-07-13")
        self.assertEqual(query["future_bound"], "2026-07-15")
        self.assertEqual(query["field_id"], "3")
        self.assertEqual(query["type"], "public")
        self.assertNotIn("openid", query)

    async def test_credentials_preserve_special_characters(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"success": True, "user_registered": True},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid-value",
                session_key="a+b/c==",
                http_client=http,
            )
            probe = await client.get_user_info()

        self.assertTrue(probe.user_registered)
        self.assertEqual(seen[0].url.params["session_key"], "a+b/c==")

    async def test_maps_tournament_catalog_snapshot_and_rank_zero(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("GetMyTournaments"):
                payload = {
                    "success": True,
                    "tourns": [
                        {
                            "id": 10,
                            "name": "完整赛事名",
                            "brief_name": "简称",
                            "season": "",
                            "begin": "2025-09-01",
                            "end": "2026-08-01",
                            "status": True,
                            "visible": False,
                        }
                    ],
                }
            else:
                game = _game(1001, 10, started=True, ended=True)
                payload = _tournament_payload(10, [game])
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            refs = await client.get_accessible_tournaments()
            snapshot = await client.get_tournament_info(10)

        self.assertEqual(refs[0].name, "完整赛事名")
        self.assertEqual(refs[0].season, "")
        self.assertFalse(refs[0].visible)
        self.assertEqual(snapshot.players_per_side, 11)
        self.assertIsNone(snapshot.teams[0].reported_rank)
        self.assertEqual(snapshot.teams[1].reported_rank, 2)
        self.assertEqual(snapshot.games[0].home_team_id, 101)
        self.assertEqual(snapshot.games[0].tournament_name, "赛事10")

    async def test_get_game_info_returns_minimal_detail(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_detail_payload(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            detail = await client.get_game_info(1001)

        self.assertEqual(detail.game.game_id, 1001)
        self.assertEqual(len(detail.events), 1)
        self.assertEqual(len(detail.referees), 1)

    async def test_retries_retryable_http_and_timeout_once(self) -> None:
        attempts = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(
                200,
                json={"success": True, "current_games": []},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(load_environment=False, http_client=http)
            self.assertEqual(await client.get_current_games(), [])
        self.assertEqual(attempts, 2)

        attempts = 0

        async def timeout_handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("timeout", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(timeout_handler)
        ) as http:
            client = THUFootballClient(load_environment=False, http_client=http)
            with self.assertRaises(Timeout):
                await client.get_current_games()
        self.assertEqual(attempts, 2)

    async def test_authentication_and_api_failures_do_not_retry(self) -> None:
        attempts = 0

        async def unauthorized(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(unauthorized)
        ) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            with self.assertRaises(AuthenticationError):
                await client.get_user_info()
        self.assertEqual(attempts, 1)

        async def api_failure(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"success": False, "info": "resource unavailable"},
                request=request,
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(api_failure)
        ) as http:
            client = THUFootballClient(load_environment=False, http_client=http)
            with self.assertRaises(InvalidResponse):
                await client.get_current_games()

    async def test_credentials_required_except_for_current_games(self) -> None:
        client = THUFootballClient(load_environment=False)
        try:
            with self.assertRaises(ConfigurationError):
                await client.get_user_info()
        finally:
            await client.aclose()

    async def test_blacklisted_tournament_is_rejected_without_request(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("blacklisted tournament must not be requested")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            with self.assertRaises(QueryValidationError):
                await client.get_tournament_info(6)

    async def test_blacklisted_games_are_not_returned_by_other_reads(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("GetCurrentGames"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "current_games": [_game(1, 6), _game(2, 10)],
                    },
                    request=request,
                )
            payload = _detail_payload()
            payload["game_info"]["tourn_id"] = 6
            payload["tourn_info"]["id"] = 6
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            games = await client.get_current_games()
            with self.assertRaises(QueryValidationError):
                await client.get_game_info(1001)

        self.assertEqual([game.game_id for game in games], [2])

    async def test_closes_only_owned_http_client(self) -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
        injected_client = THUFootballClient(load_environment=False, http_client=http)
        owned_client = THUFootballClient(load_environment=False)
        owned_http = owned_client._http_client

        await injected_client.aclose()
        await owned_client.aclose()

        self.assertFalse(http.is_closed)
        self.assertTrue(owned_http.is_closed)
        await http.aclose()


class StaticRankingDataTests(unittest.TestCase):
    def test_static_outcome_loader_maps_malformed_data_to_configuration_error(
        self,
    ) -> None:
        from thufootball.rankings import _load_teams

        with tempfile.TemporaryDirectory() as directory:
            notes_root = Path(directory)
            (notes_root / "teams.json").write_text(
                '{"测试学院": {"男足": [true], "女足": [], '
                '"五人制": [], "简称": "测试"}}\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                _load_teams(notes_root)

    def test_static_outcome_files_are_complete_and_audited(self) -> None:
        notes_root = _SRC_ROOT / "thufootball" / "notes"
        teams = json.loads((notes_root / "teams.json").read_text(encoding="utf-8"))
        tournaments = json.loads(
            (notes_root / "tourns.json").read_text(encoding="utf-8")
        )
        audit = json.loads(
            (notes_root / "identity_audit.json").read_text(encoding="utf-8")
        )

        self.assertEqual(len(tournaments), 14)
        self.assertEqual(len(teams), 53)
        self.assertEqual(
            teams["电子工程系"],
            {"男足": [34], "女足": [66], "五人制": [34], "简称": "电子"},
        )
        self.assertEqual(
            teams["新闻与传播学院-马克思主义学院"],
            {
                "男足": [2041, 253, 56, 1944],
                "女足": [2046, 253, 94],
                "五人制": [2041, 1944, 253],
                "简称": "新闻-马院",
            },
        )
        self.assertNotIn("新闻与传播学院-马克思注意学院", teams)
        self.assertEqual(
            teams["教育学院-至善书院"]["五人制"],
            [2051, 235, 293],
        )

        reverse_ids: dict[int, list[str]] = {}
        flat_team_names: set[str] = set()
        institution_by_id: dict[int, str] = {}
        for institution_name, team in teams.items():
            self.assertEqual(set(team), {"男足", "女足", "五人制", "简称"})
            self.assertIsInstance(team["简称"], str)
            self.assertTrue(team["简称"])
            for category in ("男足", "女足", "五人制"):
                team_ids = team[category]
                self.assertIsInstance(team_ids, list)
                self.assertEqual(len(team_ids), len(set(team_ids)))
                if not team_ids:
                    continue
                team_name = f"{institution_name}{category}"
                flat_team_names.add(team_name)
                for team_id in team_ids:
                    self.assertIsInstance(team_id, int)
                    self.assertNotIsInstance(team_id, bool)
                    self.assertGreater(team_id, 0)
                    owner = institution_by_id.setdefault(team_id, institution_name)
                    self.assertEqual(owner, institution_name)
                    reverse_ids.setdefault(team_id, []).append(team_name)

        self.assertEqual(len(reverse_ids), 109)
        self.assertEqual(
            teams["深圳国际研究生院"],
            {"男足": [], "女足": [], "五人制": [], "简称": "深研院"},
        )

        actual_shared = {
            team_id: team_names
            for team_id, team_names in reverse_ids.items()
            if len(team_names) > 1
        }
        audited_shared = {
            item["team_id"]: item["team_names"] for item in audit["shared_team_ids"]
        }
        self.assertEqual(audited_shared, actual_shared)
        self.assertEqual(len(audited_shared), 59)
        for item in audit["shared_team_ids"]:
            self.assertTrue(item["institution"])
            self.assertTrue(
                all(
                    item["institution"] in team_name for team_name in item["team_names"]
                )
            )

        expected_counts = {
            122: 16,
            124: 16,
            126: 17,
            123: 24,
            128: 47,
            99: 16,
            100: 16,
            101: 14,
            102: 22,
            111: 47,
            89: 16,
            88: 27,
            90: 23,
            93: 43,
        }
        rank_order = {
            "冠军": 0,
            "亚军": 1,
            "季军": 2,
            "升级": 3,
            "第四名": 4,
            "四强": 5,
            "八强": 6,
            "14强": 7,
            "16强": 8,
            "32强": 9,
            "44强": 10,
            "48强": 11,
            "小组第三": 12,
            "小组第四": 13,
            "小组第五": 14,
            "保级": 15,
            "降级": 16,
        }
        observed_labels: set[str] = set()
        for tournament_id in tournaments.values():
            ranks = json.loads(
                (notes_root / "ranks" / f"{tournament_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(ranks), expected_counts[tournament_id])
            self.assertTrue(set(ranks) <= flat_team_names)
            self.assertTrue(
                all(isinstance(rank, str) and rank for rank in ranks.values())
            )
            rank_priorities = [rank_order[rank] for rank in ranks.values()]
            self.assertEqual(rank_priorities, sorted(rank_priorities))
            observed_labels.update(ranks.values())

        ranks_101 = json.loads(
            (notes_root / "ranks" / "101.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ranks_101["新闻与传播学院-马克思主义学院男足"],
            "小组第三",
        )
        ranks_93 = json.loads(
            (notes_root / "ranks" / "93.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ranks_93["新闻与传播学院-马克思主义学院五人制"],
            "44强",
        )
        ranks_111 = json.loads(
            (notes_root / "ranks" / "111.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ranks_111["新闻与传播学院-马克思主义学院五人制"],
            "48强",
        )

        self.assertTrue(
            {
                "冠军",
                "亚军",
                "季军",
                "第四名",
                "四强",
                "八强",
                "14强",
                "16强",
                "32强",
                "44强",
                "48强",
                "小组第三",
                "小组第四",
                "小组第五",
                "保级",
                "降级",
                "升级",
            }
            <= observed_labels
        )


class QueryServiceTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def _service(
        self,
        handler,
        *,
        max_concurrency: int = 4,
    ) -> AsyncIterator[THUFootballQueryService]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid", session_key="session", http_client=http
            )
            yield THUFootballQueryService(client, max_concurrency=max_concurrency)

    async def test_from_environment_owns_and_closes_transport(self) -> None:
        with patch("thufootball.client.load_credentials", return_value=("", "")):
            service = THUFootballQueryService.from_environment()
        client = service._client

        async with service as entered:
            self.assertIs(entered, service)

        self.assertTrue(client._closed)

    async def test_date_only_uses_wide_bounds_and_beijing_filter(self) -> None:
        seen: list[httpx.Request] = []
        games = [
            _game(1, 10, kickoff="2026-07-13 15:59:59", started=True, ended=True),
            _game(2, 10, kickoff="2026-07-13 16:00:00", started=True, ended=True),
            _game(3, 10, kickoff="2026-07-14 15:59:59", started=True, ended=True),
            _game(4, 10, kickoff="2026-07-14 16:00:00", started=True, ended=True),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"success": True, "current_games": games},
                request=request,
            )

        async with self._service(handler) as service:
            result = await service.query_games(GameQuery(match_date=date(2026, 7, 14)))

        self.assertEqual([game.game_id for game in result], [2, 3])
        self.assertTrue(all(game.tournament_name == "赛事10" for game in result))
        self.assertTrue(seen[0].url.path.endswith("GetCurrentGames"))
        self.assertEqual(seen[0].url.params["history_bound"], "2026-07-13")
        self.assertEqual(seen[0].url.params["future_bound"], "2026-07-15")

    async def test_omitted_tournaments_discover_all_accessible(self) -> None:
        discovery_calls = 0
        tournament_reads: list[int] = []
        games_by_tournament = {
            10: [
                _game(
                    1,
                    10,
                    kickoff="2026-07-14 08:00:00",
                    started=True,
                    ended=True,
                    home_goal=2,
                    away_goal=1,
                    penalty_shootout=0,
                )
            ],
            20: [
                _game(
                    2,
                    20,
                    kickoff="2026-07-14 09:00:00",
                    started=True,
                    ended=True,
                    home_team_id=202,
                    away_team_id=101,
                    home_goal=1,
                    away_goal=0,
                    penalty_shootout=0,
                )
            ],
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal discovery_calls
            if request.url.path.endswith("GetMyTournaments"):
                discovery_calls += 1
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "tourns": [
                            {
                                "id": tournament_id,
                                "name": f"赛事{tournament_id}",
                                "brief_name": f"赛{tournament_id}",
                                "season": "2025~2026",
                                "begin": "2025-09-01",
                                "end": "2026-08-01",
                                "status": True,
                                "visible": True,
                            }
                            for tournament_id in (6, 10, 28, 20)
                        ],
                    },
                    request=request,
                )
            tournament_id = int(request.url.params["tourn_id"])
            tournament_reads.append(tournament_id)
            return httpx.Response(
                200,
                json=_tournament_payload(
                    tournament_id, games_by_tournament[tournament_id]
                ),
                request=request,
            )

        async with self._service(handler) as service:
            games = await service.query_games(GameQuery())
            team_matches = await service.query_team_matches(101)
            history = await service.query_team_to_team_matches(101, 202)

        self.assertEqual(discovery_calls, 3)
        self.assertEqual(sorted(tournament_reads), [10, 10, 10, 20, 20, 20])
        self.assertEqual([game.game_id for game in games], [1, 2])
        self.assertEqual([match.game.game_id for match in team_matches], [2, 1])
        self.assertEqual(history.tournament_ids, (10, 20))
        self.assertEqual([game.game_id for game in history.matches], [2, 1])
        self.assertEqual(history.summary.team_a_wins, 1)
        self.assertEqual(history.summary.team_b_wins, 1)

    async def test_blacklisted_query_scope_is_rejected_without_request(
        self,
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("blacklisted tournament must not be requested")

        async with self._service(handler) as service:
            calls = (
                service.query_games(GameQuery(tournament_ids=(6,))),
                service.query_team_matches(101, 28),
                service.query_team_to_team_matches(101, 202, [10, 28]),
            )
            for call in calls:
                with self.assertRaises(QueryValidationError):
                    await call

    async def test_unresolved_legacy_game_fails_the_query(self) -> None:
        game = _game(91, 10, penalty_shootout=None)
        game["home_tourn_team_id"] = None
        game["away_tourn_team_id"] = None
        game["home_tourn_team_info"] = None
        game["away_tourn_team_info"] = None

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_tournament_payload(10, [game]),
                request=request,
            )

        async with self._service(handler) as service:
            with self.assertRaises(SchemaError) as team_error:
                await service.query_team_matches(101, 10)
            with self.assertRaises(SchemaError) as game_error:
                await service.query_games(GameQuery(tournament_ids=(10,)))

        self.assertEqual(
            team_error.exception.field_path,
            "games[0].home_tourn_team_info",
        )
        self.assertEqual(
            game_error.exception.field_path,
            "games[0].home_tourn_team_info",
        )

    async def test_tournament_and_date_route_only_uses_tournament_api(self) -> None:
        paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(
                200,
                json=_tournament_payload(
                    10,
                    [
                        _game(
                            1,
                            10,
                            kickoff="2026-07-13 16:00:00",
                            started=True,
                            ended=True,
                        ),
                        _game(
                            2,
                            10,
                            kickoff="2026-07-14 16:00:00",
                            started=True,
                            ended=True,
                        ),
                    ],
                ),
                request=request,
            )

        async with self._service(handler) as service:
            result = await service.query_games(
                GameQuery(tournament_ids=(10,), match_date=date(2026, 7, 14))
            )

        self.assertEqual([game.game_id for game in result], [1])
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("GetTournInfo"))

    async def test_team_filters_finished_filter_and_sorting(self) -> None:
        games = [
            _game(
                3,
                10,
                kickoff="2026-07-14 10:00:00",
                home_team_id=303,
                away_team_id=404,
            ),
            _game(
                2,
                10,
                kickoff="2026-07-14 09:00:00",
                started=True,
                ended=True,
                home_team_id=202,
                away_team_id=101,
            ),
            _game(
                1,
                10,
                kickoff="2026-07-14 08:00:00",
                started=True,
                ended=True,
                home_team_id=101,
                away_team_id=202,
            ),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=_tournament_payload(10, games), request=request
            )

        async with self._service(handler) as service:
            any_result = await service.query_games(
                GameQuery(tournament_ids=(10,), team_ids=(101,))
            )
            all_result = await service.query_games(
                GameQuery(
                    tournament_ids=(10,),
                    team_ids=(101, 202),
                    team_match="all",
                    include_unfinished=False,
                )
            )

        self.assertEqual([game.game_id for game in any_result], [1, 2])
        self.assertEqual([game.game_id for game in all_result], [1, 2])

    async def test_duplicate_tournament_ids_are_fetched_once(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_tournament_payload(10), request=request)

        async with self._service(handler) as service:
            self.assertEqual(
                await service.query_games(GameQuery(tournament_ids=(10, 10))), []
            )
        self.assertEqual(calls, 1)

    async def test_multi_tournament_concurrency_is_bounded(self) -> None:
        active = 0
        peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            tournament_id = int(request.url.params["tourn_id"])
            active -= 1
            return httpx.Response(
                200, json=_tournament_payload(tournament_id), request=request
            )

        async with self._service(handler, max_concurrency=4) as service:
            result = await service.query_games(
                GameQuery(tournament_ids=(1, 2, 3, 4, 5, 7))
            )

        self.assertEqual(result, [])
        self.assertEqual(peak, 4)

    async def test_batch_failure_has_failed_tournament_ids(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            tournament_id = int(request.url.params["tourn_id"])
            if tournament_id == 2:
                return httpx.Response(
                    200,
                    json={"success": False, "info": "resource unavailable"},
                    request=request,
                )
            return httpx.Response(
                200, json=_tournament_payload(tournament_id), request=request
            )

        async with self._service(handler) as service:
            with self.assertRaises(BatchQueryError) as caught:
                await service.query_games(GameQuery(tournament_ids=(1, 2, 3)))
            with self.assertRaises(BatchQueryError) as h2h_caught:
                await service.query_team_to_team_matches(101, 202, [1, 2, 3])

        self.assertEqual(caught.exception.failed_tournament_ids, (2,))
        self.assertIsInstance(caught.exception.failures[2], InvalidResponse)
        self.assertEqual(h2h_caught.exception.failed_tournament_ids, (2,))
        self.assertIsInstance(h2h_caught.exception.failures[2], InvalidResponse)

    async def test_single_failure_is_not_wrapped(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"success": False, "info": "resource unavailable"},
                request=request,
            )

        async with self._service(handler) as service:
            with self.assertRaises(InvalidResponse):
                await service.query_games(GameQuery(tournament_ids=(1,)))
            with self.assertRaises(InvalidResponse):
                await service.query_team_matches(101, 1)

    async def test_conflicting_duplicate_game_ids_raise(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            tournament_id = int(request.url.params["tourn_id"])
            game = _game(
                99,
                tournament_id,
                started=True,
                ended=True,
                home_team_id=tournament_id * 10 + 1,
                away_team_id=tournament_id * 10 + 2,
                home_tournament_team_id=tournament_id * 100 + 1,
                away_tournament_team_id=tournament_id * 100 + 2,
            )
            return httpx.Response(
                200,
                json=_tournament_payload(tournament_id, [game]),
                request=request,
            )

        async with self._service(handler) as service:
            with self.assertRaises(DataConflict):
                await service.query_games(GameQuery(tournament_ids=(1, 2)))

    async def test_query_team_matches_normalises_results(self) -> None:
        games = [
            _game(
                10,
                10,
                kickoff="2026-07-14 06:00:00",
                started=True,
                ended=True,
                home_goal=1,
                away_goal=1,
                penalty_shootout=0,
                home_penalty=3,
                away_penalty=4,
            ),
            _game(
                9,
                10,
                kickoff="2026-07-14 07:00:00",
                started=True,
                ended=True,
                home_team_id=202,
                away_team_id=101,
                home_goal=2,
                away_goal=1,
                penalty_shootout=0,
            ),
            _game(
                1,
                10,
                kickoff="2026-07-14 08:00:00",
                started=True,
                ended=True,
                home_goal=3,
                away_goal=1,
                penalty_shootout=1,
                home_penalty=0,
                away_penalty=0,
            ),
            _game(
                2,
                10,
                kickoff="2026-07-14 09:00:00",
                started=True,
                ended=True,
                home_team_id=202,
                away_team_id=101,
                home_goal=2,
                away_goal=2,
                penalty_shootout=1,
                home_penalty=3,
                away_penalty=4,
            ),
            _game(
                3,
                10,
                kickoff="2026-07-14 10:00:00",
                started=True,
                ended=True,
                home_goal=8,
                away_goal=8,
                penalty_shootout=0,
                home_penalty=None,
                away_penalty=None,
                away_abandon=1,
            ),
            _game(
                4,
                10,
                kickoff="2026-07-14 11:00:00",
                started=True,
                ended=False,
                penalty_shootout=0,
                home_penalty=0,
                away_penalty=0,
            ),
            _game(
                8,
                10,
                kickoff="2026-07-14 15:00:00",
                started=True,
                ended=True,
                active=False,
            ),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_tournament_payload(10, games),
                request=request,
            )

        async with self._service(handler) as service:
            results = await service.query_team_matches(101, 10)
            with_unfinished = await service.query_team_matches(
                101, 10, include_unfinished=True
            )
            no_matches = await service.query_team_matches(999, 10)

        self.assertEqual([item.game.game_id for item in results], [3, 2, 1, 9, 10])
        by_game_id = {item.game.game_id: item for item in results}
        self.assertEqual(by_game_id[9].score_text, "1:2")
        self.assertEqual(by_game_id[9].result, MatchResult.LOSS)
        self.assertEqual(by_game_id[10].score_text, "1:1")
        self.assertEqual(by_game_id[10].result, MatchResult.DRAW)
        self.assertFalse(by_game_id[10].game.penalty_shootout)
        self.assertFalse(by_game_id[10].game.decided_by_penalty_shootout)
        self.assertEqual(by_game_id[1].score_text, "3:1")
        self.assertTrue(by_game_id[1].game.penalty_shootout)
        self.assertFalse(by_game_id[1].game.decided_by_penalty_shootout)
        self.assertIsNone(by_game_id[1].penalty_goals_for)
        self.assertEqual(results[0].score_text, "3:0")
        self.assertEqual(results[0].game.result_text, "3:0")
        self.assertEqual(results[0].result, MatchResult.WIN)
        self.assertEqual(results[1].venue, "away")
        self.assertEqual(results[1].score_text, "2(4):2(3)")
        self.assertEqual(results[1].game.result_text, "2(3):2(4)")
        self.assertTrue(results[1].game.penalty_shootout)
        self.assertTrue(results[1].game.decided_by_penalty_shootout)
        self.assertEqual(results[1].penalty_goals_for, 4)
        self.assertEqual(results[1].penalty_goals_against, 3)
        self.assertEqual(results[1].result, MatchResult.WIN)
        self.assertEqual(with_unfinished[0].game.game_id, 4)
        self.assertEqual(with_unfinished[0].result, MatchResult.UNKNOWN)
        self.assertIsNone(with_unfinished[0].score_text)
        self.assertEqual(no_matches, [])

    async def test_query_team_matches_uses_five_goal_forfeit(self) -> None:
        game = _game(
            1,
            10,
            started=True,
            ended=True,
            home_goal=0,
            away_goal=0,
            penalty_shootout=0,
            away_abandon=1,
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_tournament_payload(10, [game], players_per_side=5),
                request=request,
            )

        async with self._service(handler) as service:
            result = await service.query_team_matches(101, 10)

        self.assertEqual(result[0].goals_for, 5)
        self.assertEqual(result[0].goals_against, 0)
        self.assertEqual(result[0].score_text, "5:0")

    async def test_query_team_to_team_matches_summarises_all_tournaments(self) -> None:
        games_by_tournament = {
            10: [
                _game(
                    11,
                    10,
                    kickoff="2026-07-14 08:00:00",
                    started=True,
                    ended=True,
                    home_goal=1,
                    away_goal=0,
                    penalty_shootout=0,
                ),
                _game(
                    12,
                    10,
                    kickoff="2026-07-14 09:00:00",
                    started=True,
                    ended=True,
                    home_team_id=202,
                    away_team_id=101,
                    home_goal=2,
                    away_goal=2,
                    penalty_shootout=0,
                ),
            ],
            20: [
                _game(
                    21,
                    20,
                    kickoff="2026-07-14 10:00:00",
                    started=True,
                    ended=True,
                    home_team_id=202,
                    away_team_id=101,
                    home_goal=2,
                    away_goal=2,
                    penalty_shootout=1,
                    home_penalty=3,
                    away_penalty=4,
                ),
                _game(
                    22,
                    20,
                    kickoff="2026-07-14 11:00:00",
                    started=True,
                    ended=True,
                    penalty_shootout=0,
                    away_abandon=1,
                ),
                _game(
                    23,
                    20,
                    kickoff="2026-07-14 12:00:00",
                    started=True,
                    ended=False,
                    penalty_shootout=0,
                ),
            ],
            30: [],
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            tournament_id = int(request.url.params["tourn_id"])
            return httpx.Response(
                200,
                json=_tournament_payload(
                    tournament_id, games_by_tournament[tournament_id]
                ),
                request=request,
            )

        async with self._service(handler) as service:
            history = await service.query_team_to_team_matches(
                101,
                202,
                [10, 20, 30, 10],
                include_unfinished=True,
            )
            empty = await service.query_team_to_team_matches(101, 202, [30])

        self.assertEqual(history.tournament_ids, (10, 20, 30))
        self.assertEqual(
            [game.game_id for game in history.matches], [23, 22, 21, 12, 11]
        )
        self.assertEqual(history.matches[2].result_text, "2(3):2(4)")
        self.assertEqual(history.matches[1].result_text, "3:0")
        self.assertEqual(history.summary.team_a_wins, 3)
        self.assertEqual(history.summary.draws, 1)
        self.assertEqual(history.summary.team_b_wins, 0)
        self.assertEqual(history.by_tournament[10].team_a_wins, 1)
        self.assertEqual(history.by_tournament[10].draws, 1)
        self.assertEqual(history.by_tournament[20].team_a_wins, 2)
        self.assertEqual(history.by_tournament[30].team_a_wins, 0)
        self.assertEqual(empty.matches, ())
        self.assertEqual(empty.summary.team_a_wins, 0)
        self.assertEqual(empty.by_tournament[30].draws, 0)

    async def test_query_team_to_team_matches_expands_static_team_ids(self) -> None:
        games = [
            _game(
                1,
                10,
                started=True,
                ended=True,
                home_team_id=254,
                away_team_id=48,
                home_goal=1,
                away_goal=0,
                penalty_shootout=0,
            ),
            _game(
                2,
                10,
                started=True,
                ended=True,
                home_team_id=48,
                away_team_id=80,
                home_goal=0,
                away_goal=2,
                penalty_shootout=0,
            ),
            _game(
                3,
                10,
                started=True,
                ended=True,
                home_team_id=101,
                away_team_id=48,
                home_goal=3,
                away_goal=0,
                penalty_shootout=0,
            ),
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_tournament_payload(10, games),
                request=request,
            )

        async with self._service(handler) as service:
            history = await service.query_team_to_team_matches(254, 48, [10])

        self.assertEqual(history.team_a_id, 254)
        self.assertEqual(history.team_b_id, 48)
        self.assertEqual([game.game_id for game in history.matches], [2, 1])
        self.assertEqual(history.summary.team_a_wins, 2)
        self.assertEqual(history.summary.draws, 0)
        self.assertEqual(history.summary.team_b_wins, 0)

    async def test_query_team_outcomes_uses_static_alias_and_shared_id_data(
        self,
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("static outcome queries must not make requests")

        async with self._service(handler) as service:
            vehicle = await service.query_team_outcomes(48)
            explicit = await service.query_team_outcomes(48, [128, 122, 128])
            current_alias = await service.query_team_outcomes(132)
            historical_alias = await service.query_team_outcomes(244)
            shared_three_ways = await service.query_team_outcomes(253)
            merged_news = await service.query_team_outcomes(2041, [101])
            non_participant = await service.query_team_outcomes(2051, [122])

        self.assertEqual(
            [
                (outcome.tournament_id, outcome.team_name, outcome.rank)
                for outcome in vehicle
            ],
            [
                (122, "车辆与运载学院男足", "冠军"),
                (128, "车辆与运载学院五人制", "32强"),
                (99, "车辆与运载学院男足", "冠军"),
                (111, "车辆与运载学院五人制", "48强"),
                (89, "车辆与运载学院男足", "八强"),
                (93, "车辆与运载学院五人制", "32强"),
            ],
        )
        self.assertEqual(
            [(outcome.tournament_id, outcome.team_name) for outcome in explicit],
            [(128, "车辆与运载学院五人制"), (122, "车辆与运载学院男足")],
        )
        self.assertEqual(current_alias, historical_alias)
        self.assertEqual(
            {outcome.team_name for outcome in shared_three_ways},
            {
                "新闻与传播学院-马克思主义学院男足",
                "新闻与传播学院-马克思主义学院女足",
                "新闻与传播学院-马克思主义学院五人制",
            },
        )
        self.assertEqual(
            [(item.team_name, item.rank) for item in merged_news],
            [("新闻与传播学院-马克思主义学院男足", "小组第三")],
        )
        self.assertEqual(non_participant, [])

    async def test_query_validation(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("invalid queries must not make requests")

        async with self._service(handler) as service:
            invalid_calls = (
                ("team id", lambda: service.query_team_matches(True, 1)),
                ("tournament id", lambda: service.query_team_matches(1, 0)),
                (
                    "unfinished flag",
                    lambda: service.query_team_matches(1, 1, include_unfinished=1),
                ),
                (
                    "same teams",
                    lambda: service.query_team_to_team_matches(1, 1, [10]),
                ),
                (
                    "overlapping aliases",
                    lambda: service.query_team_to_team_matches(254, 80, [10]),
                ),
                (
                    "empty tournament ids",
                    lambda: service.query_team_to_team_matches(1, 2, []),
                ),
                (
                    "boolean tournament id",
                    lambda: service.query_team_to_team_matches(1, 2, [True]),
                ),
                ("outcome team id", lambda: service.query_team_outcomes(True)),
                (
                    "unknown outcome team",
                    lambda: service.query_team_outcomes(999999),
                ),
                (
                    "empty outcome tournaments",
                    lambda: service.query_team_outcomes(48, []),
                ),
                (
                    "boolean outcome tournament",
                    lambda: service.query_team_outcomes(48, [True]),
                ),
                (
                    "blacklisted outcome tournament",
                    lambda: service.query_team_outcomes(48, [6]),
                ),
                (
                    "boolean game tournament",
                    lambda: service.query_games(GameQuery(tournament_ids=(True,))),
                ),
                (
                    "datetime match date",
                    lambda: service.query_games(
                        GameQuery(match_date=datetime(2026, 7, 14))
                    ),
                ),
                (
                    "too many game teams",
                    lambda: service.query_games(
                        GameQuery(tournament_ids=(1,), team_ids=(1, 2, 3))
                    ),
                ),
                (
                    "invalid game team match",
                    lambda: service.query_games(
                        replace(GameQuery(tournament_ids=(1,)), team_match="none")
                    ),
                ),
            )
            for scenario, call in invalid_calls:
                with self.subTest(scenario=scenario):
                    with self.assertRaises(QueryValidationError):
                        await call()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "直接运行时默认只执行 THUFootball 真实只读冒烟；"
            "使用 --unit-tests 运行本文件的全部离线测试。"
        )
    )
    parser.add_argument(
        "--tournament-id",
        type=_positive_cli_id,
        action="append",
        default=[],
        help="赛事 ID；交锋时可重复传入，省略则查询全部可访问赛事",
    )
    parser.add_argument(
        "--game-id",
        type=_positive_cli_id,
        help="额外读取指定比赛 ID 的详情",
    )
    parser.add_argument(
        "--match-date",
        type=_cli_date,
        help="按北京时间自然日过滤，格式为 YYYY-MM-DD",
    )
    parser.add_argument(
        "--team-id",
        type=_positive_cli_id,
        help="查询该全局球队 ID 的比赛",
    )
    parser.add_argument(
        "--opponent-id",
        type=_positive_cli_id,
        help="与 --team-id 组合查询两队交锋",
    )
    parser.add_argument(
        "--include-unfinished",
        action="store_true",
        help="球队比赛或交锋中包含有效未完赛比赛",
    )
    parser.add_argument(
        "--full-output",
        action="store_true",
        help="打印完整白名单领域对象；默认仅打印脱敏摘要",
    )
    parser.add_argument(
        "--outcomes",
        action="store_true",
        help="从本地静态名单查询球队最终成绩，不访问真实 API",
    )
    parser.add_argument(
        "--unit-tests",
        action="store_true",
        help="运行全部离线单元测试，不访问真实 API",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    runner = unittest.TextTestRunner(verbosity=2)
    if args.unit_tests:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        return 0 if runner.run(suite).wasSuccessful() else 1

    tournament_ids = tuple(dict.fromkeys(args.tournament_id))
    if args.opponent_id is not None and args.team_id is None:
        parser.error("--opponent-id 必须与 --team-id 一起使用")
    if args.outcomes and args.team_id is None:
        parser.error("--outcomes 必须与 --team-id 一起使用")
    if args.outcomes and (
        args.opponent_id is not None
        or args.match_date is not None
        or args.include_unfinished
        or args.game_id is not None
    ):
        parser.error(
            "--outcomes 不能与 --opponent-id、--match-date、"
            "--include-unfinished 或 --game-id 同时使用"
        )
    if not args.outcomes and args.team_id is not None and args.match_date is not None:
        parser.error("球队比赛或交锋查询不能与 --match-date 同时使用")
    if not args.outcomes and args.team_id is not None and args.opponent_id is None:
        if len(tournament_ids) > 1:
            parser.error("球队比赛查询最多指定一个 --tournament-id")

    global _LIVE_SMOKE_CONFIG
    _LIVE_SMOKE_CONFIG = _LiveSmokeConfig(
        tournament_ids=tournament_ids,
        game_id=args.game_id,
        match_date=args.match_date,
        team_id=args.team_id,
        opponent_id=args.opponent_id,
        include_unfinished=args.include_unfinished,
        full_output=args.full_output,
        outcomes=args.outcomes,
    )
    suite = unittest.TestSuite([LiveSmokeTests("test_live_smoke")])
    return 0 if runner.run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
