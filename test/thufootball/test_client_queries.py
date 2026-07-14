from __future__ import annotations

import argparse
import asyncio
import json
import sys
import unittest
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from thufootball import (
    AuthenticationError,
    BatchQueryError,
    ConfigurationError,
    DataConflict,
    GameQuery,
    GameStatus,
    InvalidResponse,
    QueryValidationError,
    SchemaError,
    THUFootballClient,
    THUFootballQueryService,
    Timeout,
)
from thufootball.mappers import map_game_detail, map_game_summary


@dataclass(frozen=True)
class _LiveSmokeConfig:
    tournament_id: int | None
    game_id: int | None
    match_date: date | None
    full_output: bool


_LIVE_SMOKE_CONFIG: _LiveSmokeConfig | None = None


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
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
        "penalty_shootout": 1,
        "home_penalty": 5,
        "away_penalty": 4,
        "home_abandon": None,
        "away_abandon": 0,
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
            self.skipTest(
                "真实冒烟仅在直接运行本文件时启用，离线发现测试不会访问 API"
            )

        query_date = config.match_date
        if config.tournament_id is None and query_date is None:
            query_date = date.today()

        async with THUFootballClient() as client:
            probe = await client.get_user_info()
            tournaments = await client.get_accessible_tournaments()
            service = THUFootballQueryService(client)
            games = await service.query_games(
                GameQuery(
                    tournament_ids=(config.tournament_id,)
                    if config.tournament_id is not None
                    else (),
                    match_date=query_date,
                )
            )
            detail = (
                await client.get_game_info(config.game_id)
                if config.game_id is not None
                else None
            )

        self.assertIsInstance(probe.user_registered, bool)
        self.assertIsInstance(tournaments, list)
        self.assertIsInstance(games, list)
        if config.game_id is not None:
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual(detail.game.game_id, config.game_id)

        if config.full_output:
            output: dict[str, object] = {
                "query": {
                    "tournament_id": config.tournament_id,
                    "game_id": config.game_id,
                    "match_date": query_date,
                },
                "user_probe": probe,
                "accessible_tournament_count": len(tournaments),
                "games": games,
                "game_detail": detail,
            }
        else:
            output = {
                "authenticated": True,
                "user_registered": probe.user_registered,
                "accessible_tournament_count": len(tournaments),
                "query_tournament_id": config.tournament_id,
                "query_match_date": query_date,
                "query_game_count": len(games),
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
        started = map_game_summary(
            _game(started=True), "game", now=now
        )
        finished = map_game_summary(
            _game(started=True, ended=True), "game", now=now
        )
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
        self.assertEqual(scheduled.tournament_name, "赛事10")
        self.assertEqual(scheduled.home_score, 2)
        self.assertTrue(scheduled.penalty_shootout)
        self.assertFalse(scheduled.away_abandon)

    def test_invalid_optional_scores_become_none(self) -> None:
        summary = map_game_summary(
            _game(home_goal=-1, away_goal="2"),
            "game",
            now=datetime(2026, 7, 14, tzinfo=UTC),
        )
        self.assertIsNone(summary.home_score)
        self.assertIsNone(summary.away_score)

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
            client = THUFootballClient(
                load_environment=False, http_client=http
            )
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

    async def test_injected_http_client_is_not_closed(self) -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
        client = THUFootballClient(load_environment=False, http_client=http)
        await client.aclose()
        self.assertFalse(http.is_closed)
        await http.aclose()

    async def test_owned_http_client_is_closed(self) -> None:
        client = THUFootballClient(load_environment=False)
        owned_http = client._http_client
        await client.aclose()
        self.assertTrue(owned_http.is_closed)


class QueryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def _service(
        self,
        handler,
        *,
        max_concurrency: int = 4,
    ) -> tuple[httpx.AsyncClient, THUFootballQueryService]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = THUFootballClient(
            openid="openid", session_key="session", http_client=http
        )
        return http, THUFootballQueryService(
            client, max_concurrency=max_concurrency
        )

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

        http, service = await self._service(handler)
        try:
            result = await service.query_games(GameQuery(match_date=date(2026, 7, 14)))
        finally:
            await http.aclose()

        self.assertEqual([game.game_id for game in result], [2, 3])
        self.assertTrue(all(game.tournament_name == "赛事10" for game in result))
        self.assertTrue(seen[0].url.path.endswith("GetCurrentGames"))
        self.assertEqual(seen[0].url.params["history_bound"], "2026-07-13")
        self.assertEqual(seen[0].url.params["future_bound"], "2026-07-15")

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

        http, service = await self._service(handler)
        try:
            result = await service.query_games(
                GameQuery(tournament_ids=(10,), match_date=date(2026, 7, 14))
            )
        finally:
            await http.aclose()

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

        http, service = await self._service(handler)
        try:
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
        finally:
            await http.aclose()

        self.assertEqual([game.game_id for game in any_result], [1, 2])
        self.assertEqual([game.game_id for game in all_result], [1, 2])

    async def test_duplicate_tournament_ids_are_fetched_once(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200, json=_tournament_payload(10), request=request
            )

        http, service = await self._service(handler)
        try:
            self.assertEqual(
                await service.query_games(GameQuery(tournament_ids=(10, 10))), []
            )
        finally:
            await http.aclose()
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

        http, service = await self._service(handler, max_concurrency=4)
        try:
            result = await service.query_games(
                GameQuery(tournament_ids=(1, 2, 3, 4, 5, 6))
            )
        finally:
            await http.aclose()

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

        http, service = await self._service(handler)
        try:
            with self.assertRaises(BatchQueryError) as caught:
                await service.query_games(GameQuery(tournament_ids=(1, 2, 3)))
        finally:
            await http.aclose()

        self.assertEqual(caught.exception.failed_tournament_ids, (2,))
        self.assertIsInstance(caught.exception.failures[2], InvalidResponse)

    async def test_single_failure_is_not_wrapped(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"success": False, "info": "resource unavailable"},
                request=request,
            )

        http, service = await self._service(handler)
        try:
            with self.assertRaises(InvalidResponse):
                await service.query_games(GameQuery(tournament_ids=(1,)))
        finally:
            await http.aclose()

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

        http, service = await self._service(handler)
        try:
            with self.assertRaises(DataConflict):
                await service.query_games(GameQuery(tournament_ids=(1, 2)))
        finally:
            await http.aclose()

    async def test_query_validation(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("invalid queries must not make requests")

        http, service = await self._service(handler)
        try:
            invalid_queries = (
                GameQuery(),
                GameQuery(tournament_ids=(True,)),
                GameQuery(match_date=datetime(2026, 7, 14)),
                GameQuery(tournament_ids=(1,), team_ids=(1, 2, 3)),
                replace(GameQuery(tournament_ids=(1,)), team_match="none"),
            )
            for query in invalid_queries:
                with self.subTest(query=query):
                    with self.assertRaises(QueryValidationError):
                        await service.query_games(query)
        finally:
            await http.aclose()


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
        help="按赛事 ID 查询比赛",
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
        "--full-output",
        action="store_true",
        help="打印完整白名单领域对象；默认仅打印脱敏摘要",
    )
    parser.add_argument(
        "--unit-tests",
        action="store_true",
        help="运行全部离线单元测试，不访问真实 API",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    runner = unittest.TextTestRunner(verbosity=2)
    if args.unit_tests:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        return 0 if runner.run(suite).wasSuccessful() else 1

    global _LIVE_SMOKE_CONFIG
    _LIVE_SMOKE_CONFIG = _LiveSmokeConfig(
        tournament_id=args.tournament_id,
        game_id=args.game_id,
        match_date=args.match_date,
        full_output=args.full_output,
    )
    suite = unittest.TestSuite([LiveSmokeTests("test_live_smoke")])
    return 0 if runner.run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
