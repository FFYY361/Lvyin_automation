"""Asynchronous THUFootball HTTP client."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal

import httpx

from .config import load_credentials
from .errors import (
    AuthenticationError,
    ConfigurationError,
    InvalidResponse,
    PermissionError,
    QueryValidationError,
    RateLimited,
    Timeout,
)
from .mappers import (
    map_current_games,
    map_game_detail,
    map_tournament_refs,
    map_tournament_snapshot,
    map_user_probe,
)
from .models import (
    GameDetail,
    GameSummary,
    TournamentRef,
    TournamentSnapshot,
    UserProbe,
)
from .policy import BLACKLISTED_TOURNAMENT_IDS

DEFAULT_BASE_URL = "https://api.thufootball.tech"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})
_REPORT_ASSET_ENDPOINTS = {
    "start": "img_static/START.png",
    "end": "img_static/END.png",
    "legend_goal": "img_static/icon_goal.png",
    "legend_penalty": "img_static/icon_point.png",
    "legend_missed_penalty": "img_static/icon_point_1.png",
    "legend_own_goal": "img_static/icon_w.png",
    "event_on": "img_static/SI.png",
    "event_off": "img_static/SO.png",
    "event_goal": "img_static/G.png",
    "event_penalty": "img_static/PG.png",
    "event_missed_penalty": "img_static/PM.png",
    "event_own_goal": "img_static/OG.png",
    "event_yellow_card": "img_static/YC.png",
    "event_second_yellow_card": "img_static/Y2C.png",
    "event_red_card": "img_static/RC.png",
}
_AUTH_HINTS = (
    "openid",
    "session",
    "login",
    "credential",
    "authenticate",
    "登录",
    "凭据",
    "会话",
    "鉴权",
)
_PERMISSION_HINTS = (
    "permission",
    "authority",
    "forbidden",
    "权限",
    "无权",
)
_RATE_LIMIT_HINTS = ("rate limit", "too many", "频率", "限流")


def _query_error(message: str) -> QueryValidationError:
    return QueryValidationError(message, stage="validation")


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _query_error(f"{name} must be a positive integer")
    return value


def _query_date(value: object, name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime) or not isinstance(value, date):
        raise _query_error(f"{name} must be a date")
    return value


class THUFootballClient:
    """Map verified THUFootball APIs to safe domain objects and assets."""

    def __init__(
        self,
        *,
        openid: str | None = None,
        session_key: str | None = None,
        load_environment: bool = True,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigurationError(
                "base_url must be a non-empty string", stage="configuration"
            )
        if not isinstance(load_environment, bool):
            raise ConfigurationError(
                "load_environment must be a boolean", stage="configuration"
            )

        if openid is None and session_key is None and load_environment:
            loaded_openid, loaded_session_key = load_credentials()
            openid = loaded_openid or None
            session_key = loaded_session_key or None
        self._openid, self._session_key = self._validate_credentials(
            openid, session_key
        )
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            headers={"Accept": "application/json"}, timeout=self._timeout
        )
        self._closed = False

    @staticmethod
    def _validate_credentials(
        openid: str | None, session_key: str | None
    ) -> tuple[str | None, str | None]:
        if openid is not None and (not isinstance(openid, str) or not openid.strip()):
            raise ConfigurationError(
                "openid must be a non-empty string when supplied",
                stage="configuration",
            )
        if session_key is not None and (
            not isinstance(session_key, str) or not session_key.strip()
        ):
            raise ConfigurationError(
                "session_key must be a non-empty string when supplied",
                stage="configuration",
            )
        if (openid is None) != (session_key is None):
            raise ConfigurationError(
                "openid and session_key must be supplied together",
                stage="configuration",
            )
        return openid, session_key

    async def __aenter__(self) -> THUFootballClient:
        if self._closed:
            raise ConfigurationError("client is already closed", stage="configuration")
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    def _auth_parameters(self, *, required: bool) -> dict[str, str]:
        if self._openid is None or self._session_key is None:
            if required:
                raise ConfigurationError(
                    "complete THUFootball credentials are required",
                    stage="configuration",
                )
            return {}
        return {"openid": self._openid, "session_key": self._session_key}

    def _raise_http_error(self, endpoint: str, status_code: int) -> None:
        common = {"stage": "http", "retryable": False}
        if status_code == 401:
            raise AuthenticationError(
                f"{endpoint} rejected the current credentials", **common
            )
        if status_code == 403:
            raise PermissionError(
                f"{endpoint} denied access to the requested resource", **common
            )
        if status_code == 429:
            raise RateLimited(f"{endpoint} rate limited the request", **common)
        raise InvalidResponse(
            f"{endpoint} returned HTTP {status_code}",
            stage="http",
            retryable=status_code >= 500,
        )

    def _raise_api_failure(self, endpoint: str, payload: Mapping[str, Any]) -> None:
        if endpoint == "GetUserInfo":
            raise AuthenticationError(
                "GetUserInfo rejected the current credentials",
                stage="api",
            )

        raw_info = payload.get("info")
        info = raw_info.casefold() if isinstance(raw_info, str) else ""
        if any(hint in info for hint in _RATE_LIMIT_HINTS):
            raise RateLimited(f"{endpoint} rate limited the request", stage="api")
        if any(hint in info for hint in _PERMISSION_HINTS):
            raise PermissionError(
                f"{endpoint} denied access to the requested resource", stage="api"
            )
        if any(hint in info for hint in _AUTH_HINTS):
            raise AuthenticationError(
                f"{endpoint} rejected the current credentials", stage="api"
            )
        raise InvalidResponse(f"{endpoint} reported failure", stage="api")

    async def _request_json(
        self,
        endpoint: str,
        parameters: Mapping[str, str | int],
        *,
        authentication_required: bool,
    ) -> Mapping[str, Any]:
        if self._closed:
            raise ConfigurationError("client is closed", stage="configuration")
        request_parameters: dict[str, str | int] = self._auth_parameters(
            required=authentication_required
        )
        request_parameters.update(parameters)
        url = f"{self._base_url}/{endpoint}"

        for attempt in range(2):
            try:
                response = await self._http_client.get(
                    url,
                    params=request_parameters,
                    headers={"Accept": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                raise Timeout(
                    f"{endpoint} timed out",
                    stage="http",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == 0:
                    continue
                raise InvalidResponse(
                    f"{endpoint} request failed",
                    stage="http",
                    retryable=True,
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                continue
            if not 200 <= response.status_code < 300:
                self._raise_http_error(endpoint, response.status_code)

            try:
                payload = response.json()
            except (UnicodeDecodeError, ValueError) as exc:
                raise InvalidResponse(
                    f"{endpoint} returned invalid JSON", stage="response"
                ) from exc
            if not isinstance(payload, Mapping):
                raise InvalidResponse(
                    f"{endpoint} returned a non-object JSON value",
                    stage="response",
                )
            if payload.get("success") is not True:
                self._raise_api_failure(endpoint, payload)
            return payload

        raise AssertionError("unreachable retry state")

    async def _request_bytes(
        self,
        endpoint: str,
        parameters: Mapping[str, str | int],
        *,
        authentication_required: bool,
        content_type_prefix: str,
    ) -> bytes:
        if self._closed:
            raise ConfigurationError("client is closed", stage="configuration")
        request_parameters: dict[str, str | int] = (
            self._auth_parameters(required=True) if authentication_required else {}
        )
        request_parameters.update(parameters)
        url = f"{self._base_url}/{endpoint}"

        for attempt in range(2):
            try:
                response = await self._http_client.get(
                    url,
                    params=request_parameters,
                    headers={"Accept": f"{content_type_prefix}*"},
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                raise Timeout(
                    f"{endpoint} timed out",
                    stage="http",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == 0:
                    continue
                raise InvalidResponse(
                    f"{endpoint} request failed",
                    stage="http",
                    retryable=True,
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                continue
            if not 200 <= response.status_code < 300:
                self._raise_http_error(endpoint, response.status_code)

            content_type = response.headers.get("content-type", "").casefold()
            if not content_type.startswith(content_type_prefix.casefold()):
                raise InvalidResponse(
                    f"{endpoint} returned an unexpected content type",
                    stage="response",
                )
            if not response.content:
                raise InvalidResponse(
                    f"{endpoint} returned an empty response",
                    stage="response",
                )
            return response.content

        raise AssertionError("unreachable retry state")

    async def get_user_info(self) -> UserProbe:
        payload = await self._request_json(
            "GetUserInfo", {}, authentication_required=True
        )
        return map_user_probe(payload)

    async def get_accessible_tournaments(self) -> list[TournamentRef]:
        payload = await self._request_json(
            "GetMyTournaments", {}, authentication_required=True
        )
        return map_tournament_refs(payload)

    async def get_current_games(
        self,
        *,
        history_bound: date | None = None,
        future_bound: date | None = None,
        game_type: Literal["public", "all"] = "public",
        field_id: int | None = None,
    ) -> list[GameSummary]:
        history = _query_date(history_bound, "history_bound")
        future = _query_date(future_bound, "future_bound")
        if history is not None and future is not None and history > future:
            raise _query_error("history_bound must not be later than future_bound")
        if game_type not in {"public", "all"}:
            raise _query_error("game_type must be either 'public' or 'all'")
        if field_id is not None:
            field_id = _positive_id(field_id, "field_id")

        parameters: dict[str, str | int] = {"type": game_type}
        if history is not None:
            parameters["history_bound"] = history.isoformat()
        if future is not None:
            parameters["future_bound"] = future.isoformat()
        if field_id is not None:
            parameters["field_id"] = field_id
        payload = await self._request_json(
            "GetCurrentGames",
            parameters,
            authentication_required=False,
        )
        return [
            game
            for game in map_current_games(payload)
            if game.tournament_id not in BLACKLISTED_TOURNAMENT_IDS
        ]

    async def get_tournament_info(self, tournament_id: int) -> TournamentSnapshot:
        tournament_id = _positive_id(tournament_id, "tournament_id")
        if tournament_id in BLACKLISTED_TOURNAMENT_IDS:
            raise _query_error(f"tournament_id {tournament_id} is blacklisted")
        payload = await self._request_json(
            "GetTournInfo",
            {"tourn_id": tournament_id},
            authentication_required=True,
        )
        return map_tournament_snapshot(payload, expected_tournament_id=tournament_id)

    async def get_game_info(self, game_id: int) -> GameDetail:
        game_id = _positive_id(game_id, "game_id")
        payload = await self._request_json(
            "GetGameInfo",
            {"game_id": game_id},
            authentication_required=True,
        )
        detail = map_game_detail(payload, expected_game_id=game_id)
        if detail.game.tournament_id in BLACKLISTED_TOURNAMENT_IDS:
            raise _query_error(f"game_id {game_id} belongs to a blacklisted tournament")
        return detail

    async def refresh_game_stats(self, game_id: int) -> None:
        """Recalculate and modify server-side statistics for one game.

        Warning:
            ``OnReStatGameData`` is not a read-only API despite using HTTP GET.
            Callers must require an explicit opt-in before invoking this method.
        """

        game_id = _positive_id(game_id, "game_id")
        await self._request_json(
            "OnReStatGameData",
            {"game_id": game_id},
            authentication_required=True,
        )

    async def get_game_page_code(self, game_id: int) -> bytes:
        """Return the public mini-program QR image embedded in a game report."""

        game_id = _positive_id(game_id, "game_id")
        return await self._request_bytes(
            "GetGamePageCode",
            {"game_id": game_id},
            authentication_required=False,
            content_type_prefix="image/",
        )

    async def get_report_asset(self, name: str) -> bytes:
        """Return one public static image used by the website report canvas."""

        if not isinstance(name, str) or name not in _REPORT_ASSET_ENDPOINTS:
            raise _query_error("name must identify a supported report asset")
        return await self._request_bytes(
            _REPORT_ASSET_ENDPOINTS[name],
            {},
            authentication_required=False,
            content_type_prefix="image/",
        )
