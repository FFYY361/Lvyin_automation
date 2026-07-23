from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from weather import (
    AmapWeatherClient,
    DailyWeather,
    ForecastUnavailable,
    HistoricalWeatherUnsupported,
    InvalidAdcode,
    WeatherAuthenticationError,
    WeatherInvalidResponse,
    WeatherNetworkError,
    WeatherQueryService,
    WeatherQuotaExceeded,
)
from weather.cli import main as cli_main
from weather.config import load_api_key

CHINA = timezone(timedelta(hours=8))
TODAY = date(2099, 7, 22)
TARGET = date(2099, 7, 23)


def _cast(**overrides: str) -> dict[str, str]:
    value = {
        "date": TARGET.isoformat(),
        "week": "4",
        "dayweather": "多云",
        "nightweather": "暴雪",
        "daytemp": "32",
        "nighttemp": "24",
        "daywind": "南",
        "nightwind": "北",
        "daypower": "≤3",
        "nightpower": "12",
    }
    value.update(overrides)
    return value


def _payload(
    *,
    adcode: str = "110108",
    casts: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "status": "1",
        "count": "1",
        "info": "OK",
        "infocode": "10000",
        "forecasts": [
            {
                "city": "海淀区",
                "adcode": adcode,
                "province": "北京",
                "reporttime": "2099-07-22 18:00:00",
                "casts": casts if casts is not None else [_cast()],
            }
        ],
    }


def _service(
    handler,
    *,
    today: date = TODAY,
) -> tuple[WeatherQueryService, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AmapWeatherClient("test-secret-key", http_client=http_client)
    return (
        WeatherQueryService(client, today_provider=lambda: today),
        http_client,
    )


class WeatherServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_amap_forecast_and_ignores_night_weather_and_wind(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_payload())

        service, http_client = _service(handler)
        try:
            result = await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()

        self.assertEqual(
            result,
            DailyWeather(
                adcode="110108",
                region_name="海淀区",
                forecast_date=TARGET,
                condition="多云",
                low_c=24,
                high_c=32,
                wind_direction="南风",
                wind_level="≤3级",
                report_time=datetime(2099, 7, 22, 18, tzinfo=CHINA),
            ),
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.params["city"], "110108")
        self.assertEqual(requests[0].url.params["extensions"], "all")
        self.assertEqual(requests[0].url.params["output"], "JSON")

    async def test_temperature_uses_min_and_max_when_day_is_colder(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_payload(casts=[_cast(daytemp="8", nighttemp="18")]),
            )

        service, http_client = _service(handler)
        try:
            result = await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()
        self.assertEqual((result.low_c, result.high_c), (8, 18))

    async def test_normalizes_supported_wind_directions_and_levels(self) -> None:
        cases = (
            ("南", "≤3", "南风", "≤3级"),
            ("东", "1-3", "东风", "1-3级"),
            ("西", "3-4", "西风", "3-4级"),
            ("无风向", "4", "微风", "4级"),
            ("旋转不定", "12", "阵风", "12级"),
        )
        for raw_direction, raw_level, direction, level in cases:
            with self.subTest(raw_direction=raw_direction):
                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        200,
                        json=_payload(
                            casts=[
                                _cast(
                                    daywind=raw_direction,
                                    daypower=raw_level,
                                )
                            ]
                        ),
                    )

                service, http_client = _service(handler)
                try:
                    result = await service.get_weather("110108", TARGET)
                finally:
                    await http_client.aclose()
                self.assertEqual(result.wind_direction, direction)
                self.assertEqual(result.wind_level, level)

    async def test_rejects_invalid_adcode_and_history_before_http(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_payload())

        service, http_client = _service(handler)
        try:
            for value in ("11010", "abcdef", " 110108", 110108):
                with self.subTest(adcode=value), self.assertRaises(InvalidAdcode):
                    await service.get_weather(value, TARGET)  # type: ignore[arg-type]
            with self.assertRaises(HistoricalWeatherUnsupported):
                await service.get_weather("110108", date(2099, 7, 21))
        finally:
            await http_client.aclose()
        self.assertEqual(calls, 0)

    async def test_missing_date_is_forecast_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_payload(casts=[_cast(date="2099-07-24")]),
            )

        service, http_client = _service(handler)
        try:
            with self.assertRaises(ForecastUnavailable):
                await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()

    async def test_rejects_unknown_wind_and_malformed_temperature(self) -> None:
        cases = (
            _cast(daywind="东南偏东"),
            _cast(daypower="大风"),
            _cast(daypower="4-3"),
            _cast(daypower="13"),
            _cast(daytemp="32.5"),
        )
        for cast in cases:
            with self.subTest(cast=cast):
                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, json=_payload(casts=[cast]))

                service, http_client = _service(handler)
                try:
                    with self.assertRaises(WeatherInvalidResponse):
                        await service.get_weather("110108", TARGET)
                finally:
                    await http_client.aclose()

    async def test_classifies_api_authentication_and_quota_errors(self) -> None:
        cases = (
            ("10001", WeatherAuthenticationError),
            ("10003", WeatherQuotaExceeded),
        )
        for infocode, expected in cases:
            with self.subTest(infocode=infocode):
                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        200,
                        json={
                            "status": "0",
                            "count": "0",
                            "info": "failure",
                            "infocode": infocode,
                        },
                    )

                service, http_client = _service(handler)
                try:
                    with self.assertRaises(expected) as caught:
                        await service.get_weather("110108", TARGET)
                finally:
                    await http_client.aclose()
                self.assertEqual(caught.exception.error_code, infocode)
                self.assertNotIn("test-secret-key", str(caught.exception))

    async def test_retries_one_temporary_http_failure(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, text="temporary")
            return httpx.Response(200, json=_payload())

        service, http_client = _service(handler)
        try:
            result = await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()
        self.assertEqual(result.condition, "多云")
        self.assertEqual(calls, 2)

    async def test_retries_one_temporary_api_failure(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "status": "0",
                        "count": "0",
                        "info": "SERVICE_NOT_AVAILABLE",
                        "infocode": "10002",
                    },
                )
            return httpx.Response(200, json=_payload())

        service, http_client = _service(handler)
        try:
            result = await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()
        self.assertEqual(result.condition, "多云")
        self.assertEqual(calls, 2)

    async def test_timeout_retries_once_and_raises_secret_safe_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("secret low-level message", request=request)

        service, http_client = _service(handler)
        try:
            with self.assertRaises(WeatherNetworkError) as caught:
                await service.get_weather("110108", TARGET)
        finally:
            await http_client.aclose()
        self.assertEqual(calls, 2)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("secret", str(caught.exception))

    async def test_rejects_non_json_and_adcode_mismatch(self) -> None:
        responses = (
            httpx.Response(200, text="not-json"),
            httpx.Response(200, json=_payload(adcode="110000")),
        )
        for response in responses:
            with self.subTest(response=response):
                def handler(request: httpx.Request) -> httpx.Response:
                    return response

                service, http_client = _service(handler)
                try:
                    with self.assertRaises(WeatherInvalidResponse):
                        await service.get_weather("110108", TARGET)
                finally:
                    await http_client.aclose()


class WeatherConfigTests(unittest.TestCase):
    def test_env_file_is_allowlisted_and_process_value_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "AMAP_WEATHER_API_KEY=file-key\nUNRELATED=ignored\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"AMAP_WEATHER_API_KEY": "process-key"},
                clear=True,
            ):
                self.assertEqual(load_api_key(path), "process-key")
                self.assertNotIn("UNRELATED", os.environ)


class _FakeService:
    async def get_weather(self, adcode: str, target_date: date) -> DailyWeather:
        return DailyWeather(
            adcode=adcode,
            region_name="海淀区",
            forecast_date=target_date,
            condition="晴",
            low_c=20,
            high_c=30,
            wind_direction="微风",
            wind_level="≤3级",
            report_time=datetime(2099, 7, 22, 18, tzinfo=CHINA),
        )


class _Context:
    async def __aenter__(self) -> _FakeService:
        return _FakeService()

    async def __aexit__(self, *args: object) -> None:
        return None


class WeatherCliTests(unittest.TestCase):
    def test_query_outputs_stable_json(self) -> None:
        stdout = StringIO()
        with (
            patch(
                "weather.cli.WeatherQueryService.from_environment",
                return_value=_Context(),
            ),
            redirect_stdout(stdout),
        ):
            status = cli_main(
                ["query", "--adcode", "110108", "--date", TARGET.isoformat()]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["weather"]["condition"], "晴")
        self.assertEqual(payload["weather"]["report_time"], "2099-07-22T18:00:00+08:00")

    def test_error_output_does_not_expose_key(self) -> None:
        stderr = StringIO()
        with (
            patch.dict(os.environ, {"AMAP_WEATHER_API_KEY": ""}, clear=True),
            redirect_stderr(stderr),
        ):
            status = cli_main(
                ["query", "--adcode", "110108", "--date", TARGET.isoformat()]
            )
        payload = json.loads(stderr.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(payload["error"], "WeatherConfigurationError")
        self.assertNotIn("key=", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
