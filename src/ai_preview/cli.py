"""Command-line interface for AI preview prompt assembly."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import DEFAULT_DATA_ROOT, DEFAULT_TEAMS_PATH, build_user_message
from .config import DEFAULT_CONFIG_PATH, load_prompt_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="根据 MATCH_ID 组装 AI 比赛前瞻的 User message"
    )
    parser.add_argument("match_id", type=int, help="目标比赛 MATCH_ID")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Prompt 组装配置 JSON",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="AI 前瞻本地资料根目录",
    )
    parser.add_argument(
        "--teams-path",
        type=Path,
        default=DEFAULT_TEAMS_PATH,
        help="teams.json 路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_prompt_config(args.config)
        message = build_user_message(
            args.match_id,
            config=config,
            data_root=args.data_root,
            teams_path=args.teams_path,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0
