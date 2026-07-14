from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_my_tournaments import (
    THUFootballRequestError,
    _validation_summary,
    get_my_tournaments,
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


class GetMyTournamentsTests(unittest.TestCase):
    @patch("thufootball.api.utils.urlopen")
    def test_sends_credentials_and_returns_response(self, mock_urlopen) -> None:
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

        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["openid"], ["openid-value"])
        self.assertEqual(query["session_key"], ["a+b/c=="])
        mock_urlopen.assert_called_once_with(request, timeout=15.0)

        summary = _validation_summary(result)
        self.assertEqual(summary["tournament_count"], 2)
        self.assertEqual(summary["visible_count"], 1)
        self.assertEqual(summary["hidden_count"], 1)

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_missing_credentials_without_requesting(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "openid"):
            get_my_tournaments("", "session-key")
        with self.assertRaisesRegex(ValueError, "session_key"):
            get_my_tournaments("openid-value", "")
        mock_urlopen.assert_not_called()

    @patch("thufootball.api.utils.urlopen")
    def test_rejects_non_object_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(["unexpected"])

        with self.assertRaisesRegex(THUFootballRequestError, "non-object"):
            get_my_tournaments("openid-value", "session-key")


if __name__ == "__main__":
    unittest.main()
