from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_tourn_info import (
    THUFootballRequestError,
    _validation_summary,
    get_tourn_info,
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


class GetTournInfoTests(unittest.TestCase):
    @patch("thufootball.api.utils.urlopen")
    def test_sends_parameters_and_returns_response(self, mock_urlopen) -> None:
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
        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["openid"], ["openid-value"])
        self.assertEqual(query["session_key"], ["a+b/c=="])
        self.assertEqual(query["tourn_id"], ["122"])
        mock_urlopen.assert_called_once_with(request, timeout=15.0)

        summary = _validation_summary(result)
        self.assertEqual(summary["team_count"], 2)
        self.assertEqual(summary["game_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_missing_credentials_without_requesting(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "openid"):
            get_tourn_info("", "session-key", 122)
        with self.assertRaisesRegex(ValueError, "session_key"):
            get_tourn_info("openid-value", "", 122)
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_invalid_tournament_id_without_requesting(
        self, mock_urlopen
    ) -> None:
        for tourn_id in (0, -1, True, "122"):
            with self.subTest(tourn_id=tourn_id):
                with self.assertRaisesRegex(ValueError, "tourn_id"):
                    get_tourn_info(  # type: ignore[arg-type]
                        "openid-value", "session-key", tourn_id
                    )
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_non_object_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(["unexpected"])

        with self.assertRaisesRegex(THUFootballRequestError, "non-object"):
            get_tourn_info("openid-value", "session-key", 122)


if __name__ == "__main__":
    unittest.main()
