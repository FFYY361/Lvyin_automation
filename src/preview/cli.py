"""Command-line entry point for pure-local preview rendering."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wechat_official import CoverFile, CoverMediaId, WechatArticleError

from .bundle import load_preview_bundle
from .errors import PreviewError
from .service import PreviewService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地合成三份前瞻 JSON 并渲染文章目录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="从模板和三份 JSON 生成文章目录")
    render.add_argument("template", help="HTML 模板路径")
    render.add_argument("--source", required=True, help="前瞻 source.json 路径")
    render.add_argument("--weather", required=True, help="全局 weather.json 路径")
    render.add_argument("--config", required=True, help="全局 config.json 路径")
    render.add_argument("--output", required=True, help="文章输出目录")
    cover = render.add_mutually_exclusive_group(required=True)
    cover.add_argument("--cover", help="本地封面图片路径")
    cover.add_argument("--cover-media-id", help="已有公众号永久封面素材 ID")
    render.add_argument("--author", default="清华绿茵")
    render.add_argument("--digest", default="")
    render.add_argument("--source-url", default="")
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command != "render":
        raise AssertionError("unreachable command")
    source = load_preview_bundle(args.source, args.weather, args.config)
    service = PreviewService.from_template(args.template)
    cover = (
        CoverFile(Path(args.cover))
        if args.cover is not None
        else CoverMediaId(args.cover_media_id)
    )
    article = service.render(
        source,
        cover=cover,
        author=args.author,
        digest=args.digest,
        source_url=args.source_url,
    )
    output = article.save(args.output)
    return {
        "status": "ok",
        "title": article.title,
        "template_version": service.template_version,
        "template_fingerprint": service.template_fingerprint,
        "content_fingerprint": article.content_fingerprint,
        "output": str(output.resolve()),
        "external_writes": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except (PreviewError, WechatArticleError, OSError, ValueError) as exc:
        payload: dict[str, object] = {
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc),
        }
        stage = getattr(exc, "stage", None)
        if stage is not None:
            payload["stage"] = stage
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
