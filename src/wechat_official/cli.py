"""Command-line probes for capabilities 4, 5 and 6."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .article_source import PublishedArticleReader, extract_article, save_article_source
from .client import WechatOfficialClient
from .errors import WechatArticleError
from .media import MediaPublisher
from .network import public_ip_cross_check
from .service import DraftService
from .preview import load_preview_source
from .template import load_preview_template, save_rendered_article


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="清华绿茵公众号文章提取、模板渲染与草稿写入工具"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="从公开微信文章 URL 提取正文")
    extract.add_argument("url")
    extract.add_argument("--output-dir", required=True)

    extract_file = subparsers.add_parser(
        "extract-file", help="从本地完整页面样本提取正文（离线验证）"
    )
    extract_file.add_argument("path")
    extract_file.add_argument("--source-url", required=True)
    extract_file.add_argument("--output-dir", required=True)

    render = subparsers.add_parser("render", help="从前瞻源数据渲染 HTML 模板")
    render.add_argument("template")
    render.add_argument("--source", required=True)
    render.add_argument("--output", required=True)
    render.add_argument("--version")

    probe = subparsers.add_parser("auth-probe", help="只读验证 AppID/AppSecret 与 IP 白名单")
    probe.add_argument("--api-base-url", default="https://api.weixin.qq.com")

    network = subparsers.add_parser(
        "network-check", help="确认微信实际看到的出口 IP，不进行外部写入"
    )
    network.add_argument("--api-base-url", default="https://api.weixin.qq.com")
    network.add_argument(
        "--cross-check",
        action="store_true",
        help="显式允许额外访问 Cloudflare、AWS 和 ipify 交叉核对公网 IP",
    )

    inspect_draft = subparsers.add_parser(
        "inspect-draft", help="按 media_id 只读核验草稿元数据，不输出正文"
    )
    inspect_draft.add_argument("media_id")
    inspect_draft.add_argument("--api-base-url", default="https://api.weixin.qq.com")

    draft = subparsers.add_parser("create-draft", help="渲染并创建一个公众号草稿")
    draft.add_argument("template")
    draft.add_argument("--source", required=True)
    cover = draft.add_mutually_exclusive_group(required=True)
    cover.add_argument("--cover", help="上传本地图片作为新的永久封面素材")
    cover.add_argument(
        "--cover-media-id", help="复用公众号中已经存在的永久封面素材 ID"
    )
    draft.add_argument("--author", default="清华绿茵")
    draft.add_argument("--digest", default="")
    draft.add_argument("--source-url", default="")
    draft.add_argument("--version")
    draft.add_argument(
        "--execute",
        action="store_true",
        help="明确允许外部写入；省略时只完成本地渲染和校验",
    )
    return parser


def _preview_from_args(args: argparse.Namespace):
    template = load_preview_template(
        args.template,
        version=args.version,
    )
    source = load_preview_source(args.source)
    return template, source


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "extract":
        async with PublishedArticleReader() as reader:
            article = await reader.read(args.url)
        html_path, metadata_path = save_article_source(article, args.output_dir)
        return {
            "status": "ok",
            "title": article.title,
            "author": article.author,
            "media_count": len(article.media),
            "content_fingerprint": article.content_fingerprint,
            "preview": str(html_path.resolve()),
            "body": str((Path(args.output_dir) / "body.html").resolve()),
            "metadata": str(metadata_path.resolve()),
        }
    if args.command == "extract-file":
        raw_html = Path(args.path).read_text(encoding="utf-8")
        article = extract_article(raw_html, source_url=args.source_url)
        html_path, metadata_path = save_article_source(article, args.output_dir)
        return {
            "status": "ok",
            "title": article.title,
            "author": article.author,
            "media_count": len(article.media),
            "content_fingerprint": article.content_fingerprint,
            "preview": str(html_path.resolve()),
            "body": str((Path(args.output_dir) / "body.html").resolve()),
            "metadata": str(metadata_path.resolve()),
        }
    if args.command == "render":
        template, source = _preview_from_args(args)
        rendered = template.render(source)
        output = save_rendered_article(rendered, args.output)
        return {
            "status": "ok",
            "title": rendered.title,
            "template_version": rendered.template_version,
            "content_fingerprint": rendered.content_fingerprint,
            "media_count": len(rendered.media),
            "output": str(output.resolve()),
        }
    if args.command == "auth-probe":
        async with WechatOfficialClient.from_environment(base_url=args.api_base_url) as client:
            await client.get_access_token()
            draft_count = await client.count_drafts()
        return {
            "status": "ok",
            "credential": "accepted",
            "draft_permission": "accepted",
            "draft_count": draft_count,
            "token": "<redacted>",
        }
    if args.command == "network-check":
        cross_check = await public_ip_cross_check() if args.cross_check else None
        try:
            async with WechatOfficialClient.from_environment(
                base_url=args.api_base_url
            ) as client:
                await client.get_access_token()
                draft_count = await client.count_drafts()
        except WechatArticleError as exc:
            if exc.error_code != 40164:
                raise
            return {
                "status": "whitelist-required",
                "wechat": {
                    "reachable": True,
                    "error_code": exc.error_code,
                    "observed_source_ip": exc.observed_ip,
                },
                "cross_check": cross_check,
                "whitelist_candidate": exc.observed_ip,
                "confidence": (
                    "wechat-reported" if exc.observed_ip else "not-determined"
                ),
                "external_writes": False,
            }
        return {
            "status": "authorized",
            "wechat": {
                "reachable": True,
                "credential": "accepted",
                "ip_whitelist": "accepted",
                "draft_permission": "accepted",
                "draft_count": draft_count,
            },
            "cross_check": cross_check,
            "whitelist_candidate": None,
            "confidence": "wechat-authorized",
            "external_writes": False,
        }
    if args.command == "inspect-draft":
        async with WechatOfficialClient.from_environment(
            base_url=args.api_base_url
        ) as client:
            payload = await client.get_draft(args.media_id)
        raw_items = payload.get("news_item")
        items = raw_items if isinstance(raw_items, list) else []
        first = items[0] if items and isinstance(items[0], dict) else {}
        return {
            "status": "ok",
            "draft_media_id": args.media_id,
            "article_count": len(items),
            "title": first.get("title"),
            "author": first.get("author"),
            "thumb_media_id": first.get("thumb_media_id"),
            "create_time": payload.get("create_time"),
            "update_time": payload.get("update_time"),
            "content_included": False,
            "external_writes": False,
        }
    if args.command == "create-draft":
        template, source = _preview_from_args(args)
        rendered = template.render(source)
        local_output = save_rendered_article(rendered, "tmp/wechat_draft_preview.html")
        if not args.execute:
            return {
                "status": "dry-run",
                "title": rendered.title,
                "content_fingerprint": rendered.content_fingerprint,
                "preview": str(local_output.resolve()),
                "message": "添加 --execute 后才会写入公众号草稿箱",
            }
        async with WechatOfficialClient.from_environment() as client:
            async with MediaPublisher(client) as media:
                service = DraftService(client, media)
                receipt = await service.create_draft(
                    rendered,
                    cover_path=args.cover,
                    cover_media_id=args.cover_media_id,
                    author=args.author,
                    digest=args.digest,
                    source_url=args.source_url,
                )
        return {
            "status": "ok",
            "draft_media_id": receipt.media_id,
            "content_fingerprint": receipt.content_fingerprint,
            "created_at": receipt.created_at.isoformat(),
        }
    raise AssertionError("unreachable command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except WechatArticleError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": type(exc).__name__,
                    "stage": exc.stage,
                    "retryable": exc.retryable,
                    "error_code": exc.error_code,
                    "observed_ip": exc.observed_ip,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
