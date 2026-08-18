from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from thufootball import (
    ConfigurationError,
    GameDetail,
    GameEvent,
    GameStatus,
    GameSummary,
    ReportSettings,
    ReportValidationError,
    THUFootballClient,
    THUFootballReportService,
    prepare_game_report,
)
from thufootball.reports import _REPORT_ASSET_CACHE, _build_report_html, _report_payload

_TINY_PNG_BUFFER = BytesIO()
Image.new("RGB", (2, 2), "white").save(_TINY_PNG_BUFFER, format="PNG")
_TINY_PNG = _TINY_PNG_BUFFER.getvalue()
_REPORT_PNG_BUFFER = BytesIO()
Image.new("RGB", (1600, 1646), "white").save(
    _REPORT_PNG_BUFFER,
    format="PNG",
)
_REPORT_PNG = _REPORT_PNG_BUFFER.getvalue()
_REPORT_ASSET_NAMES = (
    "start",
    "end",
    "legend_goal",
    "legend_penalty",
    "legend_missed_penalty",
    "legend_own_goal",
    "event_on",
    "event_off",
    "event_goal",
    "event_penalty",
    "event_missed_penalty",
    "event_own_goal",
    "event_yellow_card",
    "event_second_yellow_card",
    "event_red_card",
)


def _summary() -> GameSummary:
    kickoff_utc = datetime(2026, 4, 19, 5, 0, tzinfo=UTC)
    return GameSummary(
        game_id=4245,
        tournament_id=122,
        tournament_name="2025~2026马杯男足甲级",
        kickoff_utc=kickoff_utc,
        kickoff_local=kickoff_utc.astimezone(),
        status=GameStatus.FINISHED,
        record_active=True,
        valid=True,
        stage="决赛",
        group_name=None,
        round=None,
        home_tournament_team_id=1735,
        home_team_id=48,
        home_team_name="车辆与运载学院",
        away_tournament_team_id=1745,
        away_team_id=163,
        away_team_name="未央书院",
        home_score=1,
        away_score=0,
        result_text="1:0",
        penalty_shootout=True,
        home_penalty=0,
        away_penalty=0,
        home_abandon=False,
        away_abandon=False,
        field_name="东大操场",
        home_team_brief_name="汽车",
        away_team_brief_name="未央",
        tournament_report_name="马甲",
        home_team_report_name="汽车",
        away_team_report_name="未央",
    )


def _event(
    event_id: int,
    *,
    side: str,
    event_type: str,
    minute: int,
    player_id: int,
    kit_number: int,
    name: str,
    note: str | None = None,
    stoppage_minute: int = 0,
) -> GameEvent:
    tournament_team_id = 1735 if side == "home" else 1745
    return GameEvent(
        event_id=event_id,
        tournament_team_id=tournament_team_id,
        tournament_team_player_id=player_id + 10_000,
        player_id=player_id,
        player_name=name,
        side=side,  # type: ignore[arg-type]
        event_type=event_type,
        minute=minute,
        stoppage_minute=stoppage_minute,
        kit_number=kit_number,
        during_penalty_shootout=False,
        valid=True,
        note=note,
        sequence=event_id,
        time_ordering=0,
    )


def _detail() -> GameDetail:
    starters = (
        ("away", 5, "李为峰", None),
        ("away", 6, "赵泽石", "队长"),
        ("away", 7, "李梓伊", None),
        ("away", 9, "丰飞扬", None),
        ("away", 10, "成浩铭", None),
        ("away", 11, "萧远峰", None),
        ("away", 12, "周健和", None),
        ("away", 19, "蒋辰昊", None),
        ("away", 25, "李昊融", None),
        ("away", 27, "许涵铭", None),
        ("away", 33, "许雨轩", None),
        ("home", 1, "冀明泽", None),
        ("home", 3, "敖琦迩", "队长"),
        ("home", 7, "蹇浩宇", "足特"),
        ("home", 9, "邓嵎木", None),
        ("home", 10, "康庭源", "足特"),
        ("home", 21, "郭硕海", None),
        ("home", 22, "王一凡", "足特"),
        ("home", 27, "郑路军", "足特"),
        ("home", 41, "夏勇", "教工"),
        ("home", 66, "王子丁", "足特"),
        ("home", 77, "杜雄飞", None),
    )
    timeline = (
        ("home", "YELLOWCARD", 42, 0, 66, "王子丁", "足特"),
        ("home", "YELLOWCARD", 54, 0, 21, "郭硕海", None),
        ("away", "OFF", 73, 0, 33, "许雨轩", None),
        ("away", "ON", 73, 0, 29, "常斯盛", None),
        ("home", "GOAL", 76, 0, 22, "王一凡", "足特"),
        ("home", "YELLOWCARD", 78, 0, 21, "郭硕海", None),
        ("away", "YELLOWCARD", 78, 0, 7, "李梓伊", None),
        ("home", "OFF", 80, 0, 66, "王子丁", "足特"),
        ("home", "ON", 80, 0, 19, "刘奕", "足特"),
        ("away", "YELLOWCARD", 80, 1, 29, "常斯盛", None),
        ("home", "YELLOWCARD", 80, 3, 3, "敖琦迩", "队长"),
    )
    events: list[GameEvent] = []
    for index, (side, kit_number, name, note) in enumerate(starters, start=1):
        events.append(
            _event(
                index,
                side=side,
                event_type="START",
                minute=0,
                player_id=(1_000 if side == "home" else 2_000) + kit_number,
                kit_number=kit_number,
                name=name,
                note=note,
            )
        )
    for offset, (
        side,
        event_type,
        minute,
        stoppage_minute,
        kit_number,
        name,
        note,
    ) in enumerate(timeline, start=len(events) + 1):
        events.append(
            _event(
                offset,
                side=side,
                event_type=event_type,
                minute=minute,
                stoppage_minute=stoppage_minute,
                player_id=(1_000 if side == "home" else 2_000) + kit_number,
                kit_number=kit_number,
                name=name,
                note=note,
            )
        )
    return GameDetail(
        game=_summary(),
        events=tuple(events),
        referees=(),
        players_per_side=11,
    )


class _FakeClient:
    def __init__(self, detail: GameDetail | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.detail = detail

    async def refresh_game_stats(self, game_id: int) -> None:
        self.calls.append(("refresh", game_id))

    async def get_game_info(self, game_id: int) -> GameDetail:
        self.calls.append(("detail", game_id))
        return self.detail or _detail()

    async def get_game_page_code(self, game_id: int) -> bytes:
        self.calls.append(("qrcode", game_id))
        return _TINY_PNG

    async def get_report_asset(self, name: str) -> bytes:
        self.calls.append((name, 4245))
        return _TINY_PNG


class ReportServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _REPORT_ASSET_CACHE.clear()

    @patch("thufootball.reports._render_html_to_png")
    async def test_validation_errors_stop_before_any_report_resources(
        self,
        renderer: object,
    ) -> None:
        detail = _detail()
        duplicate_start = replace(detail.events[0], event_id=999)
        client = _FakeClient(
            replace(detail, events=(*detail.events, duplicate_start))
        )
        service = THUFootballReportService(client)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReportValidationError) as caught:
                await service.download_game_report(4245, Path(directory))

        self.assertIn(
            "duplicate_start",
            [issue.code for issue in caught.exception.issues],
        )
        self.assertEqual(client.calls, [("detail", 4245)])
        renderer.assert_not_called()  # type: ignore[attr-defined]

    @patch(
        "thufootball.reports._render_html_to_png",
        return_value=_REPORT_PNG,
    )
    async def test_validation_warnings_are_returned_and_do_not_block(
        self,
        _renderer: object,
    ) -> None:
        detail = _detail()
        invalid_event = replace(
            detail.events[-1],
            event_id=999,
            valid=False,
        )
        client = _FakeClient(
            replace(detail, events=(*detail.events, invalid_event))
        )
        service = THUFootballReportService(client)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            result = await service.download_game_report(4245, Path(directory))

        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["invalid_event_ignored"],
        )
        self.assertIn(("qrcode", 4245), client.calls)


    @patch(
        "thufootball.reports._render_html_to_png",
        return_value=_REPORT_PNG,
    )
    async def test_downloads_png_without_refreshing_stats_by_default(
        self,
        _renderer: object,
    ) -> None:
        client = _FakeClient()
        service = THUFootballReportService(client)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "game-4245.png"
            result = await service.download_game_report(4245, output)

            self.assertEqual(
                client.calls,
                [
                    ("detail", 4245),
                    ("qrcode", 4245),
                    *((name, 4245) for name in _REPORT_ASSET_NAMES),
                ],
            )
            self.assertEqual(result.path, str(output.resolve()))
            self.assertEqual(result.media_type, "image/png")
            self.assertFalse(result.refreshed_stats)
            self.assertEqual(result.warnings, ())
            self.assertEqual((result.width, result.height), (1600, 1646))
            with Image.open(output) as rendered:
                self.assertEqual(rendered.format, "PNG")
                self.assertEqual(rendered.size, (result.width, result.height))

            with self.assertRaises(ConfigurationError):
                await service.download_game_report(4245, output)

    @patch(
        "thufootball.reports._render_html_to_png",
        return_value=_REPORT_PNG,
    )
    async def test_refreshes_stats_only_with_explicit_opt_in(
        self,
        _renderer: object,
    ) -> None:
        client = _FakeClient()
        service = THUFootballReportService(client)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            result = await service.download_game_report(
                4245,
                Path(directory),
                refresh_stats=True,
            )

        self.assertEqual(
            client.calls,
            [
                ("refresh", 4245),
                ("detail", 4245),
                ("qrcode", 4245),
                *((name, 4245) for name in _REPORT_ASSET_NAMES),
            ],
        )
        self.assertTrue(result.refreshed_stats)

    @patch(
        "thufootball.reports._render_html_to_png",
        return_value=_REPORT_PNG,
    )
    async def test_supports_read_only_report_without_qr_code(
        self,
        _renderer: object,
    ) -> None:
        client = _FakeClient()
        service = THUFootballReportService(client)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            result = await service.download_game_report(
                4245,
                Path(directory),
                settings=ReportSettings(include_qr_code=False),
                refresh_stats=False,
            )

            self.assertEqual(
                client.calls,
                [
                    ("detail", 4245),
                    *((name, 4245) for name in _REPORT_ASSET_NAMES),
                ],
            )
            self.assertTrue(Path(result.path).is_file())
        self.assertFalse(result.refreshed_stats)
        self.assertTrue(Path(result.path).name.startswith("game_4245_"))

    @patch(
        "thufootball.reports._render_html_to_png",
        return_value=_REPORT_PNG,
    )
    async def test_reuses_static_assets_across_service_instances(
        self,
        _renderer: object,
    ) -> None:
        client = _FakeClient()

        await THUFootballReportService(client).render_game_detail(  # type: ignore[arg-type]
            _detail()
        )
        await THUFootballReportService(client).render_game_detail(  # type: ignore[arg-type]
            _detail()
        )

        self.assertEqual(client.calls.count(("qrcode", 4245)), 2)
        for name in _REPORT_ASSET_NAMES:
            self.assertEqual(client.calls.count((name, 4245)), 1)


class AbandonReportRuleTests(unittest.TestCase):
    def test_single_abandon_without_events_skips_validation(self) -> None:
        detail = replace(
            _detail(),
            game=replace(_summary(), home_abandon=True),
            events=(),
        )

        prepared = prepare_game_report(detail)

        self.assertFalse(prepared.render_image)
        self.assertEqual(prepared.warnings, ())
        self.assertEqual(
            prepared.text,
            "车辆与运载学院vs未央书院的比赛，由于车辆与运载学院弃赛，"
            "记为车辆与运载学院 0:3 未央书院。",
        )

    def test_single_abandon_with_events_uses_larger_awarded_loss(self) -> None:
        cases = (((1, 4), (1, 4)), ((0, 2), (0, 3)), ((4, 1), (0, 3)))
        for current, expected in cases:
            with self.subTest(current=current):
                detail = _detail()
                detail = replace(
                    detail,
                    game=replace(
                        detail.game,
                        home_score=current[0],
                        away_score=current[1],
                        home_abandon=True,
                    ),
                )

                prepared = prepare_game_report(detail)

                self.assertTrue(prepared.render_image)
                self.assertEqual(
                    (prepared.detail.game.home_score, prepared.detail.game.away_score),
                    expected,
                )
                self.assertFalse(prepared.detail.game.home_abandon)
                self.assertEqual(
                    prepared.warnings[0].code,
                    "abandon_with_events_awarded_loss",
                )
                self.assertIn(f"{expected[0]}:{expected[1]}", prepared.text or "")

    def test_both_sides_abandoned_skip_events_and_return_text(self) -> None:
        detail = _detail()
        detail = replace(
            detail,
            game=replace(detail.game, home_abandon=True, away_abandon=True),
        )

        prepared = prepare_game_report(detail)

        self.assertFalse(prepared.render_image)
        self.assertEqual(prepared.warnings[0].message, "双方弃赛")
        self.assertEqual(
            prepared.text,
            "车辆与运载学院vs未央书院的比赛，双方弃赛",
        )


class ReportRendererTests(unittest.TestCase):
    def test_zero_pads_day_in_report_date(self) -> None:
        assets = {name: _TINY_PNG for name in _REPORT_ASSET_NAMES}
        detail = _detail()
        detail = replace(
            detail,
            game=replace(
                detail.game,
                kickoff_local=datetime(2026, 4, 4, 15, 0, tzinfo=UTC),
            ),
        )

        payload = _report_payload(
            detail,
            settings=ReportSettings(),
            assets=assets,
            qr_code=_TINY_PNG,
        )

        self.assertTrue(payload["metadata"].startswith("2026-04-04 15:00"))

    def test_prefers_static_full_team_names_and_keeps_api_fallbacks(
        self,
    ) -> None:
        assets = {name: _TINY_PNG for name in _REPORT_ASSET_NAMES}
        detail = _detail()

        payload = _report_payload(
            detail,
            settings=ReportSettings(),
            assets=assets,
            qr_code=_TINY_PNG,
        )
        self.assertEqual(payload["home_name"], "车辆与运载学院")
        self.assertEqual(payload["away_name"], "未央书院")

        unknown_teams = replace(
            detail,
            game=replace(
                detail.game,
                home_team_id=999_998,
                away_team_id=999_999,
            ),
        )
        fallback_payload = _report_payload(
            unknown_teams,
            settings=ReportSettings(),
            assets=assets,
            qr_code=_TINY_PNG,
        )
        self.assertEqual(fallback_payload["home_name"], "汽车")
        self.assertEqual(fallback_payload["away_name"], "未央")

    def test_uses_website_fonts_and_original_event_assets(self) -> None:
        assets = {name: _TINY_PNG for name in _REPORT_ASSET_NAMES}
        payload = _report_payload(
            _detail(),
            settings=ReportSettings(),
            assets=assets,
            qr_code=_TINY_PNG,
        )

        goal = payload["groups"][3]["home"][0]
        second_yellow = payload["groups"][4]["home"][0]
        self.assertEqual(goal["asset"], "event_goal")
        self.assertEqual(second_yellow["type"], "SECONDYELLOWCARD")
        self.assertEqual(
            second_yellow["asset"],
            "event_second_yellow_card",
        )

        html = _build_report_html(payload)
        self.assertNotIn("<script src=", html)
        self.assertIn("jQuery v3.6.4", html)
        self.assertIn("jCanvas", html)
        self.assertIn('fontFamily: "simHei"', html)
        self.assertIn('fontStyle: "bold"', html)
        self.assertIn('fontFamily: "WenQuanYi Micro Hei"', html)
        self.assertIn("source: images[event.asset]", html)
        self.assertNotIn("legend_assist", html)
        self.assertNotIn("event_assist", html)
        self.assertNotIn("助攻", html)
        self.assertIn("imageWidth + 8 + textWidth", html)
        self.assertIn("20 * (measuredLegendItems.length - 1)", html)
        self.assertIn("let legendX = 800 - legendWidth / 2", html)
        self.assertIn("function wrapTitle(text, maxWidth)", html)
        self.assertIn('context.font = "bold 50px simHei"', html)
        self.assertIn("text: wrapTitle(text, 600)", html)


class ReportClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_discovered_refresh_and_qr_endpoints(self) -> None:
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/OnReStatGameData"):
                return httpx.Response(
                    200,
                    json={"success": True, "info": "ok"},
                    request=request,
                )
            return httpx.Response(
                200,
                content=_TINY_PNG,
                headers={"content-type": "image/png"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = THUFootballClient(
                openid="openid",
                session_key="session",
                http_client=http,
            )
            await client.refresh_game_stats(4245)
            qrcode = await client.get_game_page_code(4245)
            start = await client.get_report_asset("start")
            goal = await client.get_report_asset("event_goal")

        self.assertEqual(qrcode, _TINY_PNG)
        self.assertEqual(start, _TINY_PNG)
        self.assertEqual(goal, _TINY_PNG)
        self.assertTrue(seen[0].url.path.endswith("/OnReStatGameData"))
        self.assertEqual(seen[0].url.params["game_id"], "4245")
        self.assertEqual(seen[0].url.params["openid"], "openid")
        self.assertEqual(seen[0].url.params["session_key"], "session")
        self.assertTrue(seen[1].url.path.endswith("/GetGamePageCode"))
        self.assertEqual(seen[1].url.params["game_id"], "4245")
        self.assertNotIn("openid", seen[1].url.params)
        self.assertNotIn("session_key", seen[1].url.params)
        self.assertTrue(seen[2].url.path.endswith("/img_static/START.png"))
        self.assertNotIn("openid", seen[2].url.params)
        self.assertNotIn("session_key", seen[2].url.params)
        self.assertTrue(seen[3].url.path.endswith("/img_static/G.png"))
        self.assertNotIn("openid", seen[3].url.params)
        self.assertNotIn("session_key", seen[3].url.params)


if __name__ == "__main__":
    unittest.main()
