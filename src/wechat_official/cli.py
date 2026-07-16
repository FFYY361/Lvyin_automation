"""Command-line diagnostics and draft creation for WeChat Official Accounts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .client import WechatOfficialClient
from .errors import DraftValidationError, WechatArticleError
from .models import Article, CoverFile, CoverMediaId
from .network import public_ip_cross_check
from .service import WechatOfficialService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="微信公众号只读探针与草稿创建工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("auth-probe", help="只读验证凭据、IP 白名单和草稿权限")
    probe.add_argument("--api-base-url", default="https://api.weixin.qq.com")

    network = subparsers.add_parser(
        "network-check",
        help="确认微信实际看到的出口 IP，不进行外部写入",
    )
    network.add_argument("--api-base-url", default="https://api.weixin.qq.com")
    network.add_argument(
        "--cross-check",
        action="store_true",
        help="显式允许额外访问公网 IP 服务进行交叉核对",
    )

    inspect_draft = subparsers.add_parser(
        "inspect-draft",
        help="按 media_id 只读核验草稿元数据，不输出正文",
    )
    inspect_draft.add_argument("media_id")
    inspect_draft.add_argument("--api-base-url", default="https://api.weixin.qq.com")

    draft = subparsers.add_parser("create-draft", help="从完整文章目录创建公众号草稿")
    draft.add_argument("article", help="包含 article.json 和 body.html 的文章目录")
    draft.add_argument("--open-comments", action="store_true")
    draft.add_argument("--fans-only-comments", action="store_true")
    draft.add_argument(
        "--execute",
        action="store_true",
        help="明确允许上传图片和创建草稿；省略时只完成本地校验",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
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
            "external_writes": False,
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
                "confidence": "wechat-reported" if exc.observed_ip else "not-determined",
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
        article = Article.load(args.article)
        if args.fans_only_comments and not args.open_comments:
            raise DraftValidationError(
                "fans_only_comments requires open_comments",
                stage="draft-validation",
            )
        cover_kind = "file" if isinstance(article.cover, CoverFile) else "media_id"
        if not args.execute:
            return {
                "status": "dry-run",
                "title": article.title,
                "content_fingerprint": article.content_fingerprint,
                "cover": cover_kind,
                "external_writes": False,
                "message": "添加 --execute 后才会上传素材并创建公众号草稿",
            }
        async with WechatOfficialService.from_environment() as service:
            receipt = await service.create_draft(
                article,
                open_comments=args.open_comments,
                fans_only_comments=args.fans_only_comments,
            )
        return {
            "status": "ok",
            "draft_media_id": receipt.media_id,
            "content_fingerprint": receipt.content_fingerprint,
            "created_at": receipt.created_at.isoformat(),
            "external_writes": True,
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
