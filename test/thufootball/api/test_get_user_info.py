from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from thufootball.api.get_user_info import (
    THUFootballRequestError,
    _load_env_file,
    get_user_info,
)


class _Headers:
    @staticmethod
    def get_content_charset() -> str:
        return "utf-8"


class _Response:
    headers = _Headers()

    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class GetUserInfoTests(unittest.TestCase):
    def test_loads_credentials_from_env_file_without_overwriting_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "THUFOOTBALL_OPENID=file-openid\n"
                "THUFOOTBALL_SESSION_KEY='a+b/c=='\n"
                "IGNORED=value\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"THUFOOTBALL_OPENID": "environment-openid"},
                clear=True,
            ):
                _load_env_file(env_file)

                self.assertEqual(
                    os.environ["THUFOOTBALL_OPENID"], "environment-openid"
                )
                self.assertEqual(
                    os.environ["THUFOOTBALL_SESSION_KEY"], "a+b/c=="
                )
                self.assertNotIn("IGNORED", os.environ)

    @patch("thufootball.api.get_user_info.urlopen")
    def test_returns_response_and_preserves_special_characters(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "success": True,
                "info": "Successfully get!",
                "user_registered": True,
            }
        )

        result = get_user_info("openid-value", "a+b/c==")

        self.assertTrue(result["success"])
        request = mock_urlopen.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["openid"], ["openid-value"])
        self.assertEqual(query["session_key"], ["a+b/c=="])
        mock_urlopen.assert_called_once_with(request, timeout=15.0)

    @patch("thufootball.api.get_user_info.urlopen")
    def test_rejects_missing_credentials_without_requesting(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "openid"):
            get_user_info("", "session-key")

        with self.assertRaisesRegex(ValueError, "session_key"):
            get_user_info("openid-value", "")

        mock_urlopen.assert_not_called()

    @patch("thufootball.api.get_user_info.urlopen")
    def test_rejects_non_object_json(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(["unexpected"])

        with self.assertRaisesRegex(THUFootballRequestError, "non-object"):
            get_user_info("openid-value", "session-key")


if __name__ == "__main__":
    unittest.main()
