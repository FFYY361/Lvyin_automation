"""Small seam service connecting rendered HTML to a WeChat draft."""

from __future__ import annotations

from pathlib import Path

from .client import WechatOfficialClient
from .errors import MediaUploadError
from .media import MediaPublisher
from .models import DraftArticle, DraftReceipt, RenderedArticle


class DraftService:
    def __init__(
        self,
        client: WechatOfficialClient,
        media_publisher: MediaPublisher,
    ) -> None:
        self._client = client
        self._media = media_publisher

    async def create_draft(
        self,
        rendered: RenderedArticle,
        *,
        cover_path: str | Path | None = None,
        cover_media_id: str | None = None,
        author: str = "",
        digest: str = "",
        source_url: str = "",
        open_comments: bool = False,
        fans_only_comments: bool = False,
    ) -> DraftReceipt:
        if bool(cover_path) == bool(cover_media_id):
            raise MediaUploadError(
                "provide exactly one of cover_path or cover_media_id",
                stage="cover-validation",
            )
        published = await self._media.publish_body_images(rendered.body_html)
        if cover_media_id:
            thumb_media_id = cover_media_id.strip()
        else:
            assert cover_path is not None
            cover = Path(cover_path)
            try:
                cover_bytes = cover.read_bytes()
            except OSError as exc:
                raise MediaUploadError(
                    "cover file could not be read", stage="cover-read"
                ) from exc
            thumb_media_id = await self._client.upload_cover(
                filename=cover.name, content=cover_bytes
            )
        return await self._client.add_draft(
            DraftArticle(
                title=rendered.title,
                body_html=published.body_html,
                author=author,
                digest=digest,
                source_url=source_url,
                open_comments=open_comments,
                fans_only_comments=fans_only_comments,
            ),
            thumb_media_id=thumb_media_id,
        )
