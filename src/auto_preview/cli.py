"""Command-line entry point for automated preview runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from wechat_official import CoverFile, CoverMediaId

from .diagnostics import failure_lines, log_failure
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


def _relative_path(path: Path, project_root: Path) -> Path:
    return Path(os.path.relpath(path.resolve(), project_root.resolve()))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    run_directory = (
        project_root
        / "runs"
        / "auto_preview"
        / f"{args.preview_date.isoformat()}_{args.competition.value}"
    )
    log_path = run_directory / "auto_preview.log"
    display_log_path = _relative_path(log_path, project_root)
    try:
        logger = configure_logging(run_directory, project_root=project_root)
    except Exception as exc:
        print(
            *failure_lines(exc, stage="logging", log_path=display_log_path),
            sep="\n",
            file=sys.stderr,
        )
        return 2

    try:
        if args.cover is not None:
            cover_path = Path(args.cover).expanduser().resolve()
            if not cover_path.is_file():
                log_failure(
                    logger,
                    FileNotFoundError(
                        2,
                        "封面文件不存在",
                        str(_relative_path(cover_path, project_root)),
                    ),
                    stage="arguments",
                    log_path=display_log_path,
                )
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
        result = asyncio.run(runner.run(request))
    except KeyboardInterrupt:
        logger.error("✗ auto_preview 已由用户中断")
        return 130
    except Exception as exc:
        log_failure(logger, exc, log_path=display_log_path)
        return 2

    print(
        json.dumps(
            {
                "status": result.status,
                "completed_stage": result.completed_stage.value,
                "run_directory": str(
                    _relative_path(result.run_directory, project_root)
                ),
                "source": str(_relative_path(result.source_path, project_root)),
                "article": (
                    str(_relative_path(result.article_directory, project_root))
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
