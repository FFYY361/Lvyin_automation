"""Immutable domain models for article extraction, rendering and drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Mapping


@dataclass(frozen=True)
class MediaReference:
    url: str
    kind: Literal["image", "background"]
    location: str


@dataclass(frozen=True)
class ArticleSource:
    title: str
    author: str | None
    source_url: str
    body_html: str
    media: tuple[MediaReference, ...]
    content_fingerprint: str


@dataclass(frozen=True)
class RenderedArticle:
    title: str
    body_html: str
    template_version: str
    content_fingerprint: str
    media: tuple[MediaReference, ...]


@dataclass(frozen=True)
class DraftArticle:
    title: str
    body_html: str
    author: str = ""
    digest: str = ""
    source_url: str = ""
    open_comments: bool = False
    fans_only_comments: bool = False


@dataclass(frozen=True)
class DraftReceipt:
    media_id: str
    content_fingerprint: str
    created_at: datetime


@dataclass(frozen=True)
class MediaPublishResult:
    body_html: str
    replacements: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "replacements", MappingProxyType(dict(self.replacements)))
