"""Command-line access to :class:`weather.WeatherQueryService`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date

from .errors import WeatherError
from .models import DailyWeather
from .service import WeatherQueryService


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weather",
        description="Query one AMap daily forecast by adcode and date",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    query = commands.add_parser("query", help="query one daily forecast")
    query.add_argument("--adcode", required=True)
    query.add_argument("--date", required=True, type=_date)
    return parser


def _weather_payload(value: DailyWeather) -> dict[str, object]:
    return {
        "adcode": value.adcode,
        "region_name": value.region_name,
        "forecast_date": value.forecast_date.isoformat(),
        "condition": value.condition,
        "low_c": value.low_c,
        "high_c": value.high_c,
        "wind_direction": value.wind_direction,
        "wind_level": value.wind_level,
        "report_time": value.report_time.isoformat(),
    }


async def _run(args: argparse.Namespace) -> DailyWeather:
    async with WeatherQueryService.from_environment() as service:
        return await service.get_weather(args.adcode, args.date)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        weather = asyncio.run(_run(args))
    except WeatherError as exc:
        payload: dict[str, object] = {
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc),
            "stage": exc.stage,
            "retryable": exc.retryable,
        }
        if exc.error_code is not None:
            payload["error_code"] = exc.error_code
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "ok", "weather": _weather_payload(weather)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
