from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from wechat_official import (
    Article,
    CoverMediaId,
    DraftValidationError,
    DraftWriteError,
    MediaPublisher,
    WechatAuthenticationError,
    WechatOfficialClient,
    WechatPermissionError,
)


class TokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_probe_counts_drafts(self) -> None:
        paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            return httpx.Response(200, json={"total_count": 3}, request=request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret-test", http_client=http
        )
        try:
            self.assertEqual(await client.count_drafts(), 3)
        finally:
            await http.aclose()
        self.assertEqual(paths, ["/cgi-bin/stable_token", "/cgi-bin/draft/count"])

    async def test_reuses_unexpired_access_token(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            body = json.loads(request.content.decode("utf-8"))
            self.assertEqual(body["appid"], "wx-test")
            self.assertEqual(body["secret"], "secret-test")
            return httpx.Response(
                200,
                json={"access_token": "token-value", "expires_in": 7200},
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret-test", http_client=http
        )
        try:
            self.assertEqual(await client.get_access_token(), "token-value")
            self.assertEqual(await client.get_access_token(), "token-value")
        finally:
            await http.aclose()
        self.assertEqual(calls, 1)

    async def test_maps_bad_secret_to_authentication_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"errcode": 40125, "errmsg": "invalid appsecret"},
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="wrong", http_client=http
        )
        try:
            with self.assertRaises(WechatAuthenticationError) as caught:
                await client.get_access_token()
        finally:
            await http.aclose()
        self.assertEqual(caught.exception.error_code, 40125)
        self.assertNotIn("wrong", str(caught.exception))

    async def test_maps_ip_allowlist_error_to_permission_error(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "errcode": 40164,
                    "errmsg": "invalid ip 203.0.113.17 ipv6 ::ffff:203.0.113.17, not in whitelist",
                },
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            with self.assertRaises(WechatPermissionError) as caught:
                await client.upload_cover(filename="cover.png", content=b"png")
        finally:
            await http.aclose()
        self.assertEqual(calls, 2)
        self.assertEqual(caught.exception.observed_ip, "203.0.113.17")
        self.assertNotIn("not in whitelist", str(caught.exception))


class DraftClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_large_body_and_surfaces_wechat_error(self) -> None:
        calls: list[str] = []
        submitted_content = ""

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal submitted_content
            calls.append(request.url.path)
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            if request.url.path == "/cgi-bin/draft/add":
                payload = json.loads(request.content.decode("utf-8"))
                submitted_content = payload["articles"][0]["content"]
                return httpx.Response(
                    200,
                    json={"errcode": 45002, "errmsg": "content size out of limit"},
                    request=request,
                )
            raise AssertionError(request.url)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        body = "<section>" + ("x" * 22_000) + "</section>"
        try:
            with self.assertRaises(DraftWriteError) as caught:
                await client.add_draft(
                    Article(
                        title="超长元数据" * 7,
                        author="作者" * 9,
                        digest="摘要" * 61,
                        body_html=body,
                        cover=CoverMediaId("cover-media-id"),
                    ),
                    thumb_media_id="cover-media-id",
                )
        finally:
            await http.aclose()

        self.assertEqual(submitted_content, body)
        self.assertEqual(calls, ["/cgi-bin/stable_token", "/cgi-bin/draft/add"])
        self.assertEqual(caught.exception.error_code, 45002)
        self.assertIn("content size out of limit", str(caught.exception))

    async def test_uploads_media_and_adds_single_draft(self) -> None:
        calls: list[tuple[str, str]] = []
        draft_payload: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            if request.url.path == "/cgi-bin/media/uploadimg":
                return httpx.Response(
                    200,
                    json={"url": "https://mmbiz.qpic.cn/new/body.png"},
                    request=request,
                )
            if request.url.path == "/cgi-bin/material/add_material":
                return httpx.Response(
                    200,
                    json={"media_id": "cover-media-id"},
                    request=request,
                )
            if request.url.path == "/cgi-bin/draft/add":
                draft_payload.update(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    200, json={"media_id": "draft-media-id"}, request=request
                )
            raise AssertionError(request.url)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            body_url = await client.upload_content_image(
                filename="body.png", content=b"png"
            )
            cover_id = await client.upload_cover(filename="cover.jpg", content=b"jpg")
            article = Article(
                title="[TEST] 清华绿茵",
                body_html=f'<section><img src="{body_url}"></section>',
                cover=CoverMediaId(cover_id),
                author="清华绿茵",
            )
            receipt = await client.add_draft(
                article,
                thumb_media_id=cover_id,
                open_comments=True,
                fans_only_comments=True,
            )
        finally:
            await http.aclose()

        self.assertEqual(receipt.media_id, "draft-media-id")
        self.assertEqual(receipt.content_fingerprint, article.content_fingerprint)
        self.assertEqual(
            draft_payload["articles"][0]["thumb_media_id"], "cover-media-id"
        )
        self.assertEqual(draft_payload["articles"][0]["need_open_comment"], 1)
        self.assertEqual(draft_payload["articles"][0]["only_fans_can_comment"], 1)
        self.assertEqual(
            [path for _, path in calls],
            [
                "/cgi-bin/stable_token",
                "/cgi-bin/media/uploadimg",
                "/cgi-bin/material/add_material",
                "/cgi-bin/draft/add",
            ],
        )
        self.assertFalse(hasattr(client, "publish"))

    async def test_adds_ordered_multi_article_draft_in_one_request(self) -> None:
        calls: list[str] = []
        draft_payload: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            if request.url.path == "/cgi-bin/draft/add":
                draft_payload.update(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    200, json={"media_id": "multi-draft"}, request=request
                )
            raise AssertionError(request.url)

        articles = tuple(
            Article(
                title=title,
                body_html=f"<section>{title}</section>",
                cover=CoverMediaId(cover),
            )
            for title, cover in (("头条", "cover-1"), ("次条", "cover-2"))
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            receipt = await client.add_draft(
                articles,
                thumb_media_id=("cover-1", "cover-2"),
                open_comments=True,
                fans_only_comments=True,
            )
        finally:
            await http.aclose()

        items = draft_payload["articles"]
        assert isinstance(items, list)
        self.assertEqual([item["title"] for item in items], ["头条", "次条"])
        self.assertEqual(
            [item["thumb_media_id"] for item in items], ["cover-1", "cover-2"]
        )
        self.assertTrue(all(item["need_open_comment"] == 1 for item in items))
        self.assertTrue(all(item["only_fans_can_comment"] == 1 for item in items))
        self.assertEqual(calls, ["/cgi-bin/stable_token", "/cgi-bin/draft/add"])
        self.assertEqual(receipt.media_id, "multi-draft")
        self.assertNotEqual(
            receipt.content_fingerprint, articles[0].content_fingerprint
        )

    async def test_accepts_eight_articles_in_one_draft_request(self) -> None:
        article_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal article_count
            if request.url.path == "/cgi-bin/stable_token":
                return httpx.Response(
                    200,
                    json={"access_token": "token", "expires_in": 7200},
                    request=request,
                )
            if request.url.path == "/cgi-bin/draft/add":
                payload = json.loads(request.content.decode("utf-8"))
                article_count = len(payload["articles"])
                return httpx.Response(200, json={"media_id": "eight"}, request=request)
            raise AssertionError(request.url)

        articles = tuple(
            Article(
                title=f"文章 {index}",
                body_html=f"<p>{index}</p>",
                cover=CoverMediaId(f"cover-{index}"),
            )
            for index in range(8)
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            await client.add_draft(
                articles,
                thumb_media_id=tuple(f"cover-{index}" for index in range(8)),
            )
        finally:
            await http.aclose()

        self.assertEqual(article_count, 8)

    async def test_rejects_multi_article_cover_mismatch_before_request(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise AssertionError("invalid draft must not make requests")

        articles = (
            Article("头条", "<p>一</p>", CoverMediaId("one")),
            Article("次条", "<p>二</p>", CoverMediaId("two")),
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            with self.assertRaises(DraftValidationError):
                await client.add_draft(articles, thumb_media_id=("one",))
            with self.assertRaises(DraftValidationError):
                await client.add_draft(articles, thumb_media_id="one")
            with self.assertRaises(DraftValidationError):
                await client.add_draft(
                    articles,
                    thumb_media_id=("one", "two"),
                    open_comments=1,  # type: ignore[arg-type]
                )
        finally:
            await http.aclose()

        self.assertEqual(calls, 0)

    async def test_rejects_unhosted_image_before_draft_call(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("invalid draft must not make requests")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WechatOfficialClient(
            app_id="wx-test", app_secret="secret", http_client=http
        )
        try:
            with self.assertRaises(DraftValidationError):
                await client.add_draft(
                    Article(
                        title="标题",
                        body_html='<section><img src="https://example.com/a.png"></section>',
                        cover=CoverMediaId("cover"),
                    ),
                    thumb_media_id="cover",
                )
        finally:
            await http.aclose()


class _FakeWechat:
    def __init__(self) -> None:
        self.uploads = 0

    async def upload_content_image(self, *, filename: str, content: bytes) -> str:
        self.uploads += 1
        return "https://mmbiz.qpic.cn/new/uploaded.png"


class MediaPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_and_rewrites_only_media_urls(self) -> None:
        source = "https://images.example/a.png"

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"image",
                headers={"content-type": "image/png"},
                request=request,
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fake = _FakeWechat()
        publisher = MediaPublisher(
            fake,  # type: ignore[arg-type]
            http_client=http,
            allowed_source_hosts=("images.example",),
        )
        try:
            result = await publisher.publish_body_images(
                f'<section><img src="{source}"><img src="{source}"><a href="{source}">link</a></section>'
            )
        finally:
            await http.aclose()

        self.assertEqual(fake.uploads, 1)
        self.assertEqual(
            result.body_html.count("https://mmbiz.qpic.cn/new/uploaded.png"), 2
        )
        self.assertIn(f'href="{source}"', result.body_html)


if __name__ == "__main__":
    unittest.main()
