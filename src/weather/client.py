"""Asynchronous AMap weather Web Service client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from .config import DEFAULT_ENV_FILE, load_api_key
from .errors import (
    WeatherAuthenticationError,
    WeatherConfigurationError,
    WeatherInvalidResponse,
    WeatherNetworkError,
    WeatherQuotaExceeded,
)

DEFAULT_BASE_URL = "https://restapi.amap.com"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_AUTHENTICATION_INFOCODES = frozenset(
    {
        "10001",  # invalid user key
        "10005",  # IP not permitted
        "10006",  # domain not permitted
        "10007",  # invalid user signature
        "10008",  # invalid user signature
        "10009",  # key does not match service
        "10012",  # insufficient privileges
        "10013",  # user key was recycled
    }
)
_QUOTA_INFOCODES = frozenset(
    {
        "10003",  # daily quota exceeded
        "10004",  # access too frequently
        "10010",  # IP query limit exceeded
        "10014",  # QPS exceeded
        "10019",  # request count exceeded
        "10020",  # concurrent QPS exceeded
        "10021",  # account QPS exceeded
    }
)
_TEMPORARY_INFOCODES = frozenset({"10002", "10015", "10016", "10017"})


def _json_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class AmapWeatherClient:
    """Read AMap's daily forecast response without exposing the API key."""

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise WeatherConfigurationError(
                "AMap API key must be a non-empty string",
                stage="configuration",
            )
        if not isinstance(base_url, str) or not base_url.strip():
            raise WeatherConfigurationError(
                "base_url must be a non-empty string",
                stage="configuration",
            )
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            headers={"Accept": "application/json"},
            timeout=self._timeout,
        )
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        *,
        env_path: str | Path = DEFAULT_ENV_FILE,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout | None = None,
    ) -> AmapWeatherClient:
        return cls(
            load_api_key(env_path),
            http_client=http_client,
            base_url=base_url,
            timeout=timeout,
        )

    async def __aenter__(self) -> AmapWeatherClient:
        if self._closed:
            raise WeatherConfigurationError(
                "weather client is already closed",
                stage="configuration",
            )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    @staticmethod
    def _raise_http_error(status_code: int) -> None:
        if status_code in {401, 403}:
            raise WeatherAuthenticationError(
                "AMap rejected the weather API credentials",
                stage="http",
            )
        if status_code == 429:
            raise WeatherQuotaExceeded(
                "AMap rate limited the weather request",
                stage="http",
                retryable=True,
            )
        raise WeatherInvalidResponse(
            f"AMap weather endpoint returned HTTP {status_code}",
            stage="http",
            retryable=status_code >= 500,
        )

    @staticmethod
    def _raise_api_error(payload: Mapping[str, Any]) -> None:
        raw_code = payload.get("infocode")
        error_code = raw_code if isinstance(raw_code, str) else None
        if error_code in _AUTHENTICATION_INFOCODES:
            raise WeatherAuthenticationError(
                "AMap rejected the weather API credentials",
                stage="api",
                error_code=error_code,
            )
        if error_code in _QUOTA_INFOCODES:
            raise WeatherQuotaExceeded(
                "AMap weather quota or frequency limit was exceeded",
                stage="api",
                error_code=error_code,
            )
        if error_code in _TEMPORARY_INFOCODES:
            raise WeatherNetworkError(
                "AMap weather service is temporarily unavailable",
                stage="api",
                retryable=True,
                error_code=error_code,
            )
        raise WeatherInvalidResponse(
            "AMap weather API reported failure",
            stage="api",
            error_code=error_code,
        )

    async def _request_payload(self, adcode: str) -> Mapping[str, Any]:
        if self._closed:
            raise WeatherConfigurationError(
                "weather client is closed",
                stage="configuration",
            )
        url = f"{self._base_url}/v3/weather/weatherInfo"
        parameters = {
            "city": adcode,
            "extensions": "all",
            "output": "JSON",
            "key": self._api_key,
        }
        for attempt in range(2):
            try:
                response = await self._http_client.get(
                    url,
                    params=parameters,
                    headers={"Accept": "application/json"},
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                raise WeatherNetworkError(
                    "AMap weather request timed out",
                    stage="http",
                    retryable=True,
                ) from exc
            except httpx.RequestError as exc:
                if attempt == 0:
                    continue
                raise WeatherNetworkError(
                    "AMap weather request failed",
                    stage="http",
                    retryable=True,
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt == 0:
                continue
            if not 200 <= response.status_code < 300:
                self._raise_http_error(response.status_code)
            try:
                payload = response.json()
            except (UnicodeDecodeError, ValueError) as exc:
                raise WeatherInvalidResponse(
                    "AMap weather endpoint returned invalid JSON",
                    stage="response",
                ) from exc
            if not isinstance(payload, Mapping):
                raise WeatherInvalidResponse(
                    "AMap weather endpoint returned a non-object JSON value",
                    stage="response",
                )
            if str(payload.get("status")) != "1":
                try:
                    self._raise_api_error(payload)
                except WeatherNetworkError:
                    if attempt == 0:
                        continue
                    raise
            return payload
        raise AssertionError("unreachable retry state")

    async def get_forecast(self, adcode: str) -> Mapping[str, Any]:
        """Return the single forecast object for ``adcode``."""

        payload = await self._request_payload(adcode)
        count = _json_count(payload.get("count"))
        forecasts = payload.get("forecasts")
        if count is None or count < 1:
            raise WeatherInvalidResponse(
                "AMap weather response has an invalid count",
                stage="response",
            )
        if (
            isinstance(forecasts, (str, bytes))
            or not isinstance(forecasts, Sequence)
            or not forecasts
        ):
            raise WeatherInvalidResponse(
                "AMap weather response has no forecast objects",
                stage="response",
            )
        if count != len(forecasts):
            raise WeatherInvalidResponse(
                "AMap weather response count does not match its forecasts",
                stage="response",
            )
        matching = [
            item
            for item in forecasts
            if isinstance(item, Mapping) and item.get("adcode") == adcode
        ]
        if len(matching) != 1:
            raise WeatherInvalidResponse(
                "AMap weather response does not uniquely match the requested adcode",
                stage="response",
            )
        return matching[0]
