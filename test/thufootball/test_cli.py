from __future__ import annotations

import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from thufootball import ConfigurationError, GameQuery, GameStatus
from thufootball.cli import _jsonable
from thufootball.cli import main as cli_main


@dataclass(frozen=True)
class _Result:
    command: str


@dataclass(frozen=True)
class _SerialisationFixture:
    kickoff: datetime
    match_date: date
    status: GameStatus
    values: tuple[int, ...]
    labels: object


class _FakeClient:
    init_calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.init_calls.append(kwargs)

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeQueryService:
    calls: list[tuple[object, ...]] = []

    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    async def query_games(self, query: GameQuery) -> list[_Result]:
        self.calls.append(("query_games", query))
        return [_Result("games")]

    async def query_team_matches(
        self,
        team_id: int,
        tournament_id: int | None,
        *,
        include_unfinished: bool,
    ) -> list[_Result]:
        self.calls.append(
            ("query_team_matches", team_id, tournament_id, include_unfinished)
        )
        return [_Result("team-matches")]

    async def query_team_outcomes(
        self,
        team_id: int,
        tournament_ids: tuple[int, ...] | None,
    ) -> list[_Result]:
        self.calls.append(("query_team_outcomes", team_id, tournament_ids))
        return [_Result("team-outcomes")]

    async def query_team_to_team_matches(
        self,
        team_a_id: int,
        team_b_id: int,
        tournament_ids: tuple[int, ...] | None,
        *,
        include_unfinished: bool,
    ) -> _Result:
        self.calls.append(
            (
                "query_team_to_team_matches",
                team_a_id,
                team_b_id,
                tournament_ids,
                include_unfinished,
            )
        )
        return _Result("head-to-head")


class ThufootballCliTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.init_calls = []
        _FakeQueryService.calls = []

    def _run(self, argv: list[str]) -> tuple[int, object]:
        stdout = StringIO()
        with patch("thufootball.cli.THUFootballClient", _FakeClient):
            with patch("thufootball.cli.THUFootballQueryService", _FakeQueryService):
                with redirect_stdout(stdout):
                    status = cli_main(argv)
        return status, json.loads(stdout.getvalue())

    def test_dispatches_all_public_domain_queries(self) -> None:
        cases = (
            (
                [
                    "games",
                    "--tournament-id",
                    "122",
                    "--tournament-id",
                    "123",
                    "--match-date",
                    "2026-07-15",
                    "--team-id",
                    "48",
                    "--team-id",
                    "163",
                    "--team-match",
                    "all",
                    "--finished-only",
                ],
                (
                    "query_games",
                    GameQuery(
                        tournament_ids=(122, 123),
                        match_date=date(2026, 7, 15),
                        team_ids=(48, 163),
                        team_match="all",
                        include_unfinished=False,
                    ),
                ),
                [{"command": "games"}],
                {},
            ),
            (
                [
                    "team-matches",
                    "48",
                    "--tournament-id",
                    "122",
                    "--include-unfinished",
                ],
                ("query_team_matches", 48, 122, True),
                [{"command": "team-matches"}],
                {},
            ),
            (
                [
                    "team-outcomes",
                    "48",
                    "--tournament-id",
                    "128",
                    "--tournament-id",
                    "122",
                ],
                ("query_team_outcomes", 48, (128, 122)),
                [{"command": "team-outcomes"}],
                {"load_environment": False},
            ),
            (
                [
                    "head-to-head",
                    "48",
                    "163",
                    "--tournament-id",
                    "122",
                    "--tournament-id",
                    "123",
                    "--include-unfinished",
                ],
                ("query_team_to_team_matches", 48, 163, (122, 123), True),
                {"command": "head-to-head"},
                {},
            ),
        )

        for argv, expected_call, expected_output, expected_client_options in cases:
            with self.subTest(argv=argv):
                _FakeClient.init_calls = []
                _FakeQueryService.calls = []
                status, output = self._run(argv)
                self.assertEqual(status, 0)
                self.assertEqual(_FakeClient.init_calls, [expected_client_options])
                self.assertEqual(_FakeQueryService.calls, [expected_call])
                self.assertEqual(output, expected_output)

    def test_serialises_nested_public_values(self) -> None:
        fixture = _SerialisationFixture(
            kickoff=datetime(2026, 7, 15, 12, 30, tzinfo=UTC),
            match_date=date(2026, 7, 15),
            status=GameStatus.FINISHED,
            values=(1, 2),
            labels=MappingProxyType({1: "一", 2: "二"}),
        )

        self.assertEqual(
            _jsonable(fixture),
            {
                "kickoff": "2026-07-15T12:30:00+00:00",
                "match_date": "2026-07-15",
                "status": "finished",
                "values": [1, 2],
                "labels": {"1": "一", "2": "二"},
            },
        )

    def test_rejects_invalid_cli_parameters(self) -> None:
        invalid_commands = (
            ["games", "--match-date", "2026-7-15"],
            ["games", "--team-match", "neither"],
            ["team-matches", "0"],
            ["team-outcomes", "not-an-id"],
            ["head-to-head", "48", "not-an-id"],
        )

        for argv in invalid_commands:
            with self.subTest(argv=argv):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli_main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_reports_client_errors_as_json_without_credentials(self) -> None:
        error = ConfigurationError(
            "complete THUFootball credentials are required",
            stage="configuration",
        )
        stderr = StringIO()

        with patch("thufootball.cli.THUFootballClient", side_effect=error):
            with redirect_stderr(stderr):
                status = cli_main(["games"])

        self.assertEqual(status, 2)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "ConfigurationError")
        self.assertEqual(payload["stage"], "configuration")
        self.assertFalse(payload["retryable"])
        self.assertNotIn("openid", payload)
        self.assertNotIn("session_key", payload)


if __name__ == "__main__":
    unittest.main()
