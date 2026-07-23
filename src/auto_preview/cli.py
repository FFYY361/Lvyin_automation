"""Command-line entry point for automated preview runs."""

from __future__ import annotations

import argparse
import asyncio
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
    parser.add_argument(
        "--dates",
        nargs="+",
        required=True,
        type=_date,
        metavar="YYYY-MM-DD",
    )
    parser.add_argument(
        "--competitions",
        nargs="+",
        required=True,
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
    first_date = min(args.dates)
    competition_order = {
        Competition.MALE: 0,
        Competition.FEMALE: 1,
        Competition.FUTSAL: 2,
    }
    first_competition = min(args.competitions, key=competition_order.__getitem__)
    run_directory = (
        project_root
        / "runs"
        / "auto_preview"
        / f"{first_date.isoformat()}_{first_competition.value}"
    )
    try:
        logger = configure_logging(run_directory, project_root=project_root)
    except Exception as exc:
        print(
            *failure_lines(exc, stage="logging"),
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
                )
                return 2
            cover = CoverFile(cover_path)
        elif args.cover_media_id is not None:
            cover = CoverMediaId(args.cover_media_id)
        else:
            cover = None
        request = PipelineRequest(
            preview_dates=args.dates,
            competitions=args.competitions,
            stage=args.stage,
            cover=cover,
            override=args.override,
        )
        runner = AutoPreviewPipeline(project_root=project_root)
        result = asyncio.run(runner.run(request))
    except KeyboardInterrupt:
        logger.error("✗ auto_preview 已由用户中断")
        return 130
    except Exception as exc:
        log_failure(logger, exc)
        return 2

    if result.next_command is not None:
        next_stage = (
            Stage.ARTICLE if result.completed_stage is Stage.DATA else Stage.PUBLISH
        )
        logger.info("下一步 %s 命令：%s", next_stage.value, result.next_command)
    return 0
