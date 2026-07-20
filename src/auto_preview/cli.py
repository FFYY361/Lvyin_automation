"""Command-line entry point for automated preview runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

from preview import PreviewError
from thufootball import THUFootballError
from wechat_official import CoverFile, CoverMediaId, WechatArticleError

from .errors import PipelineError
from .logging_utils import configure_logging
from .models import Competition, PipelineRequest, Stage
from .service import AutoPreviewPipeline


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    if isinstance(parsed, datetime) or parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_preview.py",
        description="Read THUFootball data, render a preview, and create a WeChat draft",
    )
    parser.add_argument("preview_date", type=_date, metavar="YYYY-MM-DD")
    parser.add_argument(
        "competition",
        type=Competition,
        choices=tuple(Competition),
    )
    parser.add_argument(
        "--stage",
        type=Stage,
        choices=tuple(Stage),
        default=Stage.ARTICLE,
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="rebuild every stage from data through the requested target",
    )
    cover = parser.add_mutually_exclusive_group()
    cover.add_argument("--cover", help="local JPEG, PNG, or GIF cover")
    cover.add_argument(
        "--cover-media-id", help="existing permanent WeChat cover media ID"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    run_directory = (
        project_root
        / "runs"
        / "auto_preview"
        / f"{args.preview_date.isoformat()}_{args.competition.value}"
    )
    logger = configure_logging(run_directory)
    if args.cover is not None:
        cover_path = Path(args.cover).expanduser().resolve()
        if not cover_path.is_file():
            logger.error("✗ 封面文件不存在：%s", cover_path)
            return 2
        cover = CoverFile(cover_path)
    elif args.cover_media_id is not None:
        cover = CoverMediaId(args.cover_media_id)
    else:
        cover = None
    request = PipelineRequest(
        preview_date=args.preview_date,
        competition=args.competition,
        stage=args.stage,
        cover=cover,
        override=args.override,
    )
    runner = AutoPreviewPipeline(project_root=project_root, logger=logger)
    try:
        result = asyncio.run(runner.run(request))
    except (PipelineError, THUFootballError, PreviewError, WechatArticleError) as exc:
        logger.error(
            "✗ auto_preview 失败：stage=%s error=%s message=%s",
            getattr(exc, "stage", "unknown"),
            type(exc).__name__,
            str(exc),
        )
        return 2
    except (OSError, ValueError) as exc:
        logger.error(
            "✗ auto_preview 失败：error=%s message=%s", type(exc).__name__, str(exc)
        )
        return 2
    except KeyboardInterrupt:
        logger.error("✗ auto_preview 已由用户中断")
        return 130

    print(
        json.dumps(
            {
                "status": result.status,
                "completed_stage": result.completed_stage.value,
                "run_directory": str(result.run_directory),
                "source": str(result.source_path),
                "article": (
                    str(result.article_directory)
                    if result.article_directory is not None
                    else None
                ),
                "draft_media_id": result.draft_media_id,
                "next_command": result.next_command,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
