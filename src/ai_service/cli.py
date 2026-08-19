"""Command-line access to the configured AI dialogue service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_FILE
from .errors import AIServiceError
from .models import ChatMessage
from .service import AIChatService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-chat",
        description="Send one request to a configured OpenAI-compatible model",
    )
    parser.add_argument("message", help="user message")
    parser.add_argument("--system", help="optional system message")
    parser.add_argument("--profile", help="model profile; defaults to config value")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    return parser


async def _run(args: argparse.Namespace) -> str:
    messages = []
    if args.system:
        messages.append(ChatMessage("system", args.system))
    messages.append(ChatMessage("user", args.message))
    async with AIChatService.from_config(
        args.profile,
        config_path=args.config,
        env_path=args.env_file,
    ) as service:
        result = await service.chat(messages)
    return result.content


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        content = asyncio.run(_run(args))
    except AIServiceError as exc:
        payload: dict[str, object] = {
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc),
            "stage": exc.stage,
            "retryable": exc.retryable,
        }
        if exc.status_code is not None:
            payload["status_code"] = exc.status_code
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(content)
    return 0
