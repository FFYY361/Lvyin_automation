"""Local wrapper and validation entry point for ``GetTournInfo``."""

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
else:  # Support ``python src/thufootball/api/get_tourn_info.py``.
    from utils import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _authenticated_parameters,
        _positive_integer,
        load_credentials,
        request_json,
    )


def get_tourn_info(
    openid: str,
    session_key: str,
    tourn_id: int,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Return tournament details, teams and games for ``tourn_id``."""

    parameters = _authenticated_parameters(openid, session_key)
    parameters["tourn_id"] = _positive_integer(tourn_id, "tourn_id")
    return request_json(
        "GetTournInfo",
        parameters,
        base_url=base_url,
        timeout=timeout,
    )


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    tourn_info = payload.get("tourn_info")
    games = payload.get("games")
    teams = payload.get("registered_teams")
    if not isinstance(teams, list):
        teams = payload.get("tourn_teams")
    return {
        "endpoint": "GetTournInfo",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "tourn_id": tourn_info.get("id") if isinstance(tourn_info, Mapping) else None,
        "tourn_name": (
            tourn_info.get("name") if isinstance(tourn_info, Mapping) else None
        ),
        "team_count": len(teams) if isinstance(teams, list) else None,
        "game_count": len(games) if isinstance(games, list) else None,
        "response_fields": sorted(str(key) for key in payload),
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate GetTournInfo locally")
    parser.add_argument("--tourn-id", required=True, type=int, help="tournament ID")
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
        payload = get_tourn_info(openid, session_key, args.tourn_id)
    except (ValueError, THUFootballRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = payload if args.full_response else _validation_summary(payload)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
