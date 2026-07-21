from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

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
        self.submitted: Article | None = None
        self.comment_options: tuple[bool, bool] | None = None

    async def upload_cover(self, *, filename: str, content: bytes) -> str:
        self.cover_uploads += 1
        return "uploaded-cover"

    async def add_draft(
        self,
        article: Article,
        *,
        thumb_media_id: str,
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        self.submitted = article
        self.comment_options = (open_comments, fans_only_comments)
        return DraftReceipt(
            media_id="draft-id",
            content_fingerprint=article.content_fingerprint,
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
        self.assertFalse(payload["external_writes"])

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
