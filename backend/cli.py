"""Local administration commands for the website."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from .auth import create_admin
from .config import WebsiteSettings
from .database import create_database_engine, create_session_factory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the lvyin website backend")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init-admin", help="create the first super administrator"
    )
    init.add_argument("--username", required=True)
    init.add_argument("--display-name", required=True)
    weather = commands.add_parser(
        "refresh-weather", help="refresh upcoming batch weather and preview articles"
    )
    weather.add_argument(
        "--dry-run",
        action="store_true",
        help="query and preview changes without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "refresh-weather":
        from .workflow import ExternalFactories, refresh_weather

        engine = None
        try:
            settings = WebsiteSettings.from_environment()
            engine = create_database_engine(settings.database_url)
            result = asyncio.run(
                refresh_weather(
                    create_session_factory(engine),
                    settings,
                    ExternalFactories(),
                    dry_run=args.dry_run,
                )
            )
        except Exception as exc:
            print(
                json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
                file=sys.stderr,
            )
            return 1
        finally:
            if engine is not None:
                engine.dispose()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result["summary"]["failed"] > 0)
    if args.command == "init-admin":
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("passwords do not match")
        if len(password) < 8:
            raise SystemExit("password must contain at least 8 characters")
        settings = WebsiteSettings.from_environment()
        user_id = create_admin(
            settings.database_url,
            username=args.username,
            display_name=args.display_name,
            password=password,
        )
        print(f"created super administrator id={user_id}")
        return 0
    raise AssertionError("unreachable")
