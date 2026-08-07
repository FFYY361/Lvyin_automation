"""High-level, pure-local service for rendering a complete article."""

from __future__ import annotations

from pathlib import Path

from wechat_official import Article, CoverFile, CoverMediaId

from .models import PreviewSourceData
from .template import PreviewTemplate, load_preview_template


class PreviewService:
    def __init__(self, template: PreviewTemplate) -> None:
        self._template = template

    @property
    def template_version(self) -> str:
        return self._template.version

    @property
    def template_fingerprint(self) -> str:
        return self._template.fingerprint

    @classmethod
    def from_template(cls, path: str | Path) -> "PreviewService":
        return cls(load_preview_template(path))

    def render(
        self,
        source: PreviewSourceData,
        *,
        cover: CoverFile | CoverMediaId,
        author: str = "",
        digest: str = "",
        source_url: str = "",
    ) -> Article:
        title, body_html = self._template.render_body(source)
        return Article(
            title=title,
            body_html=body_html,
            cover=cover,
            author=author,
            digest=digest,
            source_url=source_url,
        )
