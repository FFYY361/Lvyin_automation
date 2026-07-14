"""Local wrapper and validation entry point for ``GetUserInfo``."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.thufootball.tech"
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_CREDENTIAL_NAMES = ("THUFOOTBALL_OPENID", "THUFOOTBALL_SESSION_KEY")


class THUFootballRequestError(RuntimeError):
    """Raised when THUFootball cannot return a usable JSON response."""


def get_user_info(
    openid: str,
    session_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Call ``GetUserInfo`` with the supplied TAFA login credentials.

    Credentials are encoded as query parameters by :func:`urlencode`, so a
    session key containing characters such as ``+``, ``/`` or ``=`` is sent
    without being corrupted.
    """

    if not isinstance(openid, str) or not openid.strip():
        raise ValueError("openid must be a non-empty string")
    if not isinstance(session_key, str) or not session_key.strip():
        raise ValueError("session_key must be a non-empty string")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    query = urlencode({"openid": openid, "session_key": session_key})
    url = f"{base_url.rstrip('/')}/GetUserInfo?{query}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        raise THUFootballRequestError(
            f"GetUserInfo returned HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise THUFootballRequestError("GetUserInfo request failed") from exc
    except (TimeoutError, OSError) as exc:
        raise THUFootballRequestError("GetUserInfo request failed") from exc

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise THUFootballRequestError(
            "GetUserInfo returned invalid JSON"
        ) from exc

    if not isinstance(payload, Mapping):
        raise THUFootballRequestError("GetUserInfo returned a non-object JSON value")

    return dict(payload)


def _validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the fields needed to decide whether validation passed."""

    return {
        "endpoint": "GetUserInfo",
        "success": payload.get("success"),
        "info": payload.get("info"),
        "user_registered": payload.get("user_registered"),
        "response_fields": sorted(str(key) for key in payload),
    }


def _load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load THUFootball credentials from a simple local ``.env`` file.

    Existing process environment variables take precedence. Only the two
    credential names used by this module are read; other entries are ignored.
    """

    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in _CREDENTIAL_NAMES:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def main() -> int:
    """Validate the current credentials stored in environment variables."""

    _load_env_file()
    openid = os.environ.get("THUFOOTBALL_OPENID", "")
    session_key = os.environ.get("THUFOOTBALL_SESSION_KEY", "")
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
