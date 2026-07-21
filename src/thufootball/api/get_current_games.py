"""Local wrapper and validation entry point for ``GetCurrentGames``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

if __package__:
    from .utils import (
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _optional_authentication_parameters,
        load_credentials,
        request_json,
    )
else:  # Support ``python src/thufootball/api/get_current_games.py``.
    from utils import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _optional_authentication_parameters,
        load_credentials,
        request_json,
    )

_GAME_TYPES = ("public", "all")


def _normalise_date(value: date | str | None, parameter_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{parameter_name} must be a date, not a datetime")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError(f"{parameter_name} must use YYYY-MM-DD format")

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{parameter_name} must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{parameter_name} must use YYYY-MM-DD format")
    return value


def get_current_games(
    openid: str | None = None,
    session_key: str | None = None,
    *,
    history_bound: date | str | None = None,
    future_bound: date | str | None = None,
    field_id: int | None = None,
    game_type: str = "public",
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Return games in the requested interval from ``GetCurrentGames``.

    Authentication is optional for public games, but ``openid`` and
    ``session_key`` must either both be supplied or both be omitted. Python's
    ``game_type`` argument is sent to the HTTP API under its original name,
    ``type``.
    """

    authentication = _optional_authentication_parameters(openid, session_key)

    normalised_history = _normalise_date(history_bound, "history_bound")
    normalised_future = _normalise_date(future_bound, "future_bound")
    if (
        normalised_history is not None
        and normalised_future is not None
        and normalised_history > normalised_future
    ):
        raise ValueError("history_bound must not be later than future_bound")

    if field_id is not None and (
        isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0
    ):
        raise ValueError("field_id must be a positive integer when supplied")
    if game_type not in _GAME_TYPES:
        raise ValueError("game_type must be either 'public' or 'all'")

    parameters: dict[str, str | int] = {"type": game_type}
    parameters.update(authentication)
    if normalised_history is not None:
        parameters["history_bound"] = normalised_history
    if normalised_future is not None:
        parameters["future_bound"] = normalised_future
    if field_id is not None:
        parameters["field_id"] = field_id
    return request_json(
        "GetCurrentGames", parameters, base_url=base_url, timeout=timeout
    )


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    games = payload.get("current_games")
    game_count = len(games) if isinstance(games, list) else None
    first_game_id = None
    if games and isinstance(games, list) and isinstance(games[0], Mapping):
        first_game_id = games[0].get("id")
    return {
        "endpoint": "GetCurrentGames",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "game_count": game_count,
        "first_game_id": first_game_id,
        "response_fields": sorted(str(key) for key in payload),
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate GetCurrentGames locally")
    parser.add_argument("--history-bound", help="start date in YYYY-MM-DD format")
    parser.add_argument("--future-bound", help="end date in YYYY-MM-DD format")
    parser.add_argument("--field-id", type=int, help="only return games at this field")
    parser.add_argument(
        "--game-type", choices=_GAME_TYPES, default="public", help="visibility scope"
    )
    parser.add_argument(
        "--without-auth",
        action="store_true",
        help="do not send credentials from .env",
    )
    parser.add_argument(
        "--full-response", action="store_true", help="print the complete JSON response"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a live validation using optional credentials from the project ``.env``."""

    args = _argument_parser().parse_args(argv)
    if args.without_auth:
        openid = session_key = None
    else:
        openid, session_key = load_credentials()

    try:
        payload = get_current_games(
            openid,
            session_key,
            history_bound=args.history_bound,
            future_bound=args.future_bound,
            field_id=args.field_id,
            game_type=args.game_type,
        )
    except (ValueError, THUFootballRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = payload if args.full_response else _validation_summary(payload)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
