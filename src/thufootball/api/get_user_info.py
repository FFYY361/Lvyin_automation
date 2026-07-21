"""Local wrapper and validation entry point for ``GetUserInfo``."""

from __future__ import annotations

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
        load_credentials,
        request_json,
    )
else:  # Support ``python src/thufootball/api/get_user_info.py``.
    from utils import (  # type: ignore[no-redef]
        DEFAULT_BASE_URL,
        DEFAULT_TIMEOUT,
        THUFootballRequestError,
        _authenticated_parameters,
        load_credentials,
        request_json,
    )


def get_user_info(
    openid: str,
    session_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Call ``GetUserInfo`` with the supplied TAFA login credentials."""

    return request_json(
        "GetUserInfo",
        _authenticated_parameters(openid, session_key),
        base_url=base_url,
        timeout=timeout,
    )


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the fields needed to decide whether validation passed."""

    return {
        "endpoint": "GetUserInfo",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "user_registered": payload.get("user_registered"),
        "response_fields": sorted(str(key) for key in payload),
    }


def main() -> int:
    """Validate the current credentials stored in environment variables."""

    openid, session_key = load_credentials()
    if not openid or not session_key:
        print(
            "Please set THUFOOTBALL_OPENID and THUFOOTBALL_SESSION_KEY "
            "in the current shell.",
            file=sys.stderr,
        )
        return 2

    try:
        payload = get_user_info(openid, session_key)
    except (ValueError, THUFootballRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
