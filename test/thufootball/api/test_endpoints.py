from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_current_games import get_current_games
from thufootball.api.get_game_info import (
    _validation_summary as game_summary,
)
from thufootball.api.get_game_info import get_game_info
from thufootball.api.get_my_tournaments import (
    _validation_summary as tournaments_summary,
)
from thufootball.api.get_my_tournaments import get_my_tournaments
from thufootball.api.get_tourn_info import (
    _validation_summary as tournament_summary,
)
from thufootball.api.get_tourn_info import get_tourn_info
from thufootball.api.get_user_info import get_user_info


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _query(mock_urlopen) -> dict[str, list[str]]:
    request = mock_urlopen.call_args.args[0]
    mock_urlopen.assert_called_once_with(request, timeout=15.0)
    return parse_qs(urlsplit(request.full_url).query)


class AuthenticatedEndpointTests(unittest.TestCase):
    @patch("thufootball.api.utils.urlopen")
    def test_user_info_request_preserves_credentials(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "success": True,
                "info": "Successfully get!",
                "user_registered": True,
            }
        )

        result = get_user_info("openid-value", "a+b/c==")

        self.assertTrue(result["success"])
        self.assertEqual(
            _query(mock_urlopen),
            {"openid": ["openid-value"], "session_key": ["a+b/c=="]},
        )

    @patch("thufootball.api.utils.urlopen")
    def test_tournament_catalog_request_and_summary(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "success": True,
                "info": "ok",
                "tourns": [
                    {"id": 122, "visible": True},
                    {"id": 136, "visible": False},
                ],
            }
        )

        result = get_my_tournaments("openid-value", "a+b/c==")

        self.assertEqual(
            _query(mock_urlopen),
            {"openid": ["openid-value"], "session_key": ["a+b/c=="]},
        )
        summary = tournaments_summary(result)
        self.assertEqual(summary["tournament_count"], 2)
        self.assertEqual(summary["visible_count"], 1)
        self.assertEqual(summary["hidden_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_tournament_request_and_summary(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "success": True,
                "tourn_info": {"id": 122, "name": "test tournament"},
                "registered_teams": [{"id": 1733}, {"id": 1734}],
                "games": [{"id": 4245}],
            }
        )

        result = get_tourn_info("openid-value", "a+b/c==", 122)

        self.assertEqual(result["tourn_info"]["id"], 122)
        self.assertEqual(
            _query(mock_urlopen),
            {
                "openid": ["openid-value"],
                "session_key": ["a+b/c=="],
                "tourn_id": ["122"],
            },
        )
        summary = tournament_summary(result)
        self.assertEqual(summary["team_count"], 2)
        self.assertEqual(summary["game_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_game_request_and_summary(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "success": True,
                "game_info": {
                    "id": 4245,
                    "time": "2026-04-19 05:00:00",
                    "result": "1:0",
                    "home_tourn_team_info": {"brief_name": "汽车"},
                    "away_tourn_team_info": {"brief_name": "未央"},
                },
                "tourn_info": {"id": 122, "name": "test tournament"},
                "home_tourn_team_players": [{"id": 1}],
                "away_tourn_team_players": [{"id": 2}],
                "events": [{"id": 3}],
                "comments": [],
                "referees": [],
                "officials": [{"id": 5}],
                "durations": [{"id": 4}],
            }
        )

        result = get_game_info("openid-value", "a+b/c==", 4245)

        self.assertEqual(result["game_info"]["id"], 4245)
        self.assertEqual(
            _query(mock_urlopen),
            {
                "openid": ["openid-value"],
                "session_key": ["a+b/c=="],
                "game_id": ["4245"],
            },
        )
        summary = game_summary(result)
        self.assertEqual(summary["home_team"], "汽车")
        self.assertEqual(summary["away_team"], "未央")
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["official_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_all_authenticated_endpoints_reject_missing_credentials(
        self,
        mock_urlopen,
    ) -> None:
        calls = (
            ("user", lambda openid, session: get_user_info(openid, session)),
            (
                "catalog",
                lambda openid, session: get_my_tournaments(openid, session),
            ),
            (
                "tournament",
                lambda openid, session: get_tourn_info(openid, session, 122),
            ),
            (
                "game",
                lambda openid, session: get_game_info(openid, session, 4245),
            ),
        )
        for name, call in calls:
            with self.subTest(endpoint=name, field="openid"):
                with self.assertRaisesRegex(ValueError, "openid"):
                    call("", "session-key")
            with self.subTest(endpoint=name, field="session_key"):
                with self.assertRaisesRegex(ValueError, "session_key"):
                    call("openid-value", "")
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_resource_endpoints_reject_invalid_ids(self, mock_urlopen) -> None:
        calls = (
            ("game_id", lambda value: get_game_info("openid", "session", value)),
            (
                "tourn_id",
                lambda value: get_tourn_info("openid", "session", value),
            ),
        )
        for field, call in calls:
            for value in (0, -1, True, "1"):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        call(value)  # type: ignore[arg-type]
        mock_urlopen.assert_not_called()


class CurrentGamesEndpointTests(unittest.TestCase):
    @patch("thufootball.api.utils.urlopen")
    def test_sends_all_parameters_and_returns_response(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {"success": True, "info": "ok", "current_games": [{"id": 4367}]}
        )

        result = get_current_games(
            "openid-value",
            "a+b/c==",
            history_bound=date(2026, 6, 15),
            future_bound="2026-07-28",
            field_id=3,
            game_type="all",
        )

        self.assertEqual(result["current_games"][0]["id"], 4367)
        self.assertEqual(
            _query(mock_urlopen),
            {
                "openid": ["openid-value"],
                "session_key": ["a+b/c=="],
                "history_bound": ["2026-06-15"],
                "future_bound": ["2026-07-28"],
                "field_id": ["3"],
                "type": ["all"],
            },
        )

    @patch("thufootball.api.utils.urlopen")
    def test_allows_public_request_without_credentials(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response({"success": True, "current_games": []})

        get_current_games()

        self.assertEqual(_query(mock_urlopen), {"type": ["public"]})

    @patch("thufootball.api.utils.urlopen")
    def test_requires_credentials_as_a_pair(self, mock_urlopen) -> None:
        for openid, session_key in (("openid-value", None), (None, "session-key")):
            with self.subTest(openid=openid, session_key=session_key):
                with self.assertRaisesRegex(ValueError, "supplied together"):
                    get_current_games(openid, session_key)
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_invalid_dates_and_reversed_interval(self, mock_urlopen) -> None:
        invalid_values = ("2026-7-1", "2026-02-30", datetime(2026, 7, 1, 8, 0))
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "history_bound"):
                    get_current_games(history_bound=value)
        with self.assertRaisesRegex(ValueError, "must not be later"):
            get_current_games(
                history_bound="2026-07-29",
                future_bound="2026-07-28",
            )
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_invalid_field_and_game_type(self, mock_urlopen) -> None:
        for field_id in (0, -1, True, "3"):
            with self.subTest(field_id=field_id):
                with self.assertRaisesRegex(ValueError, "field_id"):
                    get_current_games(field_id=field_id)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "game_type"):
            get_current_games(game_type="private")
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
