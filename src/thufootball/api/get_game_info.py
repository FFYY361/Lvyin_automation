"""Local wrapper and validation entry point for ``GetGameInfo``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import Any

if __package__:
    from .utils import (
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _authenticated_parameters,
        _positive_integer,
        load_credentials,
        request_json,
    )
else:  # Support ``python src/thufootball/api/get_game_info.py``.
    from utils import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _authenticated_parameters,
        _positive_integer,
        load_credentials,
        request_json,
    )


def get_game_info(
    openid: str,
    session_key: str,
    game_id: int,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Return the complete THUFootball response for ``game_id``."""

    parameters = _authenticated_parameters(openid, session_key)
    parameters["game_id"] = _positive_integer(game_id, "game_id")
    return request_json(
        "GetGameInfo",
        parameters,
        base_url=base_url,
        timeout=timeout,
    )


def _list_length(payload: Mapping[str, Any], field: str) -> int | None:
    value = payload.get(field)
    return len(value) if isinstance(value, list) else None


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    game = payload.get("game_info")
    tournament = payload.get("tourn_info")
    home_team = game.get("home_tourn_team_info") if isinstance(game, Mapping) else None
    away_team = game.get("away_tourn_team_info") if isinstance(game, Mapping) else None
    return {
        "endpoint": "GetGameInfo",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "game_id": game.get("id") if isinstance(game, Mapping) else None,
        "time": game.get("time") if isinstance(game, Mapping) else None,
        "result": game.get("result") if isinstance(game, Mapping) else None,
        "tourn_id": (tournament.get("id") if isinstance(tournament, Mapping) else None),
        "tourn_name": (
            tournament.get("name") if isinstance(tournament, Mapping) else None
        ),
        "home_team": (
            home_team.get("brief_name") if isinstance(home_team, Mapping) else None
        ),
        "away_team": (
            away_team.get("brief_name") if isinstance(away_team, Mapping) else None
        ),
        "home_player_count": _list_length(payload, "home_tourn_team_players"),
        "away_player_count": _list_length(payload, "away_tourn_team_players"),
        "event_count": _list_length(payload, "events"),
        "comment_count": _list_length(payload, "comments"),
        "referee_count": _list_length(payload, "referees"),
        "official_count": _list_length(payload, "officials"),
        "duration_count": _list_length(payload, "durations"),
        "response_fields": sorted(str(key) for key in payload),
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate GetGameInfo locally")
    parser.add_argument("--game-id", required=True, type=int, help="game ID")
    parser.add_argument(
        "--full-response", action="store_true", help="print the complete JSON response"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a live validation using credentials from the project ``.env``."""

    args = _argument_parser().parse_args(argv)
    openid, session_key = load_credentials()
    if not openid or not session_key:
        print(
            "Please set THUFOOTBALL_OPENID and THUFOOTBALL_SESSION_KEY "
            "in the project .env file or current shell.",
            file=sys.stderr,
        )
        return 2

    try:
        payload = get_game_info(openid, session_key, args.game_id)
    except (ValueError, THUFootballRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = payload if args.full_response else _validation_summary(payload)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
