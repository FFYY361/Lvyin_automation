"""Local administration commands for the website."""

from __future__ import annotations

import argparse
import getpass

from .auth import create_admin
from .config import WebsiteSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the lvyin website backend")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-admin", help="create the first administrator")
    init.add_argument("--username", required=True)
    init.add_argument("--display-name", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
        print(f"created administrator id={user_id}")
        return 0
    raise AssertionError("unreachable")
