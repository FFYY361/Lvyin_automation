"""Shared helpers for the local THUFootball HTTP API wrappers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.thufootball.tech"
DEFAULT_TIMEOUT = 15.0
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
CREDENTIAL_ENV_NAMES = ("THUFOOTBALL_OPENID", "THUFOOTBALL_SESSION_KEY")


class THUFootballRequestError(RuntimeError):
    """Raised when THUFootball cannot return a usable JSON response."""


def _authenticated_parameters(openid: object, session_key: object) -> dict[str, str]:
    if not isinstance(openid, str) or not openid.strip():
        raise ValueError("openid must be a non-empty string")
    if not isinstance(session_key, str) or not session_key.strip():
        raise ValueError("session_key must be a non-empty string")
    return {"openid": openid, "session_key": session_key}


def _optional_authentication_parameters(
    openid: object,
    session_key: object,
) -> dict[str, str]:
    if openid is not None and (not isinstance(openid, str) or not openid.strip()):
        raise ValueError("openid must be a non-empty string when supplied")
    if session_key is not None and (
        not isinstance(session_key, str) or not session_key.strip()
    ):
        raise ValueError("session_key must be a non-empty string when supplied")
    if (openid is None) != (session_key is None):
        raise ValueError("openid and session_key must be supplied together")
    if isinstance(openid, str) and isinstance(session_key, str):
        return {"openid": openid, "session_key": session_key}
    return {}


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load THUFootball credentials from a simple ``.env`` file.

    Existing process environment variables take precedence. Other variables
    in the file are deliberately ignored because the API wrappers only need
    the two THUFootball credentials.
    """

    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        name, value = stripped.split("=", 1)
        name = name.strip()
        if name not in CREDENTIAL_ENV_NAMES:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def load_credentials(path: Path = DEFAULT_ENV_FILE) -> tuple[str, str]:
    """Load and return ``(openid, session_key)``; missing values are empty."""

    load_env_file(path)
    return (
        os.environ.get("THUFOOTBALL_OPENID", ""),
        os.environ.get("THUFOOTBALL_SESSION_KEY", ""),
    )


def request_json(
    endpoint: str,
    parameters: Mapping[str, str | int],
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send a GET request and require a JSON object response."""

    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty string")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    query = urlencode(parameters)
    url = f"{base_url.rstrip('/')}/{endpoint}?{query}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        raise THUFootballRequestError(f"{endpoint} returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise THUFootballRequestError(f"{endpoint} request failed") from exc
    except (TimeoutError, OSError) as exc:
        raise THUFootballRequestError(f"{endpoint} request failed") from exc

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise THUFootballRequestError(f"{endpoint} returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise THUFootballRequestError(f"{endpoint} returned a non-object JSON value")
    return dict(payload)
