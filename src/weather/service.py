"""Public daily-weather query service."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .client import DEFAULT_BASE_URL, AmapWeatherClient
from .config import DEFAULT_ENV_FILE
from .errors import (
    ForecastUnavailable,
    HistoricalWeatherUnsupported,
    InvalidAdcode,
    WeatherInvalidResponse,
)
from .models import DailyWeather

CHINA_TIMEZONE = timezone(timedelta(hours=8))
_TEMPERATURE = re.compile(r"^-?\d+$")
_CARDINAL_WINDS = frozenset(
    {"东", "南", "西", "北", "东北", "东南", "西南", "西北"}
)
_WIND_LEVEL = re.compile(r"^(?:≤(?P<upper>\d{1,2})|(?P<start>\d{1,2})(?:-(?P<end>\d{1,2}))?)$")


def _china_today() -> date:
    return datetime.now(CHINA_TIMEZONE).date()


def _required_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeatherInvalidResponse(
            f"AMap weather response has an invalid field at {path}",
            stage="schema",
        )
    return value.strip()


def _temperature(value: object, path: str) -> int:
    raw = _required_string(value, path)
    if _TEMPERATURE.fullmatch(raw) is None:
        raise WeatherInvalidResponse(
            f"AMap weather response has an invalid field at {path}",
            stage="schema",
        )
    return int(raw)


def _forecast_date(value: object, path: str) -> date:
    raw = _required_string(value, path)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise WeatherInvalidResponse(
            f"AMap weather response has an invalid field at {path}",
            stage="schema",
        ) from exc
    if parsed.isoformat() != raw:
        raise WeatherInvalidResponse(
            f"AMap weather response has an invalid field at {path}",
            stage="schema",
        )
    return parsed


def _report_time(value: object) -> datetime:
    raw = _required_string(value, "$.forecasts[0].reporttime")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise WeatherInvalidResponse(
            "AMap weather response has an invalid field at "
            "$.forecasts[0].reporttime",
            stage="schema",
        ) from exc
    return parsed.replace(tzinfo=CHINA_TIMEZONE)


def _wind_direction(value: object) -> str:
    raw = _required_string(value, "$.forecasts[0].casts[].daywind")
    if raw in _CARDINAL_WINDS:
        return raw + "风"
    if raw == "无风向":
        return "微风"
    if raw == "旋转不定":
        return "阵风"
    raise WeatherInvalidResponse(
        "AMap weather response has an invalid field at "
        "$.forecasts[0].casts[].daywind",
        stage="schema",
    )


def _wind_level(value: object) -> str:
    raw = _required_string(value, "$.forecasts[0].casts[].daypower")
    matched = _WIND_LEVEL.fullmatch(raw)
    if matched is None:
        raise WeatherInvalidResponse(
            "AMap weather response has an invalid field at "
            "$.forecasts[0].casts[].daypower",
            stage="schema",
        )
    upper = matched.group("upper")
    start = matched.group("start")
    end = matched.group("end")
    levels = [int(item) for item in (upper, start, end) if item is not None]
    if any(level > 12 for level in levels) or (
        start is not None and end is not None and int(start) > int(end)
    ):
        raise WeatherInvalidResponse(
            "AMap weather response has an invalid field at "
            "$.forecasts[0].casts[].daypower",
            stage="schema",
        )
    return raw + "级"


class WeatherQueryService:
    """Map an AMap forecast to one stable daily-weather object."""

    def __init__(
        self,
        client: AmapWeatherClient,
        *,
        close_client: bool = False,
        today_provider: Callable[[], date] = _china_today,
    ) -> None:
        self._client = client
        self._close_client = close_client
        self._today_provider = today_provider

    @classmethod
    def from_environment(
        cls,
        *,
        env_path: str | Path = DEFAULT_ENV_FILE,
        base_url: str = DEFAULT_BASE_URL,
    ) -> WeatherQueryService:
        return cls(
            AmapWeatherClient.from_environment(
                env_path=env_path,
                base_url=base_url,
            ),
            close_client=True,
        )

    async def __aenter__(self) -> WeatherQueryService:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._close_client:
            await self._client.aclose()

    @staticmethod
    def _validate_adcode(value: object) -> str:
        if not isinstance(value, str) or len(value) != 6 or not value.isdigit():
            raise InvalidAdcode(
                "adcode must be a six-digit string",
                stage="validation",
            )
        return value

    @staticmethod
    def _validate_target_date(value: object) -> date:
        if isinstance(value, datetime) or not isinstance(value, date):
            raise ForecastUnavailable(
                "target_date must be a date",
                stage="validation",
            )
        return value

    async def get_weather(self, adcode: str, target_date: date) -> DailyWeather:
        resolved_adcode = self._validate_adcode(adcode)
        resolved_date = self._validate_target_date(target_date)
        if resolved_date < self._today_provider():
            raise HistoricalWeatherUnsupported(
                "historical weather is not supported by AMap",
                stage="validation",
            )

        forecast = await self._client.get_forecast(resolved_adcode)
        region_name = _required_string(forecast.get("city"), "$.forecasts[0].city")
        report_time = _report_time(forecast.get("reporttime"))
        casts = forecast.get("casts")
        if isinstance(casts, (str, bytes)) or not isinstance(casts, Sequence):
            raise WeatherInvalidResponse(
                "AMap weather response has an invalid field at $.forecasts[0].casts",
                stage="schema",
            )

        matches: list[Mapping[str, Any]] = []
        for index, raw_cast in enumerate(casts):
            if not isinstance(raw_cast, Mapping):
                raise WeatherInvalidResponse(
                    "AMap weather response has an invalid field at "
                    f"$.forecasts[0].casts[{index}]",
                    stage="schema",
                )
            cast_date = _forecast_date(
                raw_cast.get("date"),
                f"$.forecasts[0].casts[{index}].date",
            )
            if cast_date == resolved_date:
                matches.append(raw_cast)
        if not matches:
            raise ForecastUnavailable(
                f"AMap forecast does not include {resolved_date.isoformat()}",
                stage="forecast",
            )
        if len(matches) != 1:
            raise WeatherInvalidResponse(
                "AMap weather response contains duplicate forecast dates",
                stage="schema",
            )

        selected = matches[0]
        day_temp = _temperature(
            selected.get("daytemp"),
            "$.forecasts[0].casts[].daytemp",
        )
        night_temp = _temperature(
            selected.get("nighttemp"),
            "$.forecasts[0].casts[].nighttemp",
        )
        return DailyWeather(
            adcode=resolved_adcode,
            region_name=region_name,
            forecast_date=resolved_date,
            condition=_required_string(
                selected.get("dayweather"),
                "$.forecasts[0].casts[].dayweather",
            ),
            low_c=min(day_temp, night_temp),
            high_c=max(day_temp, night_temp),
            wind_direction=_wind_direction(selected.get("daywind")),
            wind_level=_wind_level(selected.get("daypower")),
            report_time=report_time,
        )
