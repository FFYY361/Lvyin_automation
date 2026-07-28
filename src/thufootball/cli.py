"""Command-line access to the public :mod:`thufootball` client methods."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .client import THUFootballClient
from .errors import THUFootballError
from .models import GameQuery, ReportSettings
from .queries import THUFootballQueryService
from .reports import THUFootballReportService


def _positive_id(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ID must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("ID must be a positive integer")
    return parsed


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thufootball",
        description="Query THUFootball data and download match reports",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    games = commands.add_parser("games", help="query games by tournament, date or team")
    games.add_argument(
        "--tournament-id",
        type=_positive_id,
        action="append",
        help="limit the query to this tournament; may be repeated",
    )
    games.add_argument(
        "--match-date", type=_date, help="Beijing calendar date (YYYY-MM-DD)"
    )
    games.add_argument(
        "--team-id",
        type=_positive_id,
        action="append",
        help="limit the query to this global team ID; may be repeated twice",
    )
    games.add_argument(
        "--team-match",
        choices=("any", "all"),
        default="any",
        help="match any or all supplied teams (default: any)",
    )
    games.add_argument(
        "--finished-only",
        action="store_true",
        help="exclude scheduled and in-progress games",
    )

    team_matches = commands.add_parser(
        "team-matches", help="query one team's matches from its own perspective"
    )
    team_matches.add_argument("team_id", type=_positive_id)
    team_matches.add_argument(
        "--tournament-id", type=_positive_id, help="limit the query to one tournament"
    )
    team_matches.add_argument(
        "--include-unfinished",
        action="store_true",
        help="include valid scheduled and in-progress games",
    )

    team_outcomes = commands.add_parser(
        "team-outcomes", help="read static final tournament outcomes for one team"
    )
    team_outcomes.add_argument("team_id", type=_positive_id)
    team_outcomes.add_argument(
        "--tournament-id",
        type=_positive_id,
        action="append",
        help="select a supported tournament; may be repeated",
    )

    head_to_head = commands.add_parser(
        "head-to-head", help="query the history between two teams"
    )
    head_to_head.add_argument("team_a_id", type=_positive_id)
    head_to_head.add_argument("team_b_id", type=_positive_id)
    head_to_head.add_argument(
        "--tournament-id",
        type=_positive_id,
        action="append",
        help="select a tournament; may be repeated",
    )
    head_to_head.add_argument(
        "--include-unfinished",
        action="store_true",
        help="include valid scheduled and in-progress games",
    )

    report = commands.add_parser(
        "report",
        help="render and download one match report as PNG",
    )
    report.add_argument("game_id", type=_positive_id)
    report.add_argument(
        "--output",
        help="PNG file or destination directory (default: current directory)",
    )
    report.add_argument(
        "--no-qrcode",
        action="store_true",
        help="omit the mini-program QR code",
    )
    report.add_argument(
        "--no-time",
        action="store_true",
        help="omit the kickoff time",
    )
    report.add_argument(
        "--no-field",
        action="store_true",
        help="omit the field name",
    )
    report.add_argument(
        "--no-lineup",
        action="store_true",
        help="omit the starting lineups",
    )
    report.add_argument(
        "--refresh-stats",
        action="store_true",
        help=(
            "call the state-changing OnReStatGameData endpoint before rendering "
            "(unsafe; may modify server-side match statistics)"
        ),
    )
    report.add_argument(
        "--override",
        action="store_true",
        help="replace an existing output file",
    )
    return parser


def _jsonable(value: object) -> Any:
    """Convert public domain objects to deterministic JSON-compatible values."""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"cannot serialise {type(value).__name__} to JSON")


async def _run(args: argparse.Namespace) -> object:
    client_options = (
        {"load_environment": False} if args.command == "team-outcomes" else {}
    )
    async with THUFootballClient(**client_options) as client:
        queries = THUFootballQueryService(client)
        if args.command == "games":
            return await queries.query_games(
                GameQuery(
                    tournament_ids=tuple(args.tournament_id or ()),
                    match_date=args.match_date,
                    team_ids=tuple(args.team_id or ()),
                    team_match=args.team_match,
                    include_unfinished=not args.finished_only,
                )
            )
        if args.command == "team-matches":
            return await queries.query_team_matches(
                args.team_id,
                args.tournament_id,
                include_unfinished=args.include_unfinished,
            )
        if args.command == "team-outcomes":
            tournament_ids = (
                tuple(args.tournament_id) if args.tournament_id is not None else None
            )
            return await queries.query_team_outcomes(args.team_id, tournament_ids)
        if args.command == "head-to-head":
            tournament_ids = (
                tuple(args.tournament_id) if args.tournament_id is not None else None
            )
            return await queries.query_team_to_team_matches(
                args.team_a_id,
                args.team_b_id,
                tournament_ids,
                include_unfinished=args.include_unfinished,
            )
        if args.command == "report":
            reports = THUFootballReportService(client)
            return await reports.download_game_report(
                args.game_id,
                args.output,
                settings=ReportSettings(
                    include_qr_code=not args.no_qrcode,
                    include_time=not args.no_time,
                    include_field=not args.no_field,
                    include_lineup=not args.no_lineup,
                ),
                refresh_stats=args.refresh_stats,
                overwrite=args.override,
            )
    raise AssertionError("unreachable command")


def _error_payload(error: THUFootballError) -> dict[str, object]:
    return {
        "status": "error",
        "error": type(error).__name__,
        "stage": error.stage,
        "retryable": error.retryable,
        "tournament_id": error.tournament_id,
        "game_id": error.game_id,
        "message": str(error),
    }


def _write_json(value: object, *, stream, indent: int | None = None) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=indent),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except THUFootballError as exc:
        _write_json(_error_payload(exc), stream=sys.stderr)
        return 2

    _write_json(_jsonable(result), stream=sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
