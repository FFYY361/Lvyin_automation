from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thufootball.config import load_env_file


class ConfigTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
