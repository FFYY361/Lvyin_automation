"""High-level service that submits a complete article as a WeChat draft."""

from __future__ import annotations

from pathlib import Path

from .client import WechatOfficialClient
from .errors import DraftValidationError, MediaUploadError
from .media import MediaPublisher
from .models import Article, CoverFile, CoverMediaId, DraftReceipt


class WechatOfficialService:
    def __init__(
        self,
        client: WechatOfficialClient,
        media_publisher: MediaPublisher | None = None,
        *,
        close_client: bool = False,
    ) -> None:
        self._client = client
        self._media = media_publisher or MediaPublisher(client)
        self._close_media = media_publisher is None
        self._close_client = close_client

    @classmethod
    def from_environment(
        cls,
        *,
        base_url: str = "https://api.weixin.qq.com",
        env_path: str | Path = ".env",
    ) -> "WechatOfficialService":
        client = WechatOfficialClient.from_environment(
            base_url=base_url,
            env_path=env_path,
        )
        return cls(client, close_client=True)

    async def __aenter__(self) -> "WechatOfficialService":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._close_media:
            await self._media.aclose()
        if self._close_client:
            await self._client.aclose()

    async def create_draft(
        self,
        article: Article,
        *,
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        if not isinstance(open_comments, bool) or not isinstance(fans_only_comments, bool):
            raise DraftValidationError(
                "comment options must be bool",
                stage="draft-validation",
            )
        if fans_only_comments and not open_comments:
            raise DraftValidationError(
                "fans_only_comments requires open_comments",
                stage="draft-validation",
            )

        cover_bytes: bytes | None = None
        cover_name: str | None = None
        if isinstance(article.cover, CoverMediaId):
            thumb_media_id = article.cover.media_id
        elif isinstance(article.cover, CoverFile):
            cover = article.cover.path
            try:
                cover_bytes = cover.read_bytes()
            except OSError as exc:
                raise MediaUploadError(
                    "cover file could not be read", stage="cover-read"
                ) from exc
            cover_name = cover.name
            thumb_media_id = ""
        else:
            raise DraftValidationError(
                "article cover must be CoverFile or CoverMediaId",
                stage="cover-validation",
            )

        published = await self._media.publish_body_images(article.body_html)
        if cover_bytes is not None:
            assert cover_name is not None
            thumb_media_id = await self._client.upload_cover(
                filename=cover_name,
                content=cover_bytes,
            )

        submitted = Article(
            title=article.title,
            body_html=published.body_html,
            cover=CoverMediaId(thumb_media_id),
            author=article.author,
            digest=article.digest,
            source_url=article.source_url,
        )
        receipt = await self._client.add_draft(
            submitted,
            thumb_media_id=thumb_media_id,
            open_comments=open_comments,
            fans_only_comments=fans_only_comments,
        )
        return DraftReceipt(
            media_id=receipt.media_id,
            content_fingerprint=article.content_fingerprint,
            created_at=receipt.created_at,
        )
