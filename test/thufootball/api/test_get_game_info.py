from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_game_info import (
    THUFootballRequestError,
    _validation_summary,
    get_game_info,
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


class GetGameInfoTests(unittest.TestCase):
    @patch("thufootball.api.utils.urlopen")
    def test_sends_parameters_and_returns_response(self, mock_urlopen) -> None:
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
        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["openid"], ["openid-value"])
        self.assertEqual(query["session_key"], ["a+b/c=="])
        self.assertEqual(query["game_id"], ["4245"])
        mock_urlopen.assert_called_once_with(request, timeout=15.0)

        summary = _validation_summary(result)
        self.assertEqual(summary["home_team"], "汽车")
        self.assertEqual(summary["away_team"], "未央")
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["official_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_missing_credentials_without_requesting(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "openid"):
            get_game_info("", "session-key", 4245)
        with self.assertRaisesRegex(ValueError, "session_key"):
            get_game_info("openid-value", "", 4245)
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_invalid_game_id_without_requesting(self, mock_urlopen) -> None:
        for game_id in (0, -1, True, "4245"):
            with self.subTest(game_id=game_id):
                with self.assertRaisesRegex(ValueError, "game_id"):
                    get_game_info(  # type: ignore[arg-type]
                        "openid-value", "session-key", game_id
                    )
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_non_object_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(["unexpected"])

        with self.assertRaisesRegex(THUFootballRequestError, "non-object"):
            get_game_info("openid-value", "session-key", 4245)


if __name__ == "__main__":
    unittest.main()
