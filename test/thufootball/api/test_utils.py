from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from thufootball.api.utils import (
    THUFootballRequestError,
    load_env_file,
    request_json,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class UtilsTests(unittest.TestCase):
    def test_loads_credentials_without_overwriting_environment(self) -> None:
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
                load_env_file(env_file)

                self.assertEqual(os.environ["THUFOOTBALL_OPENID"], "environment-openid")
                self.assertEqual(os.environ["THUFOOTBALL_SESSION_KEY"], "a+b/c==")
                self.assertNotIn("IGNORED", os.environ)

    @patch("thufootball.api.utils.urlopen")
    def test_request_json_maps_transport_errors(self, mock_urlopen) -> None:
        cases = (
            (
                HTTPError("https://example.invalid", 503, "unavailable", None, None),
                "HTTP 503",
            ),
            (URLError("offline"), "request failed"),
            (TimeoutError("timeout"), "request failed"),
            (OSError("offline"), "request failed"),
        )
        for error, message in cases:
            with self.subTest(error=type(error).__name__):
                mock_urlopen.reset_mock()
                mock_urlopen.side_effect = error
                with self.assertRaisesRegex(THUFootballRequestError, message):
                    request_json("Endpoint", {})

    @patch("thufootball.api.utils.urlopen")
    def test_request_json_requires_an_object_payload(self, mock_urlopen) -> None:
        cases = ((b"{broken", "invalid JSON"), (b"[]", "non-object"))
        for body, message in cases:
            with self.subTest(message=message):
                mock_urlopen.reset_mock()
                mock_urlopen.side_effect = None
                mock_urlopen.return_value = _Response(body)
                with self.assertRaisesRegex(THUFootballRequestError, message):
                    request_json("Endpoint", {})

    @patch("thufootball.api.utils.urlopen")
    def test_request_json_validates_local_options_before_io(self, mock_urlopen) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            request_json("Endpoint", {}, base_url="")
        with self.assertRaisesRegex(ValueError, "timeout"):
            request_json("Endpoint", {}, timeout=0)
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
