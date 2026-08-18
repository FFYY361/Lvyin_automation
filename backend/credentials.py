"""Runtime THUFootball credential persistence for the website."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from thufootball import (
    AuthenticationError,
    ConfigurationError,
    THUFootballClient,
    THUFootballError,
)
from thufootball.config import CREDENTIAL_ENV_NAMES

TAFA_USERNAME_ENV = "TAFA_USERNAME"
TAFA_PASSWORD_ENV = "TAFA_PASSWORD"
TAFA_LOGIN_URL = "https://www.tafa.org.cn/member/login.php"
TAFA_CREDENTIAL_URL = "https://www.tafa.org.cn/member/ref_db_new.php"
MANUAL_CREDENTIAL_HINT = (
    "THUFootball credentials are unavailable after automatic refresh; "
    "manually update OPENID and SESSION_KEY in Settings"
)

CredentialFetcher = Callable[[str, str], Awaitable[tuple[str, str]]]
CredentialRefresher = Callable[[], Awaitable[tuple[str, str]]]


class AutomaticCredentialError(RuntimeError):
    """Raised when TAFA cannot provide a valid THUFootball credential pair."""


def _extract_javascript_string(html: str, name: str) -> str:
    pattern = re.compile(
        rf"\bvar\s+{re.escape(name)}\s*=\s*" r'("(?:\\.|[^"\\])*")\s*;'
    )
    match = pattern.search(html)
    if match is None:
        raise AutomaticCredentialError(f"TAFA did not return {name}")
    try:
        value = json.loads(match.group(1))
    except ValueError as exc:
        raise AutomaticCredentialError(f"TAFA returned an invalid {name}") from exc
    if not isinstance(value, str) or not value.strip():
        raise AutomaticCredentialError(f"TAFA returned an empty {name}")
    return value


async def fetch_tafa_credentials(
    username: str,
    password: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str]:
    """Log in to TAFA and read its currently stored THUFootball credentials."""

    if not username.strip() or not password:
        raise AutomaticCredentialError("TAFA username and password are required")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    try:
        login_page = await client.get(TAFA_LOGIN_URL)
        login_page.raise_for_status()
        login = await client.post(
            TAFA_LOGIN_URL,
            data={
                "username": username,
                "password": password,
                "type": "",
                "btnsubmit": "",
            },
            headers={"Referer": TAFA_LOGIN_URL},
        )
        login.raise_for_status()
        response = await client.get(TAFA_CREDENTIAL_URL)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AutomaticCredentialError("TAFA credential request failed") from exc
    finally:
        if owns_client:
            await client.aclose()

    if 'id="flogin"' in response.text:
        raise AutomaticCredentialError("TAFA login failed")
    return (
        _extract_javascript_string(response.text, "USER_OPENID"),
        _extract_javascript_string(response.text, "USER_SESSION_KEY"),
    )


def mask_secret(value: str) -> str | None:
    """Return a useful status hint without exposing a credential."""

    if not value:
        return None
    visible = value[-4:] if len(value) > 4 else value[-1:]
    return f"{'*' * 8}{visible}"


def credential_status() -> dict[str, object]:
    """Describe the credentials currently used by newly created clients."""

    openid, session_key = (
        os.environ.get(name, "") for name in CREDENTIAL_ENV_NAMES
    )
    return {
        "configured": bool(openid and session_key),
        "openid_masked": mask_secret(openid),
        "session_key_masked": mask_secret(session_key),
    }


def persist_credentials(path: Path, openid: str, session_key: str) -> None:
    """Atomically replace both credential entries in a dotenv file."""

    original = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    had_final_newline = original.endswith(("\n", "\r"))
    replacements = dict(zip(CREDENTIAL_ENV_NAMES, (openid, session_key), strict=True))
    found: set[str] = set()
    lines: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name in replacements:
                lines.append(f"{name}={replacements[name]}")
                found.add(name)
                continue
        lines.append(line)
    for name in CREDENTIAL_ENV_NAMES:
        if name not in found:
            lines.append(f"{name}={replacements[name]}")

    content = newline.join(lines)
    if lines and (had_final_newline or not original):
        content += newline

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activate_credentials(openid: str, session_key: str) -> None:
    """Make a persisted credential pair visible to future clients."""

    os.environ[CREDENTIAL_ENV_NAMES[0]] = openid
    os.environ[CREDENTIAL_ENV_NAMES[1]] = session_key


class AutomaticCredentialManager:
    """Fetch, validate, and activate runtime credentials obtained from TAFA."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        fetcher: CredentialFetcher | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._fetcher = fetcher or fetch_tafa_credentials
        self._lock = asyncio.Lock()

    @classmethod
    def from_environment(cls) -> "AutomaticCredentialManager":
        return cls(
            os.environ.get(TAFA_USERNAME_ENV, ""),
            os.environ.get(TAFA_PASSWORD_ENV, ""),
        )

    @property
    def configured(self) -> bool:
        return bool(self._username.strip() and self._password)

    async def refresh(self) -> tuple[str, str]:
        if not self.configured:
            raise AutomaticCredentialError(
                "TAFA_USERNAME and TAFA_PASSWORD are required for automatic refresh"
            )

        async with self._lock:
            openid, session_key = await self._fetcher(
                self._username, self._password
            )
            try:
                async with THUFootballClient(
                    openid=openid,
                    session_key=session_key,
                    load_environment=False,
                ) as client:
                    probe = await client.get_user_info()
            except THUFootballError as exc:
                raise AutomaticCredentialError(
                    "TAFA returned credentials rejected by THUFootball"
                ) from exc
            if not probe.user_registered:
                raise AutomaticCredentialError(
                    "TAFA returned an unregistered THUFootball user"
                )
            activate_credentials(openid, session_key)
            return openid, session_key


class AutoRefreshingTHUFootballClient(THUFootballClient):
    """Retry rejected authenticated requests with credentials refreshed by TAFA."""

    def __init__(
        self,
        *,
        credential_refresher: CredentialRefresher,
        authentication_retries: int = 2,
        **kwargs: Any,
    ) -> None:
        if (
            isinstance(authentication_retries, bool)
            or not isinstance(authentication_retries, int)
            or authentication_retries < 0
        ):
            raise ConfigurationError(
                "authentication_retries must be a non-negative integer",
                stage="configuration",
            )
        super().__init__(**kwargs)
        self._credential_refresher = credential_refresher
        self._authentication_retries = authentication_retries
        self._credential_generation = 0
        self._credential_refresh_lock = asyncio.Lock()

    async def _refresh_if_current(self, expected_generation: int) -> None:
        async with self._credential_refresh_lock:
            if self._credential_generation != expected_generation:
                return
            openid, session_key = await self._credential_refresher()
            self._openid, self._session_key = self._validate_credentials(
                openid, session_key
            )
            self._credential_generation += 1

    async def _request_json(
        self,
        endpoint: str,
        parameters: Mapping[str, str | int],
        *,
        authentication_required: bool,
    ) -> Mapping[str, Any]:
        request_generation = self._credential_generation
        try:
            return await super()._request_json(
                endpoint,
                parameters,
                authentication_required=authentication_required,
            )
        except (AuthenticationError, ConfigurationError) as exc:
            last_error: Exception = exc

        for _ in range(self._authentication_retries):
            try:
                await self._refresh_if_current(request_generation)
            except Exception as exc:
                last_error = exc
                continue

            request_generation = self._credential_generation
            try:
                return await super()._request_json(
                    endpoint,
                    parameters,
                    authentication_required=authentication_required,
                )
            except (AuthenticationError, ConfigurationError) as exc:
                last_error = exc

        raise AuthenticationError(
            MANUAL_CREDENTIAL_HINT,
            stage="authentication",
            retryable=False,
        ) from last_error
