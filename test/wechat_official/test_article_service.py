from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from wechat_official import (
    Article,
    CoverFile,
    CoverMediaId,
    DraftReceipt,
    DraftValidationError,
    MediaPublishResult,
    MediaUploadError,
    WechatOfficialService,
)
from wechat_official.cli import main as cli_main


class ArticleBundleTests(unittest.TestCase):
    def test_media_id_bundle_round_trips_utf8(self) -> None:
        article = Article(
            title="周六前瞻",
            body_html="<section><p>中文正文</p></section>",
            cover=CoverMediaId("existing-cover"),
            author="清华绿茵",
            digest="比赛摘要",
            source_url="https://example.test/source",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = article.save(Path(directory) / "article")
            manifest = json.loads((output / "article.json").read_text(encoding="utf-8"))
            loaded = Article.load(output)

        self.assertEqual(
            manifest["cover"], {"kind": "media_id", "media_id": "existing-cover"}
        )
        self.assertEqual(loaded, article)
        self.assertEqual(loaded.content_fingerprint, article.content_fingerprint)

    def test_local_cover_is_copied_into_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_cover = root / "source.png"
            source_cover.write_bytes(b"png")
            article = Article(
                title="标题",
                body_html="<section>正文</section>",
                cover=CoverFile(source_cover),
            )
            output = article.save(root / "article")
            loaded = Article.load(output)

            self.assertEqual((output / "cover.png").read_bytes(), b"png")
            self.assertIsInstance(loaded.cover, CoverFile)
            self.assertEqual(loaded.cover.path, (output / "cover.png").resolve())

    def test_rejects_unknown_field_path_escape_and_fingerprint_damage(self) -> None:
        article = Article(
            title="标题",
            body_html="<section>正文</section>",
            cover=CoverMediaId("cover"),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = article.save(Path(directory) / "article")
            manifest_path = output / "article.json"
            original = json.loads(manifest_path.read_text(encoding="utf-8"))

            damaged = dict(original)
            damaged["unknown"] = True
            manifest_path.write_text(json.dumps(damaged), encoding="utf-8")
            with self.assertRaises(DraftValidationError):
                Article.load(output)

            escaped = dict(original)
            escaped["body_file"] = "../body.html"
            manifest_path.write_text(json.dumps(escaped), encoding="utf-8")
            with self.assertRaises(DraftValidationError):
                Article.load(output)

            manifest_path.write_text(json.dumps(original), encoding="utf-8")
            (output / "body.html").write_text("changed", encoding="utf-8")
            with self.assertRaises(DraftValidationError):
                Article.load(output)

    def test_requires_title_body_and_valid_cover(self) -> None:
        with self.assertRaises(DraftValidationError):
            Article(title="", body_html="<p>x</p>", cover=CoverMediaId("cover"))
        with self.assertRaises(DraftValidationError):
            Article(title="x", body_html="", cover=CoverMediaId("cover"))
        with self.assertRaises(DraftValidationError):
            CoverMediaId(" ")


class _FakeMedia:
    def __init__(self) -> None:
        self.calls = 0

    async def publish_body_images(self, body_html: str) -> MediaPublishResult:
        self.calls += 1
        return MediaPublishResult(
            body_html=body_html.replace("old", "new"), replacements={}
        )


class _FakeClient:
    def __init__(self) -> None:
        self.cover_uploads = 0
        self.submitted: Article | Sequence[Article] | None = None
        self.thumb_media_id: str | Sequence[str] | None = None
        self.comment_options: tuple[bool, bool] | None = None
        self.add_calls = 0

    async def upload_cover(self, *, filename: str, content: bytes) -> str:
        self.cover_uploads += 1
        return "uploaded-cover"

    async def add_draft(
        self,
        article: Article | Sequence[Article],
        *,
        thumb_media_id: str | Sequence[str],
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        self.add_calls += 1
        self.submitted = article
        self.thumb_media_id = thumb_media_id
        self.comment_options = (open_comments, fans_only_comments)
        return DraftReceipt(
            media_id="draft-id",
            content_fingerprint="client-fingerprint",
            created_at=datetime(2026, 7, 15, tzinfo=UTC),
        )


class WechatOfficialServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_submits_complete_article_and_preserves_source_fingerprint(
        self,
    ) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        article = Article(
            title="标题",
            body_html="<section>old</section>",
            cover=CoverMediaId("existing-cover"),
        )
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]

        receipt = await service.create_draft(
            article,
            open_comments=True,
            fans_only_comments=True,
        )

        self.assertEqual(receipt.content_fingerprint, article.content_fingerprint)
        self.assertEqual(client.submitted.body_html, "<section>new</section>")
        self.assertEqual(client.comment_options, (True, True))
        self.assertEqual(client.cover_uploads, 0)
        self.assertEqual(client.add_calls, 1)

    async def test_uploads_local_cover_from_article(self) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / "cover.png"
            cover.write_bytes(b"png")
            article = Article(
                "标题",
                "<section>正文</section>",
                CoverFile(cover),
            )
            receipt = await service.create_draft(article)

        self.assertEqual(receipt.media_id, "draft-id")
        self.assertEqual(client.cover_uploads, 1)
        self.assertIsInstance(client.submitted.cover, CoverMediaId)
        self.assertEqual(client.submitted.cover.media_id, "uploaded-cover")

    async def test_creates_one_ordered_multi_article_draft(self) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as directory:
            cover = Path(directory) / "second.png"
            cover.write_bytes(b"png")
            headline = Article(
                "头条",
                "<section>old-headline</section>",
                CoverMediaId("headline-cover"),
            )
            second = Article(
                "次条",
                "<section>old-second</section>",
                CoverFile(cover),
            )
            receipt = await service.create_draft(
                [headline, second],
                open_comments=True,
                fans_only_comments=True,
            )

        self.assertEqual(client.add_calls, 1)
        self.assertEqual(media.calls, 2)
        self.assertEqual(client.cover_uploads, 1)
        self.assertIsInstance(client.submitted, tuple)
        submitted = client.submitted
        assert isinstance(submitted, tuple)
        self.assertEqual([item.title for item in submitted], ["头条", "次条"])
        self.assertEqual(
            [item.body_html for item in submitted],
            ["<section>new-headline</section>", "<section>new-second</section>"],
        )
        self.assertEqual(
            client.thumb_media_id,
            ("headline-cover", "uploaded-cover"),
        )
        self.assertEqual(client.comment_options, (True, True))
        self.assertNotEqual(receipt.content_fingerprint, headline.content_fingerprint)

        reversed_client = _FakeClient()
        reversed_service = WechatOfficialService(  # type: ignore[arg-type]
            reversed_client, _FakeMedia()
        )
        second_with_existing_cover = Article(
            second.title,
            second.body_html,
            CoverMediaId("uploaded-cover"),
            author=second.author,
            digest=second.digest,
            source_url=second.source_url,
        )
        reversed_receipt = await reversed_service.create_draft(
            [second_with_existing_cover, headline]
        )
        self.assertNotEqual(
            receipt.content_fingerprint, reversed_receipt.content_fingerprint
        )
        repeated_receipt = await reversed_service.create_draft(
            [headline, second_with_existing_cover]
        )
        self.assertEqual(
            receipt.content_fingerprint, repeated_receipt.content_fingerprint
        )

    async def test_reads_all_local_covers_before_media_uploads(self) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]
        first = Article(
            "头条", "<section>正文</section>", CoverMediaId("existing-cover")
        )
        with tempfile.TemporaryDirectory() as directory:
            missing = Article(
                "次条",
                "<section>正文</section>",
                CoverFile(Path(directory) / "missing-cover.png"),
            )
            with self.assertRaises(MediaUploadError):
                await service.create_draft([first, missing])

        self.assertEqual(media.calls, 0)
        self.assertEqual(client.cover_uploads, 0)
        self.assertEqual(client.add_calls, 0)

    async def test_rejects_invalid_article_groups_before_io(self) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]
        article = Article("标题", "<section>正文</section>", CoverMediaId("cover"))

        invalid_groups: list[object] = [
            [],
            [article] * 9,
            [article, object()],
        ]
        for group in invalid_groups:
            with self.subTest(group_length=len(group)):  # type: ignore[arg-type]
                with self.assertRaises(DraftValidationError):
                    await service.create_draft(group)  # type: ignore[arg-type]

        self.assertEqual(media.calls, 0)
        self.assertEqual(client.cover_uploads, 0)
        self.assertEqual(client.add_calls, 0)

    async def test_rejects_fans_only_without_opening_comments_before_io(self) -> None:
        client = _FakeClient()
        media = _FakeMedia()
        service = WechatOfficialService(client, media)  # type: ignore[arg-type]
        article = Article("标题", "<section>正文</section>", CoverMediaId("cover"))

        with self.assertRaises(DraftValidationError):
            await service.create_draft(article, fans_only_comments=True)
        self.assertEqual(media.calls, 0)


class WechatOfficialCliTests(unittest.TestCase):
    def test_create_draft_defaults_to_local_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            article = Article(
                title="标题",
                body_html="<section>正文</section>",
                cover=CoverMediaId("cover"),
            )
            output = article.save(Path(directory) / "article")
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = cli_main(["create-draft", str(output)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "dry-run")
        self.assertEqual(payload["article_count"], 1)
        self.assertEqual(payload["titles"], [article.title])
        self.assertEqual(payload["content_fingerprint"], article.content_fingerprint)
        self.assertFalse(payload["external_writes"])

    def test_create_multi_article_draft_dry_run_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            headline = Article("头条", "<section>一</section>", CoverMediaId("cover-1"))
            second = Article("次条", "<section>二</section>", CoverMediaId("cover-2"))
            outputs = [
                headline.save(root / "headline"),
                second.save(root / "second"),
            ]
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = cli_main(
                    ["create-draft", *(str(output) for output in outputs)]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["article_count"], 2)
        self.assertEqual(payload["title"], "头条")
        self.assertEqual(payload["titles"], ["头条", "次条"])
        self.assertEqual(payload["covers"], ["media_id", "media_id"])
        self.assertEqual(
            payload["content_fingerprints"],
            [headline.content_fingerprint, second.content_fingerprint],
        )
        self.assertNotEqual(
            payload["content_fingerprint"], headline.content_fingerprint
        )

    def test_create_multi_article_draft_execute_reports_group_metadata(self) -> None:
        class FakeService:
            submitted: Article | Sequence[Article] | None = None

            async def __aenter__(self) -> "FakeService":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def create_draft(
                self,
                article: Article | Sequence[Article],
                *,
                open_comments: bool = False,
                fans_only_comments: bool = False,
            ) -> DraftReceipt:
                self.submitted = article
                return DraftReceipt(
                    media_id="multi-draft",
                    content_fingerprint="group-fingerprint",
                    created_at=datetime(2026, 7, 21, tzinfo=UTC),
                )

        fake_service = FakeService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = [
                Article("头条", "<p>一</p>", CoverMediaId("one")).save(root / "one"),
                Article("次条", "<p>二</p>", CoverMediaId("two")).save(root / "two"),
            ]
            stdout = StringIO()
            with (
                patch(
                    "wechat_official.cli.WechatOfficialService.from_environment",
                    return_value=fake_service,
                ),
                redirect_stdout(stdout),
            ):
                status = cli_main(
                    [
                        "create-draft",
                        *(str(output) for output in outputs),
                        "--execute",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["draft_media_id"], "multi-draft")
        self.assertEqual(payload["content_fingerprint"], "group-fingerprint")
        self.assertEqual(payload["article_count"], 2)
        self.assertEqual(payload["titles"], ["头条", "次条"])
        self.assertIsInstance(fake_service.submitted, tuple)

    def test_dry_run_rejects_invalid_comment_combination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Article(
                title="标题",
                body_html="<section>正文</section>",
                cover=CoverMediaId("cover"),
            ).save(Path(directory) / "article")
            with redirect_stderr(StringIO()):
                status = cli_main(["create-draft", str(output), "--fans-only-comments"])
        self.assertEqual(status, 2)

    def test_official_package_does_not_import_preview(self) -> None:
        package = _SRC_ROOT / "wechat_official"
        sources = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        )
        self.assertNotIn("from preview", sources)
        self.assertNotIn("import preview", sources)


if __name__ == "__main__":
    unittest.main()
