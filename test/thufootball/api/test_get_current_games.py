from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_current_games import (
    THUFootballRequestError,
    get_current_games,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class GetCurrentGamesTests(unittest.TestCase):
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
        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["openid"], ["openid-value"])
        self.assertEqual(query["session_key"], ["a+b/c=="])
        self.assertEqual(query["history_bound"], ["2026-06-15"])
        self.assertEqual(query["future_bound"], ["2026-07-28"])
        self.assertEqual(query["field_id"], ["3"])
        self.assertEqual(query["type"], ["all"])
        mock_urlopen.assert_called_once_with(request, timeout=15.0)

    @patch("thufootball.api.utils.urlopen")
    def test_allows_public_request_without_credentials(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {"success": True, "current_games": []}
        )

        get_current_games()

        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query, {"type": ["public"]})

    @patch("thufootball.api.utils.urlopen")
    def test_requires_credentials_as_a_pair(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "supplied together"):
            get_current_games("openid-value", None)
        with self.assertRaisesRegex(ValueError, "supplied together"):
            get_current_games(None, "session-key")
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
                history_bound="2026-07-29", future_bound="2026-07-28"
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

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_non_object_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(["unexpected"])

        with self.assertRaisesRegex(THUFootballRequestError, "non-object"):
            get_current_games()


if __name__ == "__main__":
    unittest.main()
