"""Command-line entry point for automated match-report runs."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, datetime
from pathlib import Path

from wechat_official import CoverFile, CoverMediaId

from .logging_utils import configure_logging
from .models import Competition, PipelineRequest, Stage
from .service import AutoReportPipeline


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
        prog="auto_report.py",
        description=(
            "Render THUFootball reports, build articles, and create a WeChat draft"
        ),
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
        help=(
            "requery and rebuild every report stage through the requested target; "
            "server-side statistics are never refreshed"
        ),
    )
    cover = parser.add_mutually_exclusive_group()
    cover.add_argument("--cover", help="local JPEG, PNG, or GIF cover")
    cover.add_argument(
        "--cover-media-id",
        help="existing permanent WeChat cover media ID",
    )
    return parser


def _relative_path(path: Path, project_root: Path) -> Path:
    return Path(os.path.relpath(path.resolve(), project_root.resolve()))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    first_date = min(args.dates)
    order = {
        Competition.MALE: 0,
        Competition.FEMALE: 1,
        Competition.FUTSAL: 2,
    }
    first_competition = min(args.competitions, key=order.__getitem__)
    run_directory = (
        project_root
        / "runs"
        / "auto_report"
        / f"{first_date.isoformat()}_{first_competition.value}"
    )
    logger = configure_logging(run_directory, project_root=project_root)

    try:
        if args.cover is not None:
            cover_path = Path(args.cover).expanduser().resolve()
            if not cover_path.is_file():
                logger.error(
                    "✗ [arguments] 封面文件不存在：%s",
                    _relative_path(cover_path, project_root),
                )
                return 2
            selected_cover = CoverFile(cover_path)
        elif args.cover_media_id is not None:
            selected_cover = CoverMediaId(args.cover_media_id)
        else:
            selected_cover = None
        request = PipelineRequest(
            report_dates=args.dates,
            competitions=args.competitions,
            stage=args.stage,
            cover=selected_cover,
            override=args.override,
        )
        result = asyncio.run(
            AutoReportPipeline(project_root=project_root).run(request)
        )
    except KeyboardInterrupt:
        logger.error("✗ auto_report 已由用户中断")
        return 130
    except Exception as exc:
        stage = getattr(exc, "stage", "pipeline")
        logger.error("✗ [%s] %s: %s", stage, type(exc).__name__, exc)
        return 2

    if result.next_command is not None:
        logger.info("下一步命令：%s", result.next_command)
    return 0
