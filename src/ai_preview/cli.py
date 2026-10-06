"""Command-line interface for AI preview prompt assembly."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from thufootball.errors import THUFootballError

from .config import DEFAULT_CONFIG_PATH, load_prompt_config
from .source import build_live_prompt_bundle


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_prompt_config(args.config)
        bundle = asyncio.run(build_live_prompt_bundle(args.match_id, config=config))
        message = bundle.render_user_message()
    except (ValueError, THUFootballError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0
