"""Public article contract and immutable WeChat result models."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .errors import DraftValidationError

ARTICLE_SCHEMA_VERSION = 1
_MANIFEST_NAME = "article.json"
_BODY_NAME = "body.html"
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "title",
        "author",
        "digest",
        "source_url",
        "body_file",
        "cover",
        "content_fingerprint",
    }
)


def _validation_error(
    message: str, *, stage: str = "article-validation"
) -> DraftValidationError:
    return DraftValidationError(message, stage=stage)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation_error(f"{name} must be a non-empty string")
    return value


def _optional_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise _validation_error(f"{name} must be a string")
    return value


def _safe_bundle_file(root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise _validation_error(
            f"{name} must be a non-empty relative path", stage="article-load"
        )
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise _validation_error(
            f"{name} must stay inside the article directory", stage="article-load"
        )
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _validation_error(
            f"{name} must stay inside the article directory", stage="article-load"
        ) from exc
    return resolved


@dataclass(frozen=True, slots=True)
class CoverFile:
    """A local image that will be uploaded as permanent cover material."""

    path: Path

    def __post_init__(self) -> None:
        try:
            path = Path(self.path)
        except TypeError as exc:
            raise _validation_error("cover file path is invalid") from exc
        if not str(path).strip():
            raise _validation_error("cover file path is required")
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class CoverMediaId:
    """An existing permanent cover material in the official account."""

    media_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.media_id, str) or not self.media_id.strip():
            raise _validation_error("cover media_id is required")
        object.__setattr__(self, "media_id", self.media_id.strip())


Cover = CoverFile | CoverMediaId


def _write_article_body(root: Path, body_html: str) -> None:
    try:
        (root / _BODY_NAME).write_text(body_html, encoding="utf-8")
    except OSError as exc:
        raise _validation_error(
            "article body could not be written",
            stage="article-save",
        ) from exc


def _persist_cover(root: Path, cover: Cover) -> dict[str, str]:
    if isinstance(cover, CoverFile):
        source = cover.path
        if not source.is_file():
            raise _validation_error(
                f"cover file does not exist: {source}",
                stage="article-save",
            )
        cover_name = "cover" + source.suffix.lower()
        cover_path = root / cover_name
        try:
            if source.resolve() != cover_path.resolve():
                shutil.copyfile(source, cover_path)
        except OSError as exc:
            raise _validation_error(
                "cover file could not be copied",
                stage="article-save",
            ) from exc
        return {"kind": "file", "path": cover_name}
    return {"kind": "media_id", "media_id": cover.media_id}


def _read_manifest(root: Path) -> Mapping[str, object]:
    try:
        payload = json.loads((root / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except OSError as exc:
        raise _validation_error(
            "article manifest could not be read",
            stage="article-load",
        ) from exc
    except json.JSONDecodeError as exc:
        raise _validation_error(
            "article manifest is not valid JSON",
            stage="article-load",
        ) from exc
    if not isinstance(payload, Mapping):
        raise _validation_error(
            "article manifest must be a JSON object",
            stage="article-load",
        )
    unknown = sorted(str(key) for key in payload if key not in _MANIFEST_FIELDS)
    if unknown:
        raise _validation_error(
            "article manifest contains unknown field(s): " + ", ".join(unknown),
            stage="article-load",
        )
    missing = sorted(field for field in _MANIFEST_FIELDS if field not in payload)
    if missing:
        raise _validation_error(
            "article manifest is missing field(s): " + ", ".join(missing),
            stage="article-load",
        )
    version = payload.get("schema_version")
    if isinstance(version, bool) or version != ARTICLE_SCHEMA_VERSION:
        raise _validation_error(
            f"unsupported article schema version: {version}",
            stage="article-load",
        )
    return payload


def _read_article_body(root: Path, value: object) -> str:
    body_path = _safe_bundle_file(root, value, "body_file")
    if body_path.name != _BODY_NAME:
        raise _validation_error("body_file must be body.html", stage="article-load")
    try:
        return body_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _validation_error(
            "article body could not be read",
            stage="article-load",
        ) from exc


def _load_cover(root: Path, value: object) -> Cover:
    if not isinstance(value, Mapping):
        raise _validation_error(
            "article cover must be a JSON object",
            stage="article-load",
        )
    kind = value.get("kind")
    if kind == "file" and set(value) == {"kind", "path"}:
        cover_path = _safe_bundle_file(root, value.get("path"), "cover.path")
        if not cover_path.is_file():
            raise _validation_error(
                "article cover file does not exist",
                stage="article-load",
            )
        return CoverFile(cover_path)
    if kind == "media_id" and set(value) == {"kind", "media_id"}:
        return CoverMediaId(value.get("media_id"))  # type: ignore[arg-type]
    raise _validation_error("article cover has an invalid shape", stage="article-load")


@dataclass(frozen=True, slots=True)
class Article:
    """A complete article ready to validate, persist or submit as a draft."""

    title: str
    body_html: str
    cover: Cover
    author: str = ""
    digest: str = ""
    source_url: str = ""

    def __post_init__(self) -> None:
        _required_text(self.title, "article title")
        _required_text(self.body_html, "article body_html")
        if not isinstance(self.cover, (CoverFile, CoverMediaId)):
            raise _validation_error("article cover must be CoverFile or CoverMediaId")
        _optional_text(self.author, "article author")
        _optional_text(self.digest, "article digest")
        _optional_text(self.source_url, "article source_url")

    @property
    def content_fingerprint(self) -> str:
        serialised = json.dumps(
            {
                "title": self.title,
                "body_html": self.body_html,
                "author": self.author,
                "digest": self.digest,
                "source_url": self.source_url,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialised).hexdigest()

    def save(self, directory: str | Path) -> Path:
        """Write a readable, versioned article bundle and return its directory."""

        root = Path(directory)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _validation_error(
                f"article directory could not be created: {root}", stage="article-save"
            ) from exc
        if not root.is_dir():
            raise _validation_error(
                f"article output is not a directory: {root}", stage="article-save"
            )

        _write_article_body(root, self.body_html)
        cover_payload = _persist_cover(root, self.cover)

        payload = {
            "schema_version": ARTICLE_SCHEMA_VERSION,
            "title": self.title,
            "author": self.author,
            "digest": self.digest,
            "source_url": self.source_url,
            "body_file": _BODY_NAME,
            "cover": cover_payload,
            "content_fingerprint": self.content_fingerprint,
        }
        try:
            (root / _MANIFEST_NAME).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise _validation_error(
                "article manifest could not be written", stage="article-save"
            ) from exc
        return root

    @classmethod
    def load(cls, directory: str | Path) -> "Article":
        """Load and verify an article bundle created by :meth:`save`."""

        root = Path(directory).resolve()
        if not root.is_dir():
            raise _validation_error(
                f"article directory does not exist: {root}", stage="article-load"
            )
        payload = _read_manifest(root)
        body_html = _read_article_body(root, payload.get("body_file"))
        cover = _load_cover(root, payload.get("cover"))

        article = cls(
            title=_required_text(payload.get("title"), "article title"),
            body_html=body_html,
            cover=cover,
            author=_optional_text(payload.get("author"), "article author"),
            digest=_optional_text(payload.get("digest"), "article digest"),
            source_url=_optional_text(payload.get("source_url"), "article source_url"),
        )
        fingerprint = payload.get("content_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or fingerprint != article.content_fingerprint
        ):
            raise _validation_error(
                "article content fingerprint does not match", stage="article-load"
            )
        return article


@dataclass(frozen=True, slots=True)
class MediaReference:
    url: str
    kind: Literal["image", "background"]
    location: str


@dataclass(frozen=True, slots=True)
class DraftReceipt:
    media_id: str
    content_fingerprint: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MediaPublishResult:
    body_html: str
    replacements: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "replacements", MappingProxyType(dict(self.replacements))
        )
