"""High-level service that submits one or more articles as a WeChat draft."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .client import WechatOfficialClient
from .errors import DraftValidationError, MediaUploadError
from .media import MediaPublisher
from .models import (
    Article,
    CoverFile,
    CoverMediaId,
    DraftReceipt,
    _draft_content_fingerprint,
    _normalize_draft_articles,
)


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
        article: Article | Sequence[Article],
        *,
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        articles = _normalize_draft_articles(article)
        if not isinstance(open_comments, bool) or not isinstance(
            fans_only_comments, bool
        ):
            raise DraftValidationError(
                "comment options must be bool",
                stage="draft-validation",
            )
        if fans_only_comments and not open_comments:
            raise DraftValidationError(
                "fans_only_comments requires open_comments",
                stage="draft-validation",
            )

        covers: list[tuple[str | None, bytes | None, str]] = []
        for item in articles:
            if isinstance(item.cover, CoverMediaId):
                covers.append((None, None, item.cover.media_id))
                continue
            if not isinstance(item.cover, CoverFile):
                raise DraftValidationError(
                    "article cover must be CoverFile or CoverMediaId",
                    stage="cover-validation",
                )
            cover = item.cover.path
            try:
                cover_bytes = cover.read_bytes()
            except OSError as exc:
                raise MediaUploadError(
                    "cover file could not be read", stage="cover-read"
                ) from exc
            covers.append((cover.name, cover_bytes, ""))

        submitted: list[Article] = []
        thumb_media_ids: list[str] = []
        for item, (cover_name, cover_bytes, existing_media_id) in zip(
            articles, covers, strict=True
        ):
            published = await self._media.publish_body_images(item.body_html)
            thumb_media_id = existing_media_id
            if cover_bytes is not None:
                assert cover_name is not None
                thumb_media_id = await self._client.upload_cover(
                    filename=cover_name,
                    content=cover_bytes,
                )
            thumb_media_ids.append(thumb_media_id)
            submitted.append(
                Article(
                    title=item.title,
                    body_html=published.body_html,
                    cover=CoverMediaId(thumb_media_id),
                    author=item.author,
                    digest=item.digest,
                    source_url=item.source_url,
                )
            )

        submitted_input: Article | Sequence[Article]
        thumb_input: str | Sequence[str]
        if len(submitted) == 1:
            submitted_input = submitted[0]
            thumb_input = thumb_media_ids[0]
        else:
            submitted_input = tuple(submitted)
            thumb_input = tuple(thumb_media_ids)
        receipt = await self._client.add_draft(
            submitted_input,
            thumb_media_id=thumb_input,
            open_comments=open_comments,
            fans_only_comments=fans_only_comments,
        )
        return DraftReceipt(
            media_id=receipt.media_id,
            content_fingerprint=_draft_content_fingerprint(articles),
            created_at=receipt.created_at,
        )
