"""Local wrapper and validation entry point for ``GetMyTournaments``."""

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
        load_credentials,
        request_json,
    )
else:  # Support ``python src/thufootball/api/get_my_tournaments.py``.
    from utils import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        load_credentials,
        request_json,
    )


def get_my_tournaments(
    openid: str,
    session_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Return tournaments associated with the authenticated user."""

    if not isinstance(openid, str) or not openid.strip():
        raise ValueError("openid must be a non-empty string")
    if not isinstance(session_key, str) or not session_key.strip():
        raise ValueError("session_key must be a non-empty string")
    return request_json(
        "GetMyTournaments",
        {"openid": openid, "session_key": session_key},
        base_url=base_url,
        timeout=timeout,
    )


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    tournaments = payload.get("tourns")
    if not isinstance(tournaments, list):
        tournaments = []
    visible_count = sum(
        item.get("visible") is True for item in tournaments if isinstance(item, Mapping)
    )
    hidden_count = sum(
        item.get("visible") is False
        for item in tournaments
        if isinstance(item, Mapping)
    )
    return {
        "endpoint": "GetMyTournaments",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "tournament_count": len(tournaments),
        "visible_count": visible_count,
        "hidden_count": hidden_count,
        "response_fields": sorted(str(key) for key in payload),
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate GetMyTournaments locally")
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
        payload = get_my_tournaments(openid, session_key)
    except (ValueError, THUFootballRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = payload if args.full_response else _validation_summary(payload)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
